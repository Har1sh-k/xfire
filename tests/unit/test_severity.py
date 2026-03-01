"""Tests for severity gating logic."""

from xfire.core.models import Finding, FindingCategory, FindingStatus, Severity
from xfire.core.severity import should_fail_ci


def _make_finding(
    severity: Severity = Severity.HIGH,
    confidence: float = 0.8,
    status: FindingStatus = FindingStatus.CONFIRMED,
) -> Finding:
    return Finding(
        title="Test",
        category=FindingCategory.COMMAND_INJECTION,
        severity=severity,
        confidence=confidence,
        status=status,
    )


class TestShouldFailCI:
    def test_fails_on_confirmed_high(self):
        findings = [_make_finding(Severity.HIGH, 0.9, FindingStatus.CONFIRMED)]
        assert should_fail_ci(findings, "high", 0.7, True) is True

    def test_passes_on_low_when_threshold_high(self):
        findings = [_make_finding(Severity.LOW, 0.9, FindingStatus.CONFIRMED)]
        assert should_fail_ci(findings, "high", 0.7, True) is False

    def test_passes_on_low_confidence(self):
        findings = [_make_finding(Severity.CRITICAL, 0.3, FindingStatus.CONFIRMED)]
        assert should_fail_ci(findings, "high", 0.7, True) is False

    def test_passes_on_unclear_when_debate_required(self):
        findings = [_make_finding(Severity.CRITICAL, 0.9, FindingStatus.UNCLEAR)]
        assert should_fail_ci(findings, "high", 0.7, True) is False

    def test_fails_on_unclear_when_debate_not_required(self):
        findings = [_make_finding(Severity.CRITICAL, 0.9, FindingStatus.UNCLEAR)]
        assert should_fail_ci(findings, "high", 0.7, False) is True

    def test_no_findings(self):
        assert should_fail_ci([], "high", 0.7, True) is False

    def test_likely_passes_with_debate_required(self):
        findings = [_make_finding(Severity.HIGH, 0.9, FindingStatus.LIKELY)]
        assert should_fail_ci(findings, "high", 0.7, True) is True

    def test_rejected_never_fails(self):
        findings = [_make_finding(Severity.CRITICAL, 0.99, FindingStatus.REJECTED)]
        assert should_fail_ci(findings, "high", 0.7, True) is False
