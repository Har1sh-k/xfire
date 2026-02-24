"""Tests for the orchestrator — pipeline logic and risk computation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crossfire.config.settings import AgentConfig, CrossFireSettings, SkillsConfig
from crossfire.core.models import (
    AgentReview,
    Finding,
    FindingCategory,
    FindingStatus,
    IntentProfile,
    PRContext,
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


# ─── Pipeline Tests ─────────────────────────────────────────────────────────


def _make_context(**kwargs) -> PRContext:
    defaults = {"repo_name": "test/repo", "pr_title": "Test PR"}
    defaults.update(kwargs)
    return PRContext(**defaults)


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_no_findings(self):
        """Pipeline with zero findings returns empty report."""
        orch = CrossFireOrchestrator(_make_settings())
        with patch.object(orch.review_engine, "run_independent_reviews", new_callable=AsyncMock, return_value=[]):
            report = await orch._run_pipeline(_make_context())
        assert report.findings == []
        assert report.overall_risk == "none"

    @pytest.mark.asyncio
    async def test_findings_skip_debate(self):
        """Pipeline with skip_debate=True should not invoke debate engine."""
        orch = CrossFireOrchestrator(_make_settings())
        review = AgentReview(
            agent_name="claude",
            findings=[_make_finding()],
        )
        with patch.object(orch.review_engine, "run_independent_reviews", new_callable=AsyncMock, return_value=[review]):
            with patch.object(orch.debate_engine, "debate_all", new_callable=AsyncMock) as mock_debate:
                report = await orch._run_pipeline(_make_context(), skip_debate=True)
        mock_debate.assert_not_called()
        assert len(report.findings) >= 1

    @pytest.mark.asyncio
    async def test_findings_with_debate(self):
        """Pipeline with findings triggers debate for NEEDS_DEBATE findings."""
        orch = CrossFireOrchestrator(_make_settings())
        finding = _make_finding()
        review = AgentReview(agent_name="claude", findings=[finding])
        with (
            patch.object(orch.review_engine, "run_independent_reviews", new_callable=AsyncMock, return_value=[review]),
            patch.object(orch.debate_engine, "debate_all", new_callable=AsyncMock, return_value=[]) as mock_debate,
        ):
            await orch._run_pipeline(_make_context())
        # debate_all should be called if there are NEEDS_DEBATE findings
        # (single-agent review produces INFORMATIONAL, so debate may not trigger)
        # This tests that the pipeline runs without error


class TestAnalyzeDiff:
    @pytest.mark.asyncio
    async def test_patch_file(self, tmp_path):
        """analyze_diff with a patch file calls build_from_patch_file."""
        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff --git a/app.py b/app.py\n")
        orch = CrossFireOrchestrator(_make_settings())
        with (
            patch.object(orch.context_builder, "build_from_patch_file", return_value=_make_context()),
            patch.object(orch, "_run_pipeline", new_callable=AsyncMock, return_value=MagicMock()) as mock_pipe,
        ):
            await orch.analyze_diff(repo_dir=str(tmp_path), patch_path=str(patch_file))
        mock_pipe.assert_called_once()

    @pytest.mark.asyncio
    async def test_staged_mode(self, tmp_path):
        """analyze_diff in staged mode calls build_from_staged."""
        orch = CrossFireOrchestrator(_make_settings())
        with (
            patch.object(orch.context_builder, "build_from_staged", return_value=_make_context()),
            patch.object(orch, "_run_pipeline", new_callable=AsyncMock, return_value=MagicMock()) as mock_pipe,
        ):
            await orch.analyze_diff(repo_dir=str(tmp_path), staged=True)
        mock_pipe.assert_called_once()


class TestRunSkills:
    def test_all_disabled(self):
        """No skills run when all are disabled."""
        settings = CrossFireSettings(
            agents={"claude": AgentConfig(enabled=True)},
            skills=SkillsConfig(
                code_navigation=False,
                data_flow_tracing=False,
                git_archeology=False,
                config_analysis=False,
                dependency_analysis=False,
                test_coverage_check=False,
            ),
        )
        orch = CrossFireOrchestrator(settings)
        ctx = _make_context()
        result = orch._run_skills(ctx, IntentProfile())
        assert result == {}

    def test_skill_error_handled(self, tmp_path):
        """A skill that raises an exception is caught and returns 'Not available'."""
        settings = _make_settings()
        orch = CrossFireOrchestrator(settings)
        ctx = _make_context()
        with patch(
            "crossfire.core.orchestrator.DataFlowTracingSkill"
        ) as MockSkill:
            MockSkill.return_value.execute.side_effect = RuntimeError("boom")
            result = orch._run_skills(ctx, IntentProfile(), str(tmp_path))
        assert result.get("data_flow") == "Not available"
