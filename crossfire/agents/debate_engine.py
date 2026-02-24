"""Debate engine — orchestrates adversarial debates on findings."""

from __future__ import annotations

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.agents.claude_adapter import ClaudeAgent
from crossfire.agents.codex_adapter import CodexAgent
from crossfire.agents.consensus import compute_consensus
from crossfire.agents.gemini_adapter import GeminiAgent
from crossfire.agents.prompts.defense_prompt import (
    DEFENSE_SYSTEM_PROMPT,
    build_defense_prompt,
)
from crossfire.agents.prompts.judge_prompt import (
    JUDGE_SYSTEM_PROMPT,
    build_judge_prompt,
)
from crossfire.agents.prompts.prosecutor_prompt import (
    PROSECUTOR_SYSTEM_PROMPT,
    build_prosecutor_prompt,
)
from crossfire.config.settings import CrossFireSettings
from crossfire.core.models import (
    AgentArgument,
    CitedEvidence,
    ConsensusOutcome,
    DebateRecord,
    Finding,
    FindingStatus,
    IntentProfile,
    PRContext,
    Severity,
)

logger = structlog.get_logger()

AGENT_CLASSES: dict[str, type[BaseAgent]] = {
    "claude": ClaudeAgent,
    "codex": CodexAgent,
    "gemini": GeminiAgent,
}

ROLE_CYCLE = ["claude", "codex", "gemini"]


def _format_finding_summary(finding: Finding) -> str:
    """Format a finding into a readable summary for debate prompts."""
    parts = [
        f"**{finding.title}**",
        f"Category: {finding.category.value}",
        f"Severity: {finding.severity.value} | Confidence: {finding.confidence:.2f}",
        f"Affected files: {', '.join(finding.affected_files)}",
    ]
    if finding.data_flow_trace:
        parts.append(f"Data flow: {finding.data_flow_trace}")
    if finding.rationale_summary:
        parts.append(f"Rationale: {finding.rationale_summary}")
    return "\n".join(parts)


def _format_evidence_text(finding: Finding) -> str:
    """Format evidence from a finding for debate prompts."""
    parts: list[str] = []
    for ev in finding.evidence:
        parts.append(f"- [{ev.evidence_type}] {ev.description}")
        if ev.file_path:
            parts.append(f"  File: {ev.file_path}")
        if ev.code_snippet:
            parts.append(f"  Code: {ev.code_snippet}")
    return "\n".join(parts) if parts else "No evidence collected."


def _format_intent_summary(intent: IntentProfile) -> str:
    """Format intent profile for debate prompts."""
    parts = [
        f"Repository purpose: {intent.repo_purpose}",
        f"Intended capabilities: {', '.join(intent.intended_capabilities) or 'none specified'}",
        f"PR intent: {intent.pr_intent}",
    ]
    if intent.trust_boundaries:
        parts.append("Trust boundaries:")
        for tb in intent.trust_boundaries:
            parts.append(f"  - {tb.name}: {tb.description}")
    if intent.security_controls_detected:
        parts.append("Security controls:")
        for ctrl in intent.security_controls_detected:
            parts.append(f"  - {ctrl.control_type}: {ctrl.description}")
    return "\n".join(parts)


def _format_context_summary(context: PRContext) -> str:
    """Format PR context for debate prompts (abbreviated)."""
    parts = [
        f"Repo: {context.repo_name}",
        f"PR: {context.pr_title}",
        f"Files changed: {len(context.files)}",
    ]
    return "\n".join(parts)


def _parse_agent_argument(
    raw_response: str,
    agent: BaseAgent,
    role: str,
) -> AgentArgument:
    """Parse an agent's debate response into an AgentArgument."""
    try:
        parsed = agent.parse_json_response(raw_response)
    except AgentError:
        # If JSON parsing fails, create a basic argument from raw text
        return AgentArgument(
            agent_name=agent.name,
            role=role,
            position="unclear",
            argument=raw_response[:2000],
            confidence=0.3,
        )

    # Parse cited evidence
    cited: list[CitedEvidence] = []
    for ev in parsed.get("cited_evidence", []):
        cited.append(CitedEvidence(
            file_path=ev.get("file", "unknown"),
            line_range=ev.get("lines"),
            code_snippet=ev.get("code", ""),
            explanation=ev.get("explanation", ""),
        ))

    return AgentArgument(
        agent_name=agent.name,
        role=role,
        position=parsed.get("position", parsed.get("ruling", "unclear")),
        argument=parsed.get("argument", parsed.get("reasoning", "")),
        cited_evidence=cited,
        confidence=parsed.get("confidence", parsed.get("final_confidence", 0.5)),
    )


