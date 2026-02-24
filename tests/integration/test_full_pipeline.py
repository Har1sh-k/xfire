"""Integration tests for the full CrossFire pipeline.

These tests exercise context building and intent inference on fixtures
without calling actual LLM agents (which require API keys).
"""

import json
from pathlib import Path

import pytest

from crossfire.core.context_builder import ContextBuilder, parse_diff
from crossfire.core.intent_inference import IntentInferrer
from crossfire.core.finding_synthesizer import FindingSynthesizer
from crossfire.core.models import (
    AgentReview,
    Finding,
    FindingCategory,
    IntentProfile,
    PRContext,
    Severity,
)
from crossfire.config.settings import AnalysisConfig, load_settings
from crossfire.output.markdown_report import generate_markdown_report
from crossfire.output.json_report import generate_json_report
from crossfire.output.sarif_report import generate_sarif_report
from crossfire.core.models import CrossFireReport

FIXTURES = Path(__file__).parent.parent / "fixtures" / "prs"


class TestFixtureDiffParsing:
    """Test that all fixture diffs parse correctly."""

    @pytest.fixture(params=[
        "auth_bypass_regression",
        "command_injection_exposure",
        "intended_exec_with_sandbox",
        "secret_logging",
        "destructive_migration",
        "race_condition_data_corruption",
        "safe_refactor_no_issues",
    ])
    def fixture_name(self, request):
        return request.param

    def test_diff_parses(self, fixture_name):
        diff_path = FIXTURES / fixture_name / "diff.patch"
        diff_text = diff_path.read_text()
        files = parse_diff(diff_text)
        assert len(files) > 0, f"No files parsed from {fixture_name} diff"

    def test_context_json_valid(self, fixture_name):
        ctx_path = FIXTURES / fixture_name / "context.json"
        data = json.loads(ctx_path.read_text())
        assert "repo_purpose" in data

    def test_expected_json_valid(self, fixture_name):
        exp_path = FIXTURES / fixture_name / "expected.json"
        data = json.loads(exp_path.read_text())
        assert "expected_findings" in data


class TestReportGeneration:
    """Test that reports can be generated from a fixture-based analysis."""

    def _build_mock_report(self) -> CrossFireReport:
        return CrossFireReport(
            repo_name="test/repo",
            pr_number=1,
            pr_title="Test PR",
            context=PRContext(repo_name="test/repo", pr_title="Test PR"),
            intent=IntentProfile(repo_purpose="Test"),
            findings=[
                Finding(
                    title="Test finding",
                    category=FindingCategory.COMMAND_INJECTION,
                    severity=Severity.HIGH,
                    confidence=0.85,
                    affected_files=["test.py"],
                    reviewing_agents=["claude"],
                ),
            ],
            agents_used=["claude"],
        )

    def test_markdown_report(self):
        report = self._build_mock_report()
        md = generate_markdown_report(report)
        assert "CrossFire Security Review" in md
        assert "Test finding" in md

    def test_json_report(self):
        report = self._build_mock_report()
        js = generate_json_report(report)
        data = json.loads(js)
        assert data["repo_name"] == "test/repo"
        assert len(data["findings"]) == 1

    def test_sarif_report(self):
        report = self._build_mock_report()
        sarif = generate_sarif_report(report)
        data = json.loads(sarif)
        assert data["version"] == "2.1.0"
        assert len(data["runs"][0]["results"]) == 1


class TestConfigLoading:
    """Test that config loading works."""

    def test_default_config_loads(self):
        settings = load_settings()
        assert "claude" in settings.agents
        assert settings.analysis.context_depth == "deep"

    def test_cli_overrides(self):
        settings = load_settings(cli_overrides={"analysis": {"context_depth": "shallow"}})
        assert settings.analysis.context_depth == "shallow"
