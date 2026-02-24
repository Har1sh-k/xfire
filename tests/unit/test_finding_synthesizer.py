"""Tests for finding synthesizer."""

from crossfire.core.finding_synthesizer import (
    FindingSynthesizer,
    _is_similar_finding,
    compute_debate_budget,
    merge_severity,
)
from crossfire.core.models import (
    AgentReview,
    BlastRadius,
    DebateTag,
    Evidence,
    Finding,
    FindingCategory,
    FindingStatus,
    IntentProfile,
    LineRange,
    PurposeAssessment,
    Severity,
)


def _make_finding(**kwargs) -> Finding:
    """Helper to create a Finding with defaults."""
    defaults = {
        "title": "Test Finding",
        "category": FindingCategory.COMMAND_INJECTION,
        "severity": Severity.HIGH,
        "confidence": 0.8,
        "affected_files": ["app.py"],
        "reviewing_agents": ["claude"],
    }
    defaults.update(kwargs)
    return Finding(**defaults)


def _make_review(agent: str, findings: list[Finding]) -> AgentReview:
    return AgentReview(agent_name=agent, findings=findings)


class TestSimilarityDetection:
    def test_same_category_same_file_same_lines(self):
        a = _make_finding(
            line_ranges=[LineRange(file_path="app.py", start_line=10, end_line=15)],
        )
        b = _make_finding(
            line_ranges=[LineRange(file_path="app.py", start_line=12, end_line=18)],
            reviewing_agents=["codex"],
        )
        assert _is_similar_finding(a, b) is True

    def test_different_category_not_similar(self):
        a = _make_finding(category=FindingCategory.COMMAND_INJECTION)
        b = _make_finding(category=FindingCategory.SQL_INJECTION)
        assert _is_similar_finding(a, b) is False

    def test_different_files_not_similar(self):
        a = _make_finding(affected_files=["api.py"])
        b = _make_finding(affected_files=["auth.py"])
        assert _is_similar_finding(a, b) is False


