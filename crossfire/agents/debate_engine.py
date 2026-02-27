"""Debate engine — orchestrates adversarial debates on findings."""

from __future__ import annotations

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.agents.claude_adapter import ClaudeAgent
from crossfire.agents.codex_adapter import CodexAgent
from crossfire.agents.consensus import compute_consensus
from crossfire.agents.gemini_adapter import GeminiAgent
from crossfire.agents.prompts.guardrails import wrap_agent_output, wrap_external
from crossfire.agents.prompts.defense_prompt import (
    DEFENSE_SYSTEM_PROMPT,
    build_defense_prompt,
)
from crossfire.agents.prompts.judge_prompt import (
    JUDGE_CLARIFICATION_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    build_judge_clarification_prompt,
    build_judge_final_prompt,
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
            parts.append(
                f"  Code: {wrap_external(ev.code_snippet, 'code-snippet')}"
            )
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
        debate_budget: int | None = None,
    ) -> list[tuple[Finding, DebateRecord]]:
        """Run debates on all findings that need it, respecting budget.

        Findings should arrive pre-sorted by (severity, confidence) descending
        so the budget is spent on the most important findings first.

        Returns list of (finding, debate_record) tuples.
        """
        from crossfire.core.finding_synthesizer import SEVERITY_ORDER

        # Sort highest-severity first to spend budget wisely
        sorted_findings = sorted(
            findings,
            key=lambda f: (SEVERITY_ORDER[f.severity], f.confidence),
            reverse=True,
        )

        remaining_budget = debate_budget
        results: list[tuple[Finding, DebateRecord]] = []

        for finding in sorted_findings:
            # Budget check: each debate costs 1-2 rounds
            if remaining_budget is not None and remaining_budget <= 0:
                finding.status = FindingStatus.UNCLEAR
                finding.debate_summary = "Skipped: debate budget exhausted"
                logger.info("debate.budget_exhausted", finding=finding.title)
                continue

            try:
                debate = await self._debate_single(finding, context, intent)
                if debate:
                    self._apply_debate_result(finding, debate)
                    results.append((finding, debate))
                    # Deduct rounds used from budget
                    if remaining_budget is not None:
                        remaining_budget -= debate.rounds_used
                else:
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
        """Run a single structured debate for one finding.

        Flow:
        - Round 1: Prosecutor argues → Defense responds
        - If defense concedes → Judge issues verdict (1 round)
        - If defense disagrees → Round 2: Judge asks targeted questions,
          both sides respond, judge makes final ruling (2 rounds)
        - 2-agent mode (no judge): defense concedes → Confirmed,
          defense disagrees → Unclear
        """
        # Assign roles (evidence-driven: finder prosecutes, misser defends)
        prosecutor_name, defense_name, judge_name = self._assign_roles(finding)
        has_judge = prosecutor_name != defense_name and judge_name != defense_name

        logger.info(
            "debate.start",
            finding=finding.title,
            prosecutor=prosecutor_name,
            defense=defense_name,
            judge=judge_name,
            has_judge=has_judge,
        )

        # Create agent instances
        agents: dict[str, BaseAgent] = {}
        for name in {prosecutor_name, defense_name, judge_name}:
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

        # ── Round 1: Prosecution ──
        prosecutor_argument = await self._run_prosecution(
            agents.get(prosecutor_name),
            prosecutor_name,
            finding_summary,
            evidence_text,
            context_summary,
            intent_summary,
        )
        if prosecutor_argument:
            logger.info(
                "debate.argument",
                finding=finding.title,
                agent=prosecutor_name,
                role="prosecution",
                position=prosecutor_argument.position,
                argument=prosecutor_argument.argument,
            )

        # ── Round 1: Defense ──
        defense_argument = await self._run_defense(
            agents.get(defense_name),
            defense_name,
            finding_summary,
            evidence_text,
            prosecutor_argument.argument if prosecutor_argument else "",
            context_summary,
            intent_summary,
        )

        if defense_argument:
            logger.info(
                "debate.argument",
                finding=finding.title,
                agent=defense_name,
                role="defense",
                position=defense_argument.position,
                argument=defense_argument.argument,
            )

        if not prosecutor_argument or not defense_argument:
            return None

        # ── Check: does defense concede? ──
        defense_concedes = self._defense_concedes(defense_argument)
        rounds_used = 1
        judge_questions_text: str | None = None
        round_2_prosecution: AgentArgument | None = None
        round_2_defense: AgentArgument | None = None

        # ── 2-agent mode (no real judge) ──
        if not has_judge:
            judge_argument = AgentArgument(
                agent_name="none",
                role="judge",
                position="confirmed" if defense_concedes else "unclear",
                argument=(
                    "Defense conceded; finding confirmed."
                    if defense_concedes
                    else "No judge available and defense disagrees; marking unclear for human review."
                ),
                confidence=defense_argument.confidence if defense_concedes else 0.3,
            )
            debate = DebateRecord(
                finding_id=finding.id,
                prosecutor_argument=prosecutor_argument,
                defense_argument=defense_argument,
                judge_ruling=judge_argument,
                rounds_used=1,
                final_severity=finding.severity,
                final_confidence=judge_argument.confidence,
            )
            compute_consensus(debate, intent)
            logger.info(
                "debate.complete_2agent",
                finding=finding.title,
                defense_concedes=defense_concedes,
                consensus=debate.consensus.value,
            )
            logger.info(
                "debate.verdict",
                finding=finding.title,
                consensus=debate.consensus.value,
                final_severity=debate.final_severity.value,
                evidence_quality=debate.evidence_quality or "—",
            )
            return debate

        # ── 3-agent mode: defense concedes → judge verdicts immediately ──
        if defense_concedes:
            logger.info("debate.defense_concedes", finding=finding.title)
            judge_argument, judge_raw = await self._run_judge(
                agents.get(judge_name),
                judge_name,
                finding_summary,
                prosecutor_argument.argument,
                defense_argument.argument,
                None,
                intent_summary,
            )
            if judge_argument:
                logger.info(
                    "debate.argument",
                    finding=finding.title,
                    agent=judge_name,
                    role="judge",
                    position=judge_argument.position,
                    argument=judge_argument.argument,
                )
        else:
            # ── Round 2: Judge-led clarification ──
            logger.info("debate.round2_start", finding=finding.title)
            rounds_used = 2

            judge_questions_text = await self._run_judge_clarification(
                agents.get(judge_name),
                judge_name,
                finding_summary,
                prosecutor_argument.argument,
                defense_argument.argument,
                intent_summary,
            )

            # Both sides respond to judge's questions (parallel)
            import asyncio

            r2_pros_task = self._run_round2_response(
                agents.get(prosecutor_name),
                prosecutor_name,
                "prosecutor",
                finding_summary,
                judge_questions_text or "",
            )
            r2_def_task = self._run_round2_response(
                agents.get(defense_name),
                defense_name,
                "defense",
                finding_summary,
                judge_questions_text or "",
            )
            round_2_prosecution, round_2_defense = await asyncio.gather(
                r2_pros_task, r2_def_task,
            )

            if round_2_prosecution:
                logger.info(
                    "debate.argument",
                    finding=finding.title,
                    agent=prosecutor_name,
                    role="rebuttal",
                    position=round_2_prosecution.position,
                    argument=round_2_prosecution.argument,
                )
            if round_2_defense:
                logger.info(
                    "debate.argument",
                    finding=finding.title,
                    agent=defense_name,
                    role="counter",
                    position=round_2_defense.position,
                    argument=round_2_defense.argument,
                )

            # Judge makes final ruling with all context
            judge_argument, judge_raw = await self._run_judge_final(
                agents.get(judge_name),
                judge_name,
                finding_summary,
                prosecutor_argument.argument,
                defense_argument.argument,
                judge_questions_text or "",
                round_2_prosecution.argument if round_2_prosecution else "",
                round_2_defense.argument if round_2_defense else "",
                intent_summary,
            )
            if judge_argument:
                logger.info(
                    "debate.argument",
                    finding=finding.title,
                    agent=judge_name,
                    role="judge",
                    position=judge_argument.position,
                    argument=judge_argument.argument,
                )

        if not judge_argument:
            return None

        # Parse severity from judge's raw response
        final_severity = finding.severity
        if judge_raw and judge_name in agents:
            try:
                parsed_judge = agents[judge_name].parse_json_response(judge_raw)
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
            judge_questions=judge_questions_text,
            round_2_prosecution=round_2_prosecution,
            round_2_defense=round_2_defense,
            rounds_used=rounds_used,
            final_severity=final_severity,
            final_confidence=judge_argument.confidence,
        )

        # Compute consensus
        compute_consensus(debate, intent)

        logger.info(
            "debate.complete",
            finding=finding.title,
            rounds=rounds_used,
            consensus=debate.consensus.value,
        )
        logger.info(
            "debate.verdict",
            finding=finding.title,
            consensus=debate.consensus.value,
            final_severity=debate.final_severity.value,
            evidence_quality=debate.evidence_quality or "—",
        )

        return debate

    @staticmethod
    def _defense_concedes(defense_argument: AgentArgument) -> bool:
        """Check if the defense conceded (agrees the finding is real)."""
        concede_positions = {"real_issue", "confirmed", "agree", "concede"}
        return defense_argument.position.lower().strip() in concede_positions

    def _assign_roles(
        self, finding: Finding | None = None,
    ) -> tuple[str, str, str]:
        """Assign debate roles based on config mode.

        Modes:
        - "evidence": prosecutor=finder, defense=highest-pref misser, judge=remaining
        - "fixed": use fixed_roles from config
        - "rotate": round-robin rotation (legacy fallback)
        """
        enabled_agents = [name for name, cfg in self.settings.agents.items() if cfg.enabled]
        if not enabled_agents:
            raise AgentError("debate", "No agents are enabled for debate")

        if self.settings.debate.role_assignment == "evidence" and finding:
            return self._assign_evidence_roles(finding, enabled_agents)

        if self.settings.debate.role_assignment == "fixed":
            roles = self.settings.debate.fixed_roles
            if all(roles.get(r) in enabled_agents for r in ("prosecutor", "defense", "judge")):
                return roles["prosecutor"], roles["defense"], roles["judge"]
            logger.warning(
                "debate.fixed_roles_unavailable",
                roles=roles,
                enabled=enabled_agents,
                msg="Falling back to rotation",
            )

        # Rotate roles (legacy fallback)
        agents = list(enabled_agents)
        while len(agents) < 3:
            agents.append(agents[0])

        offset = self._rotation_index % len(agents)
        self._rotation_index += 1

        return agents[offset % len(agents)], agents[(offset + 1) % len(agents)], agents[(offset + 2) % len(agents)]

    def _assign_evidence_roles(
        self, finding: Finding, enabled_agents: list[str],
    ) -> tuple[str, str, str]:
        """Assign roles based on which agents found/missed the finding.

        - Prosecutor: first finder (the agent that reported the vuln)
        - Defense: highest-preference agent among those who missed it
        - Judge: remaining agent, chosen by judge_preference
        """
        finders = [a for a in finding.reviewing_agents if a in enabled_agents]
        missers = [a for a in enabled_agents if a not in finders]

        # Pick prosecutor: first finder
        prosecutor = finders[0] if finders else enabled_agents[0]

        # Pick defense: highest-pref among missers
        defense = self._pick_by_preference(
            self.settings.debate.defense_preference, missers,
        )
        if not defense:
            # No missers (all found) — fall back to defense_pref excluding prosecutor
            candidates = [a for a in enabled_agents if a != prosecutor]
            defense = self._pick_by_preference(
                self.settings.debate.defense_preference, candidates,
            )
        if not defense:
            defense = prosecutor  # single-agent edge case

        # Pick judge: highest-pref among remaining
        assigned = {prosecutor, defense}
        remaining = [a for a in enabled_agents if a not in assigned]
        judge = self._pick_by_preference(
            self.settings.debate.judge_preference, remaining,
        )
        if not judge:
            # 2-agent mode — no judge available
            judge = defense

        logger.info(
            "debate.evidence_roles_assigned",
            finders=finders,
            missers=missers,
            prosecutor=prosecutor,
            defense=defense,
            judge=judge,
        )

        return prosecutor, defense, judge

    @staticmethod
    def _pick_by_preference(preference: list[str], candidates: list[str]) -> str | None:
        """Pick the first candidate that appears in the preference list."""
        for pref in preference:
            if pref in candidates:
                return pref
        return candidates[0] if candidates else None

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

    async def _run_judge_clarification(
        self,
        agent: BaseAgent | None,
        agent_name: str,
        finding_summary: str,
        prosecutor_argument: str,
        defense_argument: str,
        intent_summary: str,
    ) -> str | None:
        """Round 2: Judge identifies disagreement and asks clarifying questions."""
        if not agent:
            return None

        prompt = build_judge_clarification_prompt(
            finding_summary, prosecutor_argument, defense_argument, intent_summary,
        )

        try:
            response = await agent.execute(prompt, JUDGE_CLARIFICATION_SYSTEM_PROMPT)
            return response
        except AgentError as e:
            logger.error("debate.judge_clarification_failed", error=str(e))
            return None

    async def _run_round2_response(
        self,
        agent: BaseAgent | None,
        agent_name: str,
        role: str,
        finding_summary: str,
        judge_questions: str,
    ) -> AgentArgument | None:
        """Round 2: Prosecutor or defense responds to judge's targeted questions."""
        if not agent:
            return AgentArgument(
                agent_name=agent_name, role=f"{role}_round2",
                position="unclear", argument="Agent unavailable", confidence=0.0,
            )

        system = PROSECUTOR_SYSTEM_PROMPT if role == "prosecutor" else DEFENSE_SYSTEM_PROMPT
        prompt = (
            f"## Finding Under Review\n\n{wrap_agent_output(finding_summary, 'review-agent')}\n\n"
            f"## Judge's Questions for You ({role.title()})\n\n{wrap_agent_output(judge_questions, 'judge')}\n\n"
            f"Answer the judge's specific questions. Cite code evidence. "
            f"Be concise and directly address each question."
        )

        try:
            response = await agent.execute(prompt, system)
            return _parse_agent_argument(response, agent, f"{role}_round2")
        except AgentError as e:
            logger.error("debate.round2_response_failed", role=role, error=str(e))
            return None

    async def _run_judge_final(
        self,
        agent: BaseAgent | None,
        agent_name: str,
        finding_summary: str,
        prosecutor_argument: str,
        defense_argument: str,
        judge_questions: str,
        prosecution_response: str,
        defense_response: str,
        intent_summary: str,
    ) -> tuple[AgentArgument | None, str]:
        """Round 2: Judge makes final ruling after hearing both sides' responses."""
        if not agent:
            return AgentArgument(
                agent_name=agent_name, role="judge",
                position="unclear", argument="Agent unavailable", confidence=0.0,
            ), ""

        prompt = build_judge_final_prompt(
            finding_summary, prosecutor_argument, defense_argument,
            judge_questions, prosecution_response, defense_response,
            intent_summary,
        )

        try:
            response = await agent.execute(prompt, JUDGE_SYSTEM_PROMPT)
            return _parse_agent_argument(response, agent, "judge"), response
        except AgentError as e:
            logger.error("debate.judge_final_failed", error=str(e))
            return None, ""

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
