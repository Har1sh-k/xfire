"""Tests for the orchestrator — pipeline logic and risk computation."""

from crossfire.config.settings import AgentConfig, CrossFireSettings
from crossfire.core.models import (
    Finding,
    FindingCategory,
    FindingStatus,
    Severity,
)
from crossfire.core.orchestrator import CrossFireOrchestrator


def _make_settings() -> CrossFireSettings:
    return CrossFireSettings(
        agents={
            "claude": AgentConfig(enabled=True),
            "codex": AgentConfig(enabled=True),
            "gemini": AgentConfig(enabled=False),
        },
    )


def _make_finding(
    severity: Severity = Severity.HIGH,
    status: FindingStatus = FindingStatus.CONFIRMED,
) -> Finding:
    return Finding(
        title="Test Finding",
        category=FindingCategory.COMMAND_INJECTION,
        severity=severity,
        status=status,
    )


class TestComputeOverallRisk:
    def test_no_findings(self):
        orch = CrossFireOrchestrator(_make_settings())
        assert orch._compute_overall_risk([]) == "none"

    def test_critical(self):
        orch = CrossFireOrchestrator(_make_settings())
        findings = [_make_finding(Severity.CRITICAL)]
        assert orch._compute_overall_risk(findings) == "critical"

    def test_high(self):
        orch = CrossFireOrchestrator(_make_settings())
        findings = [_make_finding(Severity.HIGH)]
        assert orch._compute_overall_risk(findings) == "high"

    def test_medium(self):
        orch = CrossFireOrchestrator(_make_settings())
        findings = [_make_finding(Severity.MEDIUM)]
        assert orch._compute_overall_risk(findings) == "medium"

    def test_low(self):
        orch = CrossFireOrchestrator(_make_settings())
        findings = [_make_finding(Severity.LOW)]
        assert orch._compute_overall_risk(findings) == "low"

    def test_rejected_excluded(self):
        orch = CrossFireOrchestrator(_make_settings())
        findings = [_make_finding(Severity.CRITICAL, FindingStatus.REJECTED)]
        assert orch._compute_overall_risk(findings) == "none"

    def test_highest_wins(self):
        orch = CrossFireOrchestrator(_make_settings())
        findings = [
            _make_finding(Severity.LOW),
            _make_finding(Severity.CRITICAL),
            _make_finding(Severity.MEDIUM),
        ]
        assert orch._compute_overall_risk(findings) == "critical"


class TestBuildSummary:
    def test_normal_summary(self):
        orch = CrossFireOrchestrator(_make_settings())
        findings = [
            _make_finding(status=FindingStatus.CONFIRMED),
            _make_finding(status=FindingStatus.LIKELY),
            _make_finding(status=FindingStatus.REJECTED),
        ]
        summary = orch._build_summary(findings, ["review1"], [])
        assert "1 confirmed" in summary
        assert "1 likely" in summary
        assert "1 rejected" in summary
        assert "1 agent(s)" in summary

    def test_all_agents_failed(self):
        orch = CrossFireOrchestrator(_make_settings())
        summary = orch._build_summary([], [], [])
        assert "WARNING" in summary
        assert "failed" in summary

    def test_no_findings_with_reviews(self):
        orch = CrossFireOrchestrator(_make_settings())
        summary = orch._build_summary([], ["r1", "r2"], [])
        assert "0 confirmed" in summary
        assert "2 agent(s)" in summary
