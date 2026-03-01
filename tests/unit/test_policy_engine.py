"""Tests for the policy engine — suppressions and waivers."""

from xfire.core.models import Finding, FindingCategory, FindingStatus, Severity
from xfire.core.policy_engine import PolicyEngine


def _make_finding(
    title: str = "Test Finding",
    category: FindingCategory = FindingCategory.COMMAND_INJECTION,
    files: list[str] | None = None,
) -> Finding:
    return Finding(
        title=title,
        category=category,
        severity=Severity.HIGH,
        confidence=0.8,
        affected_files=files or ["src/main.py"],
    )


class TestPolicyEngine:
    def test_no_suppressions(self):
        engine = PolicyEngine([])
        findings = [_make_finding()]
        result = engine.apply(findings)
        assert len(result) == 1
        assert result[0].status != FindingStatus.REJECTED

    def test_suppress_by_category(self):
        engine = PolicyEngine([
            {"category": "COMMAND_INJECTION", "reason": "accepted risk"},
        ])
        findings = [_make_finding()]
        result = engine.apply(findings)
        assert result[0].status == FindingStatus.REJECTED
        assert "accepted risk" in result[0].debate_summary

    def test_category_mismatch_not_suppressed(self):
        engine = PolicyEngine([
            {"category": "SQL_INJECTION", "reason": "wrong category"},
        ])
        findings = [_make_finding(category=FindingCategory.COMMAND_INJECTION)]
        result = engine.apply(findings)
        assert result[0].status != FindingStatus.REJECTED

    def test_suppress_by_file_pattern(self):
        engine = PolicyEngine([
            {"file_pattern": "tests/.*", "reason": "test code"},
        ])
        findings = [_make_finding(files=["tests/test_main.py"])]
        result = engine.apply(findings)
        assert result[0].status == FindingStatus.REJECTED

    def test_file_pattern_no_match(self):
        engine = PolicyEngine([
            {"file_pattern": "tests/.*", "reason": "test code"},
        ])
        findings = [_make_finding(files=["src/main.py"])]
        result = engine.apply(findings)
        assert result[0].status != FindingStatus.REJECTED

    def test_suppress_by_title_pattern(self):
        engine = PolicyEngine([
            {"title_pattern": ".*unsafe.*", "reason": "known issue"},
        ])
        findings = [_make_finding(title="Unsafe deserialization in handler")]
        result = engine.apply(findings)
        assert result[0].status == FindingStatus.REJECTED

    def test_combined_rule_all_must_match(self):
        engine = PolicyEngine([
            {
                "category": "COMMAND_INJECTION",
                "file_pattern": "scripts/.*",
                "reason": "accepted in scripts",
            },
        ])
        # File doesn't match, so should NOT be suppressed
        f1 = _make_finding(files=["src/main.py"])
        result = engine.apply([f1])
        assert result[0].status != FindingStatus.REJECTED

        # Both match, should be suppressed
        f2 = _make_finding(files=["scripts/deploy.py"])
        result = engine.apply([f2])
        assert result[0].status == FindingStatus.REJECTED

    def test_default_reason_when_not_specified(self):
        engine = PolicyEngine([{"category": "COMMAND_INJECTION"}])
        findings = [_make_finding()]
        result = engine.apply(findings)
        assert result[0].status == FindingStatus.REJECTED
        assert "Matched suppression rule" in result[0].debate_summary

    def test_multiple_findings_selective_suppression(self):
        engine = PolicyEngine([
            {"category": "SQL_INJECTION", "reason": "accepted"},
        ])
        findings = [
            _make_finding(category=FindingCategory.COMMAND_INJECTION),
            _make_finding(category=FindingCategory.SQL_INJECTION),
        ]
        result = engine.apply(findings)
        assert result[0].status != FindingStatus.REJECTED
        assert result[1].status == FindingStatus.REJECTED
