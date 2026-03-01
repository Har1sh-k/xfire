"""Tests for CLI helper functions."""

from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from xfire.cli import (
    _check_severity_gate,
    _default_config_yaml,
    _handle_error,
    _output_report,
    _parse_agents_list,
    _preflight_check,
    app,
)
from xfire.config.settings import CrossFireSettings, SeverityGateConfig, load_settings
from xfire.core.models import (
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

    def test_markdown_write_unicode_emoji(self, tmp_path):
        """Regression: Windows CP1252 UnicodeEncodeError when report contains emoji."""
        finding = Finding(
            title="🔥 SQL Injection",
            description="User input reaches DB 🚨",
            severity=Severity.HIGH,
            category=FindingCategory.SQL_INJECTION,
            status=FindingStatus.CONFIRMED,
            file_path="app.py",
            confidence=0.9,
        )
        report = _make_report(findings=[finding])
        out_file = tmp_path / "report.md"
        # Must not raise UnicodeEncodeError on Windows
        _output_report(report, "markdown", str(out_file), False)
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "SQL Injection" in content


class TestPrintJudgeQuestions:
    """Regression: 'name Rule is not defined' in _print_judge_questions."""

    def test_renders_without_error(self):
        from io import StringIO
        from rich.console import Console
        from xfire.cli_ui import HackerUI

        buf = StringIO()
        console = Console(file=buf, highlight=False)
        ui = HackerUI(show_debate=True, console=console)
        # Must not raise NameError for Rule
        ui._print_judge_questions({
            "agent": "claude",
            "questions": "1. Is the check on line 42 sufficient?\n2. Can token be None here?",
        })
        output = buf.getvalue()
        assert "judge questions" in output.lower()

    def test_skips_empty_questions(self):
        from io import StringIO
        from rich.console import Console
        from xfire.cli_ui import HackerUI

        buf = StringIO()
        console = Console(file=buf, highlight=False)
        ui = HackerUI(show_debate=True, console=console)
        ui._print_judge_questions({"agent": "claude", "questions": "   "})
        assert buf.getvalue() == ""


class TestDefaultConfigYaml:
    def test_contains_expected_sections(self):
        yaml_text = _default_config_yaml()
        assert "repo:" in yaml_text
        assert "agents:" in yaml_text
        assert "severity_gate:" in yaml_text


# ─── CLI Command Tests ──────────────────────────────────────────────────────

runner = CliRunner()


class TestCliInit:
    def test_creates_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        config_file = tmp_path / ".xfire" / "config.yaml"
        assert config_file.exists()
        assert "agents:" in config_file.read_text()

    def test_existing_config_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".xfire"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("existing: true\n")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "already exists" in result.stdout
        # Should not overwrite
        assert (config_dir / "config.yaml").read_text() == "existing: true\n"


class TestCliConfigCheck:
    def test_valid_config(self):
        """config-check with default settings (no config file) should succeed."""
        result = runner.invoke(app, ["config-check"])
        assert result.exit_code == 0
        assert "valid" in result.stdout.lower()

class TestAuthCommands:
    def test_auth_status_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0
        assert "claude" in result.stdout
        assert "codex" in result.stdout
        assert "gemini" in result.stdout

    def test_auth_login_claude_token(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["auth", "login", "--provider", "claude", "--token", "setup-token-value"],
        )
        assert result.exit_code == 0
        auth_file = tmp_path / ".xfire" / "auth.json"
        assert auth_file.exists()
        assert "setup-token-value" in auth_file.read_text(encoding="utf-8")

    def test_auth_login_invalid_provider(self):
        result = runner.invoke(app, ["auth", "login", "--provider", "invalid"])
        assert result.exit_code == 1
        assert "Unknown provider" in result.stdout


class TestPreflightAuthStore:
    def test_api_preflight_accepts_auth_store(self, tmp_path, monkeypatch):
        import asyncio

        from xfire.auth.store import upsert_claude_setup_token

        monkeypatch.chdir(tmp_path)
        upsert_claude_setup_token("setup-token-value")

        settings = load_settings()
        for name, cfg in settings.agents.items():
            cfg.enabled = name == "claude"
        settings.agents["claude"].mode = "api"

        results = asyncio.run(_preflight_check(settings))
        assert results["claude"][0] is True
        assert "subscription auth" in results["claude"][1]