class TestSynthesizer:
    def test_empty_reviews(self):
        synth = FindingSynthesizer()
        result = synth.synthesize([], IntentProfile())
        assert result == []

    def test_single_finding_no_merge(self):
        synth = FindingSynthesizer()
        f = _make_finding()
        reviews = [_make_review("claude", [f])]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1

    def test_merges_similar_findings(self):
        synth = FindingSynthesizer()
        f1 = _make_finding(
            reviewing_agents=["claude"],
            evidence=[Evidence(source="claude", evidence_type="code_reading",
                              description="Found issue", confidence=0.8)],
            line_ranges=[LineRange(file_path="app.py", start_line=10, end_line=15)],
        )
        f2 = _make_finding(
            reviewing_agents=["codex"],
            evidence=[Evidence(source="codex", evidence_type="code_reading",
                              description="Same issue", confidence=0.7)],
            line_ranges=[LineRange(file_path="app.py", start_line=12, end_line=18)],
        )
        reviews = [
            _make_review("claude", [f1]),
            _make_review("codex", [f2]),
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        assert "claude" in result[0].reviewing_agents
        assert "codex" in result[0].reviewing_agents
        assert len(result[0].evidence) == 2

    def test_confidence_boost_two_agents(self):
        synth = FindingSynthesizer()
        f1 = _make_finding(
            confidence=0.7,
            reviewing_agents=["claude"],
            line_ranges=[LineRange(file_path="app.py", start_line=10, end_line=15)],
        )
        f2 = _make_finding(
            confidence=0.7,
            reviewing_agents=["codex"],
            line_ranges=[LineRange(file_path="app.py", start_line=10, end_line=15)],
        )
        reviews = [_make_review("claude", [f1]), _make_review("codex", [f2])]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        # Confidence should be boosted (0.7 * 1.2 = 0.84, capped at 0.95)
        assert result[0].confidence > 0.7

    def test_confidence_boost_three_agents(self):
        synth = FindingSynthesizer()
        findings = [
            _make_finding(
                confidence=0.7,
                reviewing_agents=[agent],
                line_ranges=[LineRange(file_path="app.py", start_line=10, end_line=15)],
            )
            for agent in ["claude", "codex", "gemini"]
        ]
        reviews = [_make_review(a, [f]) for a, f in zip(["claude", "codex", "gemini"], findings)]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        # 0.7 * 1.4 = 0.98
        assert result[0].confidence > 0.9

    def test_debate_tag_single_agent_informational(self):
        """1-agent mode always tags informational regardless of severity."""
        synth = FindingSynthesizer()
        f = _make_finding(severity=Severity.CRITICAL)
        reviews = [_make_review("claude", [f])]
        result = synth.synthesize(reviews, IntentProfile())
        assert result[0].debate_tag == DebateTag.INFORMATIONAL

    def test_debate_tag_one_of_two_needs_debate(self):
        """1 agent found, 1 agent missed → needs debate."""
        synth = FindingSynthesizer()
        f = _make_finding(reviewing_agents=["claude"])
        reviews = [
            _make_review("claude", [f]),
            _make_review("codex", []),  # codex ran but missed it
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert result[0].debate_tag == DebateTag.NEEDS_DEBATE

    def test_debate_tag_all_agents_auto_confirmed(self):
        """All agents found the same issue → auto-confirmed."""
        synth = FindingSynthesizer()
        f1 = _make_finding(
            reviewing_agents=["claude"],
            line_ranges=[LineRange(file_path="app.py", start_line=10, end_line=15)],
        )
        f2 = _make_finding(
            reviewing_agents=["codex"],
            line_ranges=[LineRange(file_path="app.py", start_line=10, end_line=15)],
        )
        reviews = [_make_review("claude", [f1]), _make_review("codex", [f2])]
        result = synth.synthesize(reviews, IntentProfile())
        assert result[0].debate_tag == DebateTag.AUTO_CONFIRMED

    def test_intended_capability_with_controls_filtered_out(self):
        """Intended capabilities with isolation controls are dropped entirely."""
        synth = FindingSynthesizer()
        f = _make_finding(
            severity=Severity.CRITICAL,
            purpose_aware_assessment=PurposeAssessment(
                is_intended_capability=True,
                isolation_controls_present=True,
            ),
        )
        reviews = [_make_review("claude", [f])]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 0

    def test_sensitive_path_boosts_confidence(self):
        synth = FindingSynthesizer()
        f = _make_finding(confidence=0.7, affected_files=["auth/login.py"])
        reviews = [_make_review("claude", [f])]
        intent = IntentProfile(sensitive_paths=["auth/"])
        result = synth.synthesize(reviews, intent)
        assert result[0].confidence > 0.7

    def test_different_findings_not_merged(self):
        synth = FindingSynthesizer()
        f1 = _make_finding(
            title="SQL Injection",
            category=FindingCategory.SQL_INJECTION,
            affected_files=["db.py"],
            reviewing_agents=["claude"],
        )
        f2 = _make_finding(
            title="Command Injection",
            category=FindingCategory.COMMAND_INJECTION,
            affected_files=["api.py"],
            reviewing_agents=["codex"],
        )
        reviews = [_make_review("claude", [f1]), _make_review("codex", [f2])]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 2


class TestMergeSeverity:
    def test_empty_list_returns_medium(self):
        assert merge_severity([]) == Severity.MEDIUM

    def test_critical_wins(self):
        assert merge_severity([Severity.LOW, Severity.CRITICAL, Severity.LOW]) == Severity.CRITICAL

    def test_median_without_critical(self):
        result = merge_severity([Severity.HIGH, Severity.MEDIUM, Severity.LOW])
        assert result == Severity.MEDIUM


class TestComputeDebateBudgetUnit:
    def test_small_pr(self):
        assert compute_debate_budget(10) == 2

    def test_medium_pr(self):
        assert compute_debate_budget(50) == 6

    def test_large_pr(self):
        assert compute_debate_budget(200) == 12

    def test_huge_pr(self):
        assert compute_debate_budget(1000) == 20


class TestCheckSilentDissent:
    def test_no_missing_agents(self):
        synth = FindingSynthesizer()
        finding = _make_finding(reviewing_agents=["claude"])
        reviews = [_make_review("claude", [finding])]
        assert synth._check_silent_dissent(finding, [], reviews) is False

    def test_missing_agent_no_findings(self):
        synth = FindingSynthesizer()
        finding = _make_finding(
            reviewing_agents=["claude"],
            affected_files=["app.py"],
        )
        reviews = [
            _make_review("claude", [finding]),
            _make_review("codex", []),
        ]
        assert synth._check_silent_dissent(finding, ["codex"], reviews) is False

    def test_missing_agent_with_overlapping_rejected(self):
        synth = FindingSynthesizer()
        finding = _make_finding(
            reviewing_agents=["claude"],
            affected_files=["app.py"],
            line_ranges=[LineRange(file_path="app.py", start_line=10, end_line=20)],
        )
        rejected = _make_finding(
            status=FindingStatus.REJECTED,
            affected_files=["app.py"],
            line_ranges=[LineRange(file_path="app.py", start_line=15, end_line=25)],
        )
        reviews = [
            _make_review("claude", [finding]),
            _make_review("codex", [rejected]),
        ]
        assert synth._check_silent_dissent(finding, ["codex"], reviews) is True


class TestFilterNonExploitable:
    def test_architectural_finding_filtered(self):
        synth = FindingSynthesizer()
        finding = _make_finding(category=FindingCategory.MISSING_RATE_LIMIT)
        result = synth._filter_non_exploitable([finding], IntentProfile())
        assert len(result) == 0

    def test_non_architectural_finding_kept(self):
        synth = FindingSynthesizer()
        finding = _make_finding(category=FindingCategory.SQL_INJECTION)
        result = synth._filter_non_exploitable([finding], IntentProfile())
        assert len(result) == 1
