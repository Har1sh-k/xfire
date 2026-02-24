"""Review engine — orchestrates independent parallel agent reviews."""

from __future__ import annotations

import asyncio
import time
from typing import Any

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
        # Map category string to enum
        category_str = raw.get("category", "MISSING_VALIDATION")
        try:
            category = FindingCategory(category_str)
        except ValueError:
            category = FindingCategory.MISSING_VALIDATION

        severity_str = raw.get("severity", "Medium")
        try:
            severity = Severity(severity_str)
        except ValueError:
            severity = Severity.MEDIUM

        exploitability_str = raw.get("exploitability", "Possible")
        try:
            exploitability = Exploitability(exploitability_str)
        except ValueError:
            exploitability = Exploitability.POSSIBLE

        blast_radius_str = raw.get("blast_radius", "Component")
        try:
            blast_radius = BlastRadius(blast_radius_str)
        except ValueError:
            blast_radius = BlastRadius.COMPONENT

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
    ) -> list[AgentReview]:
        """Run independent reviews from all enabled agents in parallel.

        1. Build the review prompt (same prompt for all agents)
        2. Dispatch to all enabled agents concurrently
        3. Parse each agent's structured JSON response
        4. Handle failures gracefully
        5. Return all successful reviews
        """
        # Build the review prompt (same for all agents — fair comparison)
        user_prompt = build_review_prompt(context, intent, skill_outputs)

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
            self._dispatch_to_agent(agent, user_prompt, REVIEW_SYSTEM_PROMPT)
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

            return AgentReview(
                agent_name=agent.name,
                findings=findings,
                overall_risk_assessment=parsed.get("overall_risk", "unknown"),
                review_methodology=parsed.get("risk_summary", ""),
                files_analyzed=[f.get("file", "") for f in parsed.get("findings", [])
                                if isinstance(f, dict) and "file" in f],
                review_duration_seconds=duration,
            )

        except AgentError as e:
            logger.error("review.agent_error", agent=agent.name, error=str(e))
            return None
        except Exception as e:
            logger.error("review.unexpected_error", agent=agent.name, error=str(e))
            return None
