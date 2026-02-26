"""Review engine — orchestrates independent parallel agent reviews."""

from __future__ import annotations

import asyncio
import time
from enum import Enum

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.agents.claude_adapter import ClaudeAgent
from crossfire.agents.codex_adapter import CodexAgent
from crossfire.agents.gemini_adapter import GeminiAgent
from crossfire.agents.prompts.review_prompt import (
    REVIEW_SYSTEM_PROMPT,
    build_review_prompt,
)
from crossfire.config.settings import AgentConfig, CrossFireSettings
from crossfire.core.models import (
    AgentReview,
    BlastRadius,
    Evidence,
    Exploitability,
    Finding,
    FindingCategory,
    IntentProfile,
    LineRange,
    PRContext,
    PurposeAssessment,
    Severity,
)

logger = structlog.get_logger()


def _parse_enum_flexible(enum_cls: type[Enum], value: str, default: Enum) -> Enum:
    """Parse an enum value with case-insensitive fallback."""
    try:
        return enum_cls(value)
    except ValueError:
        pass
    # Case-insensitive match against enum values
    value_lower = str(value).lower().strip()
    for member in enum_cls:
        if member.value.lower() == value_lower:
            return member
    return default


AGENT_CLASSES: dict[str, type[BaseAgent]] = {
    "claude": ClaudeAgent,
    "codex": CodexAgent,
    "gemini": GeminiAgent,
}


def _create_agent(name: str, config: AgentConfig) -> BaseAgent:
    """Create an agent instance by name."""
    cls = AGENT_CLASSES.get(name)
    if not cls:
        raise ValueError(f"Unknown agent: {name}")
    return cls(config)


def _parse_finding_from_raw(raw: dict, agent_name: str) -> Finding | None:
    """Parse a raw finding dict from agent JSON into a Finding model."""
    try:
        # Map category string to enum (case-insensitive)
        category = _parse_enum_flexible(
            FindingCategory,
            raw.get("category", "MISSING_VALIDATION"),
            FindingCategory.MISSING_VALIDATION,
        )
        severity = _parse_enum_flexible(
            Severity, raw.get("severity", "Medium"), Severity.MEDIUM,
        )
        exploitability = _parse_enum_flexible(
            Exploitability, raw.get("exploitability", "Possible"), Exploitability.POSSIBLE,
        )
        blast_radius = _parse_enum_flexible(
            BlastRadius, raw.get("blast_radius", "Component"), BlastRadius.COMPONENT,
        )

        # Parse evidence
        evidence_list: list[Evidence] = []
        for ev in raw.get("evidence", []):
            evidence_list.append(Evidence(
                source=agent_name,
                evidence_type=ev.get("type", "code_reading"),
                description=ev.get("description", ""),
                file_path=ev.get("file"),
                code_snippet=ev.get("code"),
                context_snippet=ev.get("context"),
                confidence=raw.get("confidence", 0.5),
            ))

        # Parse line ranges
        line_ranges: list[LineRange] = []
        for lr_str in raw.get("line_ranges", []):
            if "-" in str(lr_str):
                parts = str(lr_str).split("-")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    affected = raw.get("affected_files", ["unknown"])
                    line_ranges.append(LineRange(
                        file_path=affected[0] if affected else "unknown",
                        start_line=int(parts[0]),
                        end_line=int(parts[1]),
                    ))

        # Parse purpose assessment
        pa_raw = raw.get("purpose_aware", {})
        purpose_assessment = PurposeAssessment(
            is_intended_capability=pa_raw.get("is_intended", False),
            trust_boundary_violated=pa_raw.get("trust_boundary_violated", False),
            untrusted_input_reaches_sink=pa_raw.get("untrusted_input_reaches_sink", False),
            isolation_controls_present=pa_raw.get("controls_present", False),
            assessment=pa_raw.get("assessment", ""),
        )

        return Finding(
            title=raw.get("title", "Untitled finding"),
            category=category,
            severity=severity,
            confidence=raw.get("confidence", 0.5),
            exploitability=exploitability,
            blast_radius=blast_radius,
            purpose_aware_assessment=purpose_assessment,
            affected_files=raw.get("affected_files", []),
            line_ranges=line_ranges,
            evidence=evidence_list,
            data_flow_trace=raw.get("data_flow_trace"),
            reproduction_risk_notes=raw.get("reproduction_risk", ""),
            mitigations=raw.get("mitigations", []),
            rationale_summary=raw.get("rationale", ""),
            reviewing_agents=[agent_name],
        )

    except Exception as e:
        logger.warning("finding.parse_error", agent=agent_name, error=str(e))
        return None


