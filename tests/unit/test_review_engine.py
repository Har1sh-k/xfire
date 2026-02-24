"""Tests for the review engine — agent dispatch and response parsing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crossfire.agents.base import AgentError
from crossfire.agents.review_engine import (
    ReviewEngine,
    _create_agent,
    _parse_enum_flexible,
    _parse_finding_from_raw,
)
from crossfire.config.settings import AgentConfig, CrossFireSettings
from crossfire.core.models import (
    BlastRadius,
    Exploitability,
    FindingCategory,
    IntentProfile,
    PRContext,
    Severity,
)


class TestParseEnumFlexible:
    def test_exact_value(self):
        assert _parse_enum_flexible(Severity, "Critical", Severity.MEDIUM) == Severity.CRITICAL

    def test_lowercase(self):
        assert _parse_enum_flexible(Severity, "critical", Severity.MEDIUM) == Severity.CRITICAL

    def test_uppercase(self):
        assert _parse_enum_flexible(Severity, "CRITICAL", Severity.MEDIUM) == Severity.CRITICAL

    def test_mixed_case(self):
        assert _parse_enum_flexible(Severity, "hIgH", Severity.MEDIUM) == Severity.HIGH

    def test_default_on_invalid(self):
        assert _parse_enum_flexible(Severity, "nonsense", Severity.MEDIUM) == Severity.MEDIUM

    def test_finding_category_case_insensitive(self):
        assert _parse_enum_flexible(
            FindingCategory, "sql_injection", FindingCategory.MISSING_VALIDATION,
        ) == FindingCategory.SQL_INJECTION

    def test_exploitability(self):
        assert _parse_enum_flexible(
            Exploitability, "likely", Exploitability.POSSIBLE,
        ) == Exploitability.LIKELY

    def test_blast_radius(self):
        assert _parse_enum_flexible(
            BlastRadius, "system", BlastRadius.COMPONENT,
        ) == BlastRadius.SYSTEM

    def test_whitespace_stripped(self):
        assert _parse_enum_flexible(Severity, "  High  ", Severity.MEDIUM) == Severity.HIGH

    def test_empty_string_returns_default(self):
        assert _parse_enum_flexible(Severity, "", Severity.MEDIUM) == Severity.MEDIUM


class TestParseFindingFromRaw:
    def test_minimal_finding(self):
        raw = {"title": "SQL Injection in login"}
        finding = _parse_finding_from_raw(raw, "claude")
        assert finding is not None
        assert finding.title == "SQL Injection in login"
        assert finding.reviewing_agents == ["claude"]

    def test_full_finding(self):
        raw = {
            "title": "Auth bypass via header",
            "category": "AUTH_BYPASS",
            "severity": "Critical",
            "confidence": 0.9,
            "exploitability": "Proven",
            "blast_radius": "System",
            "affected_files": ["auth/handler.py"],
            "evidence": [
                {
                    "type": "code_reading",
                    "description": "Missing auth check",
                    "file": "auth/handler.py",
                    "code": "if True: pass",
                },
            ],
            "rationale": "Auth check removed in refactor",
            "mitigations": ["Add auth middleware"],
        }
        finding = _parse_finding_from_raw(raw, "gemini")
        assert finding is not None
        assert finding.category == FindingCategory.AUTH_BYPASS
        assert finding.severity == Severity.CRITICAL
        assert finding.exploitability == Exploitability.PROVEN
        assert finding.blast_radius == BlastRadius.SYSTEM
        assert finding.confidence == 0.9
        assert len(finding.evidence) == 1
        assert finding.rationale_summary == "Auth check removed in refactor"

    def test_case_insensitive_category(self):
        raw = {"title": "Test", "category": "command_injection", "severity": "high"}
        finding = _parse_finding_from_raw(raw, "codex")
        assert finding is not None
        assert finding.category == FindingCategory.COMMAND_INJECTION
        assert finding.severity == Severity.HIGH

    def test_unknown_category_defaults(self):
        raw = {"title": "Test", "category": "TOTALLY_FAKE"}
        finding = _parse_finding_from_raw(raw, "claude")
        assert finding is not None
        assert finding.category == FindingCategory.MISSING_VALIDATION

    def test_line_ranges_parsed(self):
        raw = {
            "title": "Issue",
            "affected_files": ["main.py"],
            "line_ranges": ["10-20", "30-40"],
        }
        finding = _parse_finding_from_raw(raw, "claude")
        assert finding is not None
        assert len(finding.line_ranges) == 2
        assert finding.line_ranges[0].start_line == 10
        assert finding.line_ranges[0].end_line == 20

    def test_purpose_assessment_parsed(self):
        raw = {
            "title": "Exec call",
            "purpose_aware": {
                "is_intended": True,
                "trust_boundary_violated": False,
                "controls_present": True,
                "assessment": "Intended capability with sandbox",
            },
        }
        finding = _parse_finding_from_raw(raw, "claude")
        assert finding is not None
        assert finding.purpose_aware_assessment.is_intended_capability is True
        assert finding.purpose_aware_assessment.isolation_controls_present is True


# ─── Agent Creation Tests ────────────────────────────────────────────────────


class TestCreateAgent:
    def test_claude(self):
        config = AgentConfig(enabled=True, cli_command="claude")
        agent = _create_agent("claude", config)
        assert agent.name == "claude"

    def test_codex(self):
        config = AgentConfig(enabled=True, cli_command="codex")
        agent = _create_agent("codex", config)
        assert agent.name == "codex"

    def test_gemini(self):
        config = AgentConfig(enabled=True, cli_command="gemini")
        agent = _create_agent("gemini", config)
        assert agent.name == "gemini"

    def test_unknown_raises(self):
        config = AgentConfig(enabled=True)
        with pytest.raises(ValueError, match="Unknown agent"):
            _create_agent("gpt4", config)


# ─── Run Independent Reviews Tests ──────────────────────────────────────────


class TestRunIndependentReviews:
    @pytest.mark.asyncio
    async def test_returns_reviews(self):
        """Successful agent call returns parsed reviews."""
        settings = CrossFireSettings(
            agents={"claude": AgentConfig(enabled=True, cli_command="claude")},
        )
        engine = ReviewEngine(settings)
        mock_response = '{"findings": [], "overall_risk": "none"}'
        with patch(
            "crossfire.agents.review_engine._create_agent"
        ) as mock_create:
            mock_agent = MagicMock()
            mock_agent.name = "claude"
            mock_agent.execute = AsyncMock(return_value=mock_response)
            mock_agent.parse_json_response.return_value = {
                "findings": [],
                "overall_risk": "none",
            }
            mock_create.return_value = mock_agent
            reviews = await engine.run_independent_reviews(
                PRContext(repo_name="test/repo", pr_title="Test"),
                IntentProfile(),
                {},
            )
        assert len(reviews) == 1
        assert reviews[0].agent_name == "claude"

    @pytest.mark.asyncio
    async def test_handles_failure(self):
        """Agent that raises AgentError still returns remaining reviews."""
        settings = CrossFireSettings(
            agents={
                "claude": AgentConfig(enabled=True, cli_command="claude"),
                "codex": AgentConfig(enabled=True, cli_command="codex"),
            },
        )
        engine = ReviewEngine(settings)
        with patch(
            "crossfire.agents.review_engine._create_agent"
        ) as mock_create:
            mock_claude = MagicMock()
            mock_claude.name = "claude"
            mock_claude.execute = AsyncMock(side_effect=AgentError("claude", "timeout"))

            mock_codex = MagicMock()
            mock_codex.name = "codex"
            mock_codex.execute = AsyncMock(return_value='{"findings":[]}')
            mock_codex.parse_json_response.return_value = {"findings": []}

            mock_create.side_effect = [mock_claude, mock_codex]
            reviews = await engine.run_independent_reviews(
                PRContext(repo_name="test/repo", pr_title="Test"),
                IntentProfile(),
                {},
            )
        # claude failed, codex succeeded → 1 review
        assert len(reviews) == 1
        assert reviews[0].agent_name == "codex"

    @pytest.mark.asyncio
    async def test_no_enabled_agents(self):
        """Returns empty list when no agents are enabled."""
        settings = CrossFireSettings(
            agents={"claude": AgentConfig(enabled=False)},
        )
        engine = ReviewEngine(settings)
        reviews = await engine.run_independent_reviews(
            PRContext(repo_name="test/repo", pr_title="Test"),
            IntentProfile(),
            {},
        )
        assert reviews == []