class DebateEngine:
    """Orchestrates adversarial debates on disputed findings."""

    def __init__(self, settings: CrossFireSettings) -> None:
        self.settings = settings
        self._rotation_index = 0

    async def debate_all(
        self,
        findings: list[Finding],
        context: PRContext,
        intent: IntentProfile,
    ) -> list[tuple[Finding, DebateRecord]]:
        """Run debates on all findings that need it.

        Returns list of (finding, debate_record) tuples.
        """
        results: list[tuple[Finding, DebateRecord]] = []

        for finding in findings:
            try:
                debate = await self._debate_single(finding, context, intent)
                if debate:
                    # Update finding status based on consensus
                    self._apply_debate_result(finding, debate)
                    results.append((finding, debate))
                else:
                    # Debate failed, mark as unclear
                    finding.status = FindingStatus.UNCLEAR
                    finding.debate_summary = "Debate could not be completed"
            except Exception as e:
                logger.error("debate.error", finding_id=finding.id, error=str(e))
                finding.status = FindingStatus.UNCLEAR

        return results

    async def _debate_single(
        self,
        finding: Finding,
        context: PRContext,
        intent: IntentProfile,
    ) -> DebateRecord | None:
        """Run a single debate for one finding."""
        # Assign roles
        prosecutor_name, defense_name, judge_name = self._assign_roles()

        logger.info(
            "debate.start",
            finding=finding.title,
            prosecutor=prosecutor_name,
            defense=defense_name,
            judge=judge_name,
        )

        # Create agent instances
        agents: dict[str, BaseAgent] = {}
        for name in (prosecutor_name, defense_name, judge_name):
            config = self.settings.agents.get(name)
            if not config or not config.enabled:
                logger.warning("debate.agent_unavailable", agent=name)
                continue
            cls = AGENT_CLASSES.get(name)
            if cls:
                agents[name] = cls(config)

        available_count = len(agents)
        min_required = self.settings.debate.min_agents_for_debate

        if available_count < min_required:
            logger.warning(
                "debate.insufficient_agents",
                available=available_count,
                required=min_required,
            )
            return None

        # Prepare summaries
        finding_summary = _format_finding_summary(finding)
        evidence_text = _format_evidence_text(finding)
        context_summary = _format_context_summary(context)
        intent_summary = _format_intent_summary(intent)

        # Step 1: Prosecution
        prosecutor_argument = await self._run_prosecution(
            agents.get(prosecutor_name),
            prosecutor_name,
            finding_summary,
            evidence_text,
            context_summary,
            intent_summary,
        )

        # Step 2: Defense
        defense_argument = await self._run_defense(
            agents.get(defense_name),
            defense_name,
            finding_summary,
            evidence_text,
            prosecutor_argument.argument if prosecutor_argument else "",
            context_summary,
            intent_summary,
        )

        # Step 3: Rebuttal (optional)
        rebuttal_argument = None
        if self.settings.debate.enable_rebuttal and agents.get(prosecutor_name):
            rebuttal_argument = await self._run_rebuttal(
                agents[prosecutor_name],
                prosecutor_name,
                finding_summary,
                defense_argument.argument if defense_argument else "",
            )

        # Step 4: Judge
        judge_argument, judge_raw_response = await self._run_judge(
            agents.get(judge_name),
            judge_name,
            finding_summary,
            prosecutor_argument.argument if prosecutor_argument else "",
            defense_argument.argument if defense_argument else "",
            rebuttal_argument.argument if rebuttal_argument else None,
            intent_summary,
        )

        # Build debate record
        if not prosecutor_argument or not defense_argument or not judge_argument:
            return None

        # Parse severity from judge's raw response (not the extracted argument text)
        final_severity = finding.severity
        if judge_raw_response and judge_name in agents:
            try:
                parsed_judge = agents[judge_name].parse_json_response(judge_raw_response)
                if isinstance(parsed_judge, dict):
                    sev_str = parsed_judge.get("final_severity", finding.severity.value)
                    final_severity = Severity(sev_str)
            except (ValueError, AgentError):
                pass

        debate = DebateRecord(
            finding_id=finding.id,
            prosecutor_argument=prosecutor_argument,
            defense_argument=defense_argument,
            judge_ruling=judge_argument,
            rebuttal=rebuttal_argument,
            final_severity=final_severity,
            final_confidence=judge_argument.confidence,
        )

        # Compute consensus
        compute_consensus(debate, intent)

        logger.info(
            "debate.complete",
            finding=finding.title,
            consensus=debate.consensus.value,
        )

        return debate

    def _assign_roles(self) -> tuple[str, str, str]:
        """Assign roles based on config (rotate or fixed)."""
        if self.settings.debate.role_assignment == "fixed":
            roles = self.settings.debate.fixed_roles
            return roles["prosecutor"], roles["defense"], roles["judge"]

        # Rotate roles
        agents = [name for name, cfg in self.settings.agents.items() if cfg.enabled]
        if len(agents) < 3:
            # Pad with available agents
            while len(agents) < 3:
                agents.append(agents[0])

        offset = self._rotation_index % len(agents)
        self._rotation_index += 1

        return agents[offset % len(agents)], agents[(offset + 1) % len(agents)], agents[(offset + 2) % len(agents)]

    async def _run_prosecution(
        self,
        agent: BaseAgent | None,
        agent_name: str,
        finding_summary: str,
        evidence_text: str,
        context_summary: str,
        intent_summary: str,
    ) -> AgentArgument | None:
        """Run the prosecution phase."""
        if not agent:
            return AgentArgument(
                agent_name=agent_name, role="prosecutor",
                position="unclear", argument="Agent unavailable", confidence=0.0,
            )

        prompt = build_prosecutor_prompt(finding_summary, evidence_text, context_summary, intent_summary)

        try:
            response = await agent.execute(prompt, PROSECUTOR_SYSTEM_PROMPT)
            return _parse_agent_argument(response, agent, "prosecutor")
        except AgentError as e:
            logger.error("debate.prosecution_failed", error=str(e))
            return None

    async def _run_defense(
        self,
        agent: BaseAgent | None,
        agent_name: str,
        finding_summary: str,
        evidence_text: str,
        prosecutor_argument: str,
        context_summary: str,
        intent_summary: str,
    ) -> AgentArgument | None:
        """Run the defense phase."""
        if not agent:
            return AgentArgument(
                agent_name=agent_name, role="defense",
                position="unclear", argument="Agent unavailable", confidence=0.0,
            )

        prompt = build_defense_prompt(
            finding_summary, evidence_text, prosecutor_argument,
            context_summary, intent_summary,
        )

        try:
            response = await agent.execute(prompt, DEFENSE_SYSTEM_PROMPT)
            return _parse_agent_argument(response, agent, "defense")
        except AgentError as e:
            logger.error("debate.defense_failed", error=str(e))
            return None

    async def _run_rebuttal(
        self,
        agent: BaseAgent,
        agent_name: str,
        finding_summary: str,
        defense_argument: str,
    ) -> AgentArgument | None:
        """Run the rebuttal phase (prosecutor responds to defense)."""
        prompt = (
            f"## Finding Under Review\n\n{finding_summary}\n\n"
            f"## Defense's Argument\n\n{defense_argument}\n\n"
            "Respond to the defense's specific claims. Address their strongest points. "
            "Cite code evidence. One round only."
        )

        try:
            response = await agent.execute(prompt, PROSECUTOR_SYSTEM_PROMPT)
            return _parse_agent_argument(response, agent, "prosecutor_rebuttal")
        except AgentError as e:
            logger.error("debate.rebuttal_failed", error=str(e))
            return None

    async def _run_judge(
        self,
        agent: BaseAgent | None,
        agent_name: str,
        finding_summary: str,
        prosecutor_argument: str,
        defense_argument: str,
        rebuttal_argument: str | None,
        intent_summary: str,
    ) -> tuple[AgentArgument | None, str]:
        """Run the judge phase.

        Returns (argument, raw_response) so the caller can parse
        structured fields like final_severity from the original JSON.
        """
        if not agent:
            return AgentArgument(
                agent_name=agent_name, role="judge",
                position="unclear", argument="Agent unavailable", confidence=0.0,
            ), ""

        prompt = build_judge_prompt(
            finding_summary, prosecutor_argument, defense_argument,
            rebuttal_argument, intent_summary,
        )

        try:
            response = await agent.execute(prompt, JUDGE_SYSTEM_PROMPT)
            return _parse_agent_argument(response, agent, "judge"), response
        except AgentError as e:
            logger.error("debate.judge_failed", error=str(e))
            return None, ""

    def _apply_debate_result(self, finding: Finding, debate: DebateRecord) -> None:
        """Update finding based on debate outcome."""
        consensus_map = {
            ConsensusOutcome.CONFIRMED: FindingStatus.CONFIRMED,
            ConsensusOutcome.LIKELY: FindingStatus.LIKELY,
            ConsensusOutcome.UNCLEAR: FindingStatus.UNCLEAR,
            ConsensusOutcome.REJECTED: FindingStatus.REJECTED,
        }

        finding.status = consensus_map.get(debate.consensus, FindingStatus.UNCLEAR)
        finding.severity = debate.final_severity
        finding.confidence = debate.final_confidence
        finding.consensus_outcome = debate.consensus.value
        finding.debate_summary = (
            f"Prosecutor ({debate.prosecutor_argument.agent_name}): {debate.prosecutor_argument.position} | "
            f"Defense ({debate.defense_argument.agent_name}): {debate.defense_argument.position} | "
            f"Judge ({debate.judge_ruling.agent_name}): {debate.judge_ruling.position}"
        )
