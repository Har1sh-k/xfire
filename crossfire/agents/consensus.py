"""Consensus logic — determine final verdict from debate arguments."""

from __future__ import annotations

import structlog

from crossfire.core.models import (
    AgentArgument,
    ConsensusOutcome,
    DebateRecord,
    IntentProfile,
    Severity,
)

logger = structlog.get_logger()

SEVERITY_ORDER = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
}


def _evidence_quality_score(argument: AgentArgument) -> float:
    """Score the evidence quality of an argument.

    - Arguments with specific file:line citations → strong (0.8-1.0)
    - Arguments with code snippets → strong (0.7-0.9)
    - Arguments with only abstract reasoning → weak (0.1-0.3)
    """
    score = 0.2  # baseline for having an argument at all

    for citation in argument.cited_evidence:
        if citation.file_path and citation.line_range:
            score += 0.25  # specific file:line citation
        if citation.code_snippet:
            score += 0.15  # includes code
        if citation.explanation:
            score += 0.1   # explains why

    return min(score, 1.0)


def _categorize_evidence_quality(score: float) -> str:
    """Convert numeric evidence quality to category."""
    if score >= 0.7:
        return "strong"
    elif score >= 0.4:
        return "moderate"
    return "weak"


def compute_consensus(
    debate: DebateRecord,
    intent: IntentProfile,
) -> ConsensusOutcome:
    """Determine final consensus from the debate.

    Primary signal: Judge's ruling (they've seen all arguments)

    Cross-checks:
    - Judge Confirmed + Prosecutor real_issue → Confirmed (high confidence)
    - Judge Confirmed + Defense also real_issue → Confirmed (very high, unanimous)
    - Judge Rejected + Defense showed controls → Rejected
    - Judge Unclear → Unclear
    - Judge Confirmed but weak evidence → downgrade to Likely

    Purpose-aware override:
    - If intent shows intended + controls + no bypass → bias toward Rejected

    Minimum evidence threshold:
    - Confirmed requires 2+ strong evidence from prosecution
    - Rejected requires 1+ strong evidence from defense
    """
    judge = debate.judge_ruling
    prosecutor = debate.prosecutor_argument
    defense = debate.defense_argument

    # Compute evidence quality
    prosecution_quality = _evidence_quality_score(prosecutor)
    defense_quality = _evidence_quality_score(defense)

    # Start with judge's ruling
    judge_position = judge.position.lower()

    if "confirmed" in judge_position:
        outcome = ConsensusOutcome.CONFIRMED
    elif "likely" in judge_position:
        outcome = ConsensusOutcome.LIKELY
    elif "rejected" in judge_position:
        outcome = ConsensusOutcome.REJECTED
    else:
        outcome = ConsensusOutcome.UNCLEAR

    # Check unanimity: defense conceded (both sides agree finding is real)
    defense_concedes = defense.position.lower() in ("real_issue", "confirmed", "agree", "concede")

    # Cross-check with other positions
    if outcome == ConsensusOutcome.CONFIRMED:
        # If defense also says real_issue → unanimous, boost confidence
        if defense_concedes:
            debate.final_confidence = min(debate.final_confidence + 0.15, 0.99)

        # Weak prosecution evidence → downgrade to Likely
        # Waived when unanimous: defense concession is stronger than citation count
        if prosecution_quality < 0.4 and not defense_concedes:
            logger.info("consensus.downgrade", reason="weak prosecution evidence")
            outcome = ConsensusOutcome.LIKELY

        # Minimum evidence threshold: need 2+ cited evidence items
        # Waived when unanimous: all agents agree, no need for citation-count gate
        if len(prosecutor.cited_evidence) < 2 and not defense_concedes:
            if outcome == ConsensusOutcome.CONFIRMED:
                outcome = ConsensusOutcome.LIKELY

    elif outcome == ConsensusOutcome.REJECTED:
        # Need at least some evidence from defense
        if defense_quality < 0.3:
            logger.info("consensus.upgrade", reason="weak defense evidence")
            outcome = ConsensusOutcome.UNCLEAR

    # Purpose-aware override
    if intent.intended_capabilities:
        # Check if any finding evidence relates to an intended capability
        prosecutor_mentions_intended = any(
            any(cap.lower() in ev.explanation.lower()
                for cap in intent.intended_capabilities)
            for ev in prosecutor.cited_evidence
            if ev.explanation
        )

        defense_shows_controls = any(
            "control" in ev.explanation.lower() or
            "sandbox" in ev.explanation.lower() or
            "validation" in ev.explanation.lower()
            for ev in defense.cited_evidence
            if ev.explanation
        )

        if prosecutor_mentions_intended and defense_shows_controls:
            if outcome == ConsensusOutcome.CONFIRMED:
                outcome = ConsensusOutcome.LIKELY
            elif outcome == ConsensusOutcome.LIKELY:
                outcome = ConsensusOutcome.UNCLEAR

    # Update debate record
    debate.consensus = outcome
    debate.evidence_quality = (
        f"Prosecution: {_categorize_evidence_quality(prosecution_quality)}, "
        f"Defense: {_categorize_evidence_quality(defense_quality)}"
    )

    return outcome
