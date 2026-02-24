"""Tests for consensus logic."""

from crossfire.agents.consensus import compute_consensus, _evidence_quality_score
from crossfire.core.models import (
    AgentArgument,
    CitedEvidence,
    ConsensusOutcome,
    DebateRecord,
    IntentProfile,
    Severity,
)


def _make_argument(
    role: str = "prosecutor",
    position: str = "real_issue",
    confidence: float = 0.8,
    citations: int = 2,
    agent_name: str = "claude",
) -> AgentArgument:
    cited = [
        CitedEvidence(
            file_path="app.py",
            line_range=f"{i*10}-{i*10+5}",
            code_snippet=f"code_snippet_{i}",
            explanation=f"This code is problematic because reason {i}",
        )
        for i in range(citations)
    ]
    return AgentArgument(
        agent_name=agent_name,
        role=role,
        position=position,
        argument=f"Detailed argument for {position}",
        cited_evidence=cited,
        confidence=confidence,
    )


def _make_debate(
    prosecutor_position: str = "real_issue",
    defense_position: str = "false_positive",
    judge_position: str = "Confirmed",
    prosecutor_citations: int = 2,
    defense_citations: int = 1,
) -> DebateRecord:
    return DebateRecord(
        finding_id="test-123",
        prosecutor_argument=_make_argument(
            role="prosecutor",
            position=prosecutor_position,
            citations=prosecutor_citations,
        ),
        defense_argument=_make_argument(
            role="defense",
            position=defense_position,
            citations=defense_citations,
            agent_name="codex",
        ),
        judge_ruling=_make_argument(
            role="judge",
            position=judge_position,
            citations=1,
            agent_name="gemini",
        ),
        final_severity=Severity.HIGH,
        final_confidence=0.8,
    )


class TestEvidenceQualityScore:
    def test_no_citations(self):
        arg = AgentArgument(
            agent_name="test", role="prosecutor",
            position="real_issue", argument="Just trust me",
        )
        score = _evidence_quality_score(arg)
        assert score < 0.3  # baseline only

    def test_with_citations(self):
        arg = _make_argument(citations=3)
        score = _evidence_quality_score(arg)
        assert score > 0.7  # strong evidence


class TestConsensus:
    def test_confirmed_with_strong_evidence(self):
        debate = _make_debate(
            prosecutor_position="real_issue",
            defense_position="false_positive",
            judge_position="Confirmed",
            prosecutor_citations=3,
        )
        result = compute_consensus(debate, IntentProfile())
        assert result == ConsensusOutcome.CONFIRMED

    def test_rejected_with_defense_evidence(self):
        debate = _make_debate(
            prosecutor_position="real_issue",
            defense_position="false_positive",
            judge_position="Rejected",
            defense_citations=2,
        )
        result = compute_consensus(debate, IntentProfile())
        assert result == ConsensusOutcome.REJECTED

    def test_unclear_judge_ruling(self):
        debate = _make_debate(judge_position="Unclear")
        result = compute_consensus(debate, IntentProfile())
        assert result == ConsensusOutcome.UNCLEAR

    def test_likely_judge_ruling(self):
        debate = _make_debate(judge_position="Likely")
        result = compute_consensus(debate, IntentProfile())
        assert result == ConsensusOutcome.LIKELY

    def test_weak_prosecution_downgrades_confirmed(self):
        debate = _make_debate(
            judge_position="Confirmed",
            prosecutor_citations=0,  # no evidence at all
        )
        result = compute_consensus(debate, IntentProfile())
        # Should downgrade to Likely due to weak prosecution evidence
        assert result in (ConsensusOutcome.LIKELY, ConsensusOutcome.UNCLEAR)

    def test_weak_defense_upgrades_rejected(self):
        debate = _make_debate(
            judge_position="Rejected",
            defense_citations=0,  # no evidence
        )
        result = compute_consensus(debate, IntentProfile())
        # Should upgrade to Unclear due to weak defense
        assert result == ConsensusOutcome.UNCLEAR

    def test_unanimous_confirmation_high_confidence(self):
        debate = _make_debate(
            prosecutor_position="real_issue",
            defense_position="real_issue",  # defense agrees
            judge_position="Confirmed",
            prosecutor_citations=3,
        )
        original_confidence = debate.final_confidence
        result = compute_consensus(debate, IntentProfile())
        assert result == ConsensusOutcome.CONFIRMED
        assert debate.final_confidence > original_confidence

    def test_evidence_quality_recorded(self):
        debate = _make_debate(prosecutor_citations=3, defense_citations=1)
        compute_consensus(debate, IntentProfile())
        assert "Prosecution:" in debate.evidence_quality
        assert "Defense:" in debate.evidence_quality
