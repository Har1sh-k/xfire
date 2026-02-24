"""Tests for CLI helper functions."""

from unittest.mock import MagicMock, patch

import pytest
import typer

from crossfire.cli import (
    _check_severity_gate,
    _default_config_yaml,
    _handle_error,
    _output_report,
    _parse_agents_list,
)
from crossfire.config.settings import CrossFireSettings, SeverityGateConfig
from crossfire.core.models import (
    CrossFireReport,
    Finding,
    FindingCategory,
    FindingStatus,
    PRContext,
    IntentProfile,
    Severity,
)


class TestParseAgentsList:
    def test_none_input(self):
        assert _parse_agents_list(None) is None

    def test_empty_string(self):
        assert _parse_agents_list("") is None

    def test_single_agent(self):
        assert _parse_agents_list("claude") == ["claude"]

    def test_multiple_agents(self):
        result = _parse_agents_list("claude,codex,gemini")
        assert result == ["claude", "codex", "gemini"]

    def test_strips_whitespace(self):
        result = _parse_agents_list("claude , codex , gemini")
        assert result == ["claude", "codex", "gemini"]

    def test_skips_empty_entries(self):
        result = _parse_agents_list("claude,,codex,")
        assert result == ["claude", "codex"]


def _make_report(**kwargs) -> CrossFireReport:
    """Create a minimal CrossFireReport."""
    defaults = {
        "repo_name": "test/repo",
        "context": PRContext(repo_name="test/repo", pr_title="Test"),
        "intent": IntentProfile(),
    }
    defaults.update(kwargs)
    return CrossFireReport(**defaults)


class TestHandleError:
    def test_exits_with_code_1(self):
        with pytest.raises(typer.Exit) as exc_info:
            _handle_error("something broke")
        assert exc_info.value.exit_code == 1

    def test_includes_exception_detail(self, capsys):
        with pytest.raises(typer.Exit):
            _handle_error("oops", ValueError("bad value"))
        captured = capsys.readouterr()
        assert "oops" in captured.out
        assert "ValueError" in captured.out


class TestCheckSeverityGate:
    def test_no_findings_no_exit(self):
        report = _make_report()
        settings = CrossFireSettings()
        _check_severity_gate(report, settings)  # should not raise

    def test_high_finding_triggers_exit(self):
        finding = Finding(
            title="Real issue",
            category=FindingCategory.COMMAND_INJECTION,
            severity=Severity.HIGH,
            confidence=0.9,
            status=FindingStatus.CONFIRMED,
        )
        report = _make_report(findings=[finding])
        settings = CrossFireSettings(
            severity_gate=SeverityGateConfig(fail_on="high", min_confidence=0.7),
        )
        with pytest.raises(typer.Exit) as exc_info:
            _check_severity_gate(report, settings)
        assert exc_info.value.exit_code == 1

    def test_low_finding_below_gate(self):
        finding = Finding(
            title="Minor",
            category=FindingCategory.COMMAND_INJECTION,
            severity=Severity.LOW,
            confidence=0.9,
            status=FindingStatus.CONFIRMED,
        )
        report = _make_report(findings=[finding])
        settings = CrossFireSettings(
            severity_gate=SeverityGateConfig(fail_on="high", min_confidence=0.7),
        )
        _check_severity_gate(report, settings)  # should not raise


class TestOutputReport:
    def test_json_format(self, capsys):
        report = _make_report()
        _output_report(report, "json", None, False)
        captured = capsys.readouterr()
        assert "test/repo" in captured.out

    def test_writes_to_file(self, tmp_path):
        report = _make_report()
        out_file = tmp_path / "report.json"
        _output_report(report, "json", str(out_file), False)
        assert out_file.exists()
        assert "test/repo" in out_file.read_text(encoding="utf-8")


class TestDefaultConfigYaml:
    def test_contains_expected_sections(self):
        yaml_text = _default_config_yaml()
        assert "repo:" in yaml_text
        assert "agents:" in yaml_text
        assert "severity_gate:" in yaml_text
