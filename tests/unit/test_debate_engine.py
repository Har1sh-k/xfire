"""Tests for the debate engine — role assignment and formatting."""

import pytest

from crossfire.agents.base import AgentError
from crossfire.agents.debate_engine import (
    DebateEngine,
    _format_context_summary,
    _format_evidence_text,
    _format_finding_summary,
    _format_intent_summary,
    _parse_agent_argument,
)
from crossfire.config.settings import AgentConfig, CrossFireSettings, DebateConfig
from crossfire.core.models import (
    Evidence,
    Finding,
    FindingCategory,
    IntentProfile,
    PRContext,
    SecurityControl,
    Severity,
    TrustBoundary,
)


def _make_finding(**kwargs) -> Finding:
    defaults = dict(
        title="SQL Injection in login",
        category=FindingCategory.SQL_INJECTION,
        severity=Severity.HIGH,
        confidence=0.85,
        affected_files=["auth/login.py"],
    )
    defaults.update(kwargs)
    return Finding(**defaults)


def _make_settings(
    agents: dict | None = None,
    role_assignment: str = "rotate",
) -> CrossFireSettings:
    if agents is None:
        agents = {
            "claude": AgentConfig(enabled=True, cli_command="claude"),
            "codex": AgentConfig(enabled=True, cli_command="codex"),
            "gemini": AgentConfig(enabled=True, cli_command="gemini"),
        }
    return CrossFireSettings(
        agents=agents,
        debate=DebateConfig(role_assignment=role_assignment),
    )


class TestFormatFindingSummary:
    def test_basic_format(self):
        finding = _make_finding()
        summary = _format_finding_summary(finding)
        assert "SQL Injection in login" in summary
        assert "SQL_INJECTION" in summary
        assert "High" in summary
        assert "auth/login.py" in summary

    def test_with_data_flow(self):
        finding = _make_finding(data_flow_trace="request.body -> cursor.execute()")
        summary = _format_finding_summary(finding)
        assert "request.body -> cursor.execute()" in summary

    def test_with_rationale(self):
        finding = _make_finding(rationale_summary="Missing parameterization")
        summary = _format_finding_summary(finding)
        assert "Missing parameterization" in summary


class TestFormatEvidenceText:
    def test_no_evidence(self):
        finding = _make_finding()
        text = _format_evidence_text(finding)
        assert text == "No evidence collected."

    def test_with_evidence(self):
        finding = _make_finding(evidence=[
            Evidence(
                source="claude",
                evidence_type="code_reading",
                description="Unsanitized input",
                file_path="auth/login.py",
                code_snippet="cursor.execute(query)",
            ),
        ])
        text = _format_evidence_text(finding)
        assert "code_reading" in text
        assert "Unsanitized input" in text
        assert "auth/login.py" in text
        assert "cursor.execute(query)" in text


class TestFormatIntentSummary:
    def test_basic(self):
        intent = IntentProfile(
            repo_purpose="Web API backend",
            intended_capabilities=["web_server", "database_access"],
            pr_intent="feature",
        )
        summary = _format_intent_summary(intent)
        assert "Web API backend" in summary
        assert "web_server" in summary
        assert "feature" in summary

    def test_with_trust_boundaries(self):
        intent = IntentProfile(
            repo_purpose="API",
            pr_intent="bugfix",
            trust_boundaries=[
                TrustBoundary(name="HTTP", description="Untrusted HTTP input"),
            ],
        )
        summary = _format_intent_summary(intent)
        assert "HTTP" in summary
        assert "Untrusted HTTP input" in summary

    def test_with_security_controls(self):
        intent = IntentProfile(
            repo_purpose="API",
            pr_intent="feature",
            security_controls_detected=[
                SecurityControl(
                    control_type="auth_decorator",
                    location="auth/middleware.py",
                    description="Auth decorator found",
                ),
            ],
        )
        summary = _format_intent_summary(intent)
        assert "auth_decorator" in summary


class TestFormatContextSummary:
    def test_basic(self):
        context = PRContext(
            repo_name="org/repo",
            pr_title="Fix auth bypass",
            files=[],
        )
        summary = _format_context_summary(context)
        assert "org/repo" in summary
        assert "Fix auth bypass" in summary


class TestAssignRoles:
    def test_rotation_three_agents(self):
        settings = _make_settings()
        engine = DebateEngine(settings)
        p, d, j = engine._assign_roles()
        # All should be different agents
        assert len({p, d, j}) == 3

    def test_rotation_advances(self):
        settings = _make_settings()
        engine = DebateEngine(settings)
        roles1 = engine._assign_roles()
        roles2 = engine._assign_roles()
        # Roles should rotate
        assert roles1 != roles2

    def test_fixed_roles(self):
        settings = _make_settings(role_assignment="fixed")
        settings.debate.fixed_roles = {
            "prosecutor": "claude",
            "defense": "codex",
            "judge": "gemini",
        }
        engine = DebateEngine(settings)
        p, d, j = engine._assign_roles()
        assert p == "claude"
        assert d == "codex"
        assert j == "gemini"

    def test_fixed_roles_fallback_to_rotation(self):
        agents = {
            "claude": AgentConfig(enabled=True),
            "codex": AgentConfig(enabled=True),
        }
        settings = _make_settings(agents=agents, role_assignment="fixed")
        settings.debate.fixed_roles = {
            "prosecutor": "claude",
            "defense": "codex",
            "judge": "gemini",  # not available
        }
        engine = DebateEngine(settings)
        p, d, j = engine._assign_roles()
        # Should fall back to rotation with available agents
        assert p in ("claude", "codex")

    def test_no_agents_raises(self):
        settings = _make_settings(agents={})
        engine = DebateEngine(settings)
        with pytest.raises(AgentError, match="No agents"):
            engine._assign_roles()

    def test_two_agents_fills_three_roles(self):
        agents = {
            "claude": AgentConfig(enabled=True),
            "codex": AgentConfig(enabled=True),
        }
        settings = _make_settings(agents=agents)
        engine = DebateEngine(settings)
        p, d, j = engine._assign_roles()
        # With 2 agents, one must fill two roles
        assert p in ("claude", "codex")
        assert d in ("claude", "codex")
        assert j in ("claude", "codex")