class ReviewEngine:
    """Orchestrates independent parallel reviews from all enabled agents."""

    def __init__(self, settings: CrossFireSettings) -> None:
        self.settings = settings

    async def run_independent_reviews(
        self,
        context: PRContext,
        intent: IntentProfile,
        skill_outputs: dict[str, str],
        system_prompt: str | None = None,
    ) -> list[AgentReview]:
        """Run independent reviews from all enabled agents in parallel.

        1. Build the review prompt (same prompt for all agents)
        2. Dispatch to all enabled agents concurrently
        3. Parse each agent's structured JSON response
        4. Handle failures gracefully
        5. Return all successful reviews

        Args:
            system_prompt: Optional repo-specific system prompt from fast model.
                           If None, falls back to REVIEW_SYSTEM_PROMPT.
        """
        # Build the review prompt (same for all agents — fair comparison)
        user_prompt = build_review_prompt(context, intent, skill_outputs)

        # Use provided system prompt or fall back to the default
        effective_system_prompt = system_prompt or REVIEW_SYSTEM_PROMPT

        # Create agent instances
        agents: list[BaseAgent] = []
        for name, config in self.settings.agents.items():
            if config.enabled:
                try:
                    agent = _create_agent(name, config)
                    agents.append(agent)
                except ValueError as e:
                    logger.warning("agent.create_failed", agent=name, error=str(e))

        if not agents:
            logger.error("review.no_agents", msg="No agents enabled")
            return []

        logger.info("review.start", agents=[a.name for a in agents])

        # Dispatch all reviews in parallel
        tasks = [
            self._dispatch_to_agent(agent, user_prompt, effective_system_prompt)
            for agent in agents
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect successful reviews
        reviews: list[AgentReview] = []
        for agent, result in zip(agents, results):
            if isinstance(result, Exception):
                logger.error("review.agent_failed", agent=agent.name, error=str(result))
            elif result is not None:
                reviews.append(result)
                logger.info(
                    "review.agent_complete",
                    agent=agent.name,
                    findings=len(result.findings),
                )

        return reviews

    async def _dispatch_to_agent(
        self,
        agent: BaseAgent,
        prompt: str,
        system_prompt: str,
    ) -> AgentReview | None:
        """Send review prompt to a single agent and parse the response."""
        start_time = time.monotonic()

        try:
            raw_response = await agent.execute(
                prompt=prompt,
                system_prompt=system_prompt,
            )

            # Parse the JSON response
            parsed = agent.parse_json_response(raw_response)

            # Convert to AgentReview
            findings: list[Finding] = []
            for raw_finding in parsed.get("findings", []):
                finding = _parse_finding_from_raw(raw_finding, agent.name)
                if finding:
                    findings.append(finding)

            duration = time.monotonic() - start_time

            # Use dedicated files_analyzed key if present, else extract from findings
            files_analyzed = parsed.get("files_analyzed")
            if not files_analyzed:
                files_analyzed = [
                    f.get("file", "") for f in parsed.get("findings", [])
                    if isinstance(f, dict) and "file" in f
                ]

            return AgentReview(
                agent_name=agent.name,
                findings=findings,
                overall_risk_assessment=parsed.get("overall_risk", "unknown"),
                review_methodology=parsed.get("risk_summary", ""),
                files_analyzed=files_analyzed,
                review_duration_seconds=duration,
            )

        except AgentError as e:
            logger.error("review.agent_error", agent=agent.name, error=str(e))
            return None
        except Exception as e:
            logger.error("review.unexpected_error", agent=agent.name, error=str(e))
            return None
