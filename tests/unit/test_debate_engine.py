"""Tests for the debate engine — role assignment, formatting, routing, and budget."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xfire.agents.base import AgentError
from xfire.agents.debate_engine import (
    DebateEngine,
    _format_context_summary,
    _format_evidence_text,
    _format_finding_summary,
    _format_intent_summary,
    _parse_agent_argument,
)
from xfire.config.settings import AgentConfig, CrossFireSettings, DebateConfig
from xfire.core.finding_synthesizer import (
    FindingSynthesizer,
    compute_debate_budget,
    merge_severity,
)
from xfire.core.models import (
    AgentArgument,
    AgentReview,
    ConsensusOutcome,
    DebateRecord,
    DebateTag,
    Evidence,
    Finding,
    FindingCategory,
    FindingStatus,
    IntentProfile,
    LineRange,
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
    **debate_kwargs,
) -> CrossFireSettings:
    if agents is None:
        agents = {
            "claude": AgentConfig(enabled=True, cli_command="claude"),
            "codex": AgentConfig(enabled=True, cli_command="codex"),
            "gemini": AgentConfig(enabled=True, cli_command="gemini"),
        }
    return CrossFireSettings(
        agents=agents,
        debate=DebateConfig(role_assignment=role_assignment, **debate_kwargs),
    )


def _make_review(agent: str, findings: list[Finding]) -> AgentReview:
    return AgentReview(agent_name=agent, findings=findings)


# ─── Formatting Tests ────────────────────────────────────────────────────────


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


# ─── Legacy Role Assignment Tests ────────────────────────────────────────────


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


# ─── Evidence-Driven Role Assignment Tests ───────────────────────────────────


class TestEvidenceDrivenRoles:
    """Test the new evidence-driven role assignment where
    prosecutor=finder, defense=misser, judge=remaining."""

    def test_one_finder_two_missers(self):
        """Claude found it, codex and gemini missed → claude prosecutes,
        codex defends (highest pref), gemini judges."""
        settings = _make_settings(role_assignment="evidence")
        engine = DebateEngine(settings)
        finding = _make_finding(reviewing_agents=["claude"])
        p, d, j = engine._assign_roles(finding)
        assert p == "claude"
        assert d == "codex"  # defense_preference: codex > claude > gemini
        assert j == "gemini"

    def test_two_finders_one_misser(self):
        """Claude and codex found it, gemini missed → gemini defends,
        judge by preference from finders."""
        settings = _make_settings(role_assignment="evidence")
        engine = DebateEngine(settings)
        finding = _make_finding(reviewing_agents=["claude", "codex"])
        p, d, j = engine._assign_roles(finding)
        assert p == "claude"  # first finder
        assert d == "gemini"  # only misser
        # Judge from remaining enabled agents not already assigned
        assert j == "codex"

    def test_defense_preference_order(self):
        """When gemini found it, claude and codex missed →
        codex defends (codex > claude in defense_preference)."""
        settings = _make_settings(role_assignment="evidence")
        engine = DebateEngine(settings)
        finding = _make_finding(reviewing_agents=["gemini"])
        p, d, j = engine._assign_roles(finding)
        assert p == "gemini"
        assert d == "codex"  # codex > claude in defense pref
        assert j == "claude"

    def test_custom_defense_preference(self):
        """Custom defense preference should be respected."""
        settings = _make_settings(
            role_assignment="evidence",
            defense_preference=["gemini", "codex", "claude"],
        )
        engine = DebateEngine(settings)
        finding = _make_finding(reviewing_agents=["claude"])
        p, d, j = engine._assign_roles(finding)
        assert p == "claude"
        assert d == "gemini"  # gemini > codex in custom pref
        assert j == "codex"

    def test_custom_judge_preference(self):
        """Custom judge preference should be respected."""
        settings = _make_settings(
            role_assignment="evidence",
            judge_preference=["claude", "codex", "gemini"],
        )
        engine = DebateEngine(settings)
        finding = _make_finding(reviewing_agents=["claude", "codex"])
        p, d, j = engine._assign_roles(finding)
        assert p == "claude"
        assert d == "gemini"  # only misser
        # Judge from finders by judge_preference: claude > codex, but claude=prosecutor
        assert j == "codex"

    def test_two_agent_mode(self):
        """With only 2 agents, no real judge available."""
        agents = {
            "claude": AgentConfig(enabled=True),
            "codex": AgentConfig(enabled=True),
        }
        settings = _make_settings(agents=agents, role_assignment="evidence")
        engine = DebateEngine(settings)
        finding = _make_finding(reviewing_agents=["claude"])
        p, d, j = engine._assign_roles(finding)
        assert p == "claude"
        assert d == "codex"
        # Judge defaults to defense in 2-agent mode
        assert j == "codex"

    def test_falls_back_to_rotation_without_finding(self):
        """Evidence mode without a finding falls back to rotation."""
        settings = _make_settings(role_assignment="evidence")
        engine = DebateEngine(settings)
        p, d, j = engine._assign_roles()  # no finding
        # Should use rotation fallback
        assert len({p, d, j}) == 3


# ─── Defense Concedes Detection Tests ────────────────────────────────────────


class TestDefenseConcedes:
    """Test detection of whether defense agrees with prosecution."""

    def test_real_issue_concedes(self):
        arg = AgentArgument(
            agent_name="codex", role="defense",
            position="real_issue", argument="I agree", confidence=0.8,
        )
        assert DebateEngine._defense_concedes(arg) is True

    def test_confirmed_concedes(self):
        arg = AgentArgument(
            agent_name="codex", role="defense",
            position="confirmed", argument="This is real", confidence=0.9,
        )
        assert DebateEngine._defense_concedes(arg) is True

    def test_false_positive_does_not_concede(self):
        arg = AgentArgument(
            agent_name="codex", role="defense",
            position="false_positive", argument="Not a real issue", confidence=0.7,
        )
        assert DebateEngine._defense_concedes(arg) is False

    def test_mitigated_does_not_concede(self):
        arg = AgentArgument(
            agent_name="codex", role="defense",
            position="mitigated", argument="Controls exist", confidence=0.6,
        )
        assert DebateEngine._defense_concedes(arg) is False

    def test_case_insensitive(self):
        arg = AgentArgument(
            agent_name="codex", role="defense",
            position="Real_Issue", argument="Yes", confidence=0.8,
        )
        assert DebateEngine._defense_concedes(arg) is True


# ─── Debate Budget Tests ────────────────────────────────────────────────────


class TestDebateBudget:
    """Test budget computation based on PR size."""

    def test_tiny_pr(self):
        assert compute_debate_budget(5) == 2

    def test_small_pr(self):
        assert compute_debate_budget(20) == 2

    def test_medium_pr(self):
        assert compute_debate_budget(50) == 6

    def test_large_pr(self):
        assert compute_debate_budget(100) == 6

    def test_very_large_pr(self):
        assert compute_debate_budget(300) == 12

    def test_huge_pr(self):
        assert compute_debate_budget(1000) == 20

    def test_boundary_20(self):
        assert compute_debate_budget(20) == 2
        assert compute_debate_budget(21) == 6

    def test_boundary_100(self):
        assert compute_debate_budget(100) == 6
        assert compute_debate_budget(101) == 12


# ─── Severity Merge Tests ────────────────────────────────────────────────────


class TestSeverityMerge:
    """Test deterministic severity merge rule."""

    def test_critical_wins(self):
        """If any agent rated Critical, result is Critical."""
        result = merge_severity([Severity.CRITICAL, Severity.LOW, Severity.LOW])
        assert result == Severity.CRITICAL

    def test_median_without_critical(self):
        """Without Critical, take median."""
        result = merge_severity([Severity.HIGH, Severity.MEDIUM, Severity.LOW])
        assert result == Severity.MEDIUM

    def test_two_high_one_low(self):
        result = merge_severity([Severity.HIGH, Severity.HIGH, Severity.LOW])
        assert result == Severity.HIGH

    def test_all_same(self):
        result = merge_severity([Severity.MEDIUM, Severity.MEDIUM, Severity.MEDIUM])
        assert result == Severity.MEDIUM

    def test_two_agents(self):
        result = merge_severity([Severity.HIGH, Severity.LOW])
        assert result == Severity.HIGH  # median of 2 = index 1

    def test_single_agent(self):
        result = merge_severity([Severity.LOW])
        assert result == Severity.LOW


# ─── Silent Dissent Tests ────────────────────────────────────────────────────


class TestSilentDissent:
    """Test silent dissent detection in the synthesizer."""

    def test_no_dissent_when_agent_did_not_analyze_area(self):
        """Missing agent with no overlapping findings → no dissent."""
        synth = FindingSynthesizer()
        finding = _make_finding(
            reviewing_agents=["claude", "codex"],
            affected_files=["auth/login.py"],
            line_ranges=[LineRange(file_path="auth/login.py", start_line=10, end_line=20)],
        )
        reviews = [
            _make_review("claude", [finding]),
            _make_review("codex", [_make_finding(
                reviewing_agents=["codex"],
                line_ranges=[LineRange(file_path="auth/login.py", start_line=10, end_line=20)],
            )]),
            _make_review("gemini", []),  # gemini ran but found nothing here
        ]
        has_dissent = synth._check_silent_dissent(finding, ["gemini"], reviews)
        assert has_dissent is False

    def test_dissent_when_agent_rejected_overlapping_finding(self):
        """Missing agent rejected a finding in the same area → dissent."""
        synth = FindingSynthesizer()
        finding = _make_finding(
            reviewing_agents=["claude"],
            affected_files=["auth/login.py"],
            line_ranges=[LineRange(file_path="auth/login.py", start_line=10, end_line=20)],
        )
        rejected_finding = _make_finding(
            status=FindingStatus.REJECTED,
            affected_files=["auth/login.py"],
            line_ranges=[LineRange(file_path="auth/login.py", start_line=15, end_line=25)],
        )
        reviews = [
            _make_review("claude", [finding]),
            _make_review("gemini", [rejected_finding]),
        ]
        has_dissent = synth._check_silent_dissent(finding, ["gemini"], reviews)
        assert has_dissent is True

    def test_dissent_when_file_mentioned_in_risk_assessment(self):
        """Missing agent mentions the file in risk assessment → dissent."""
        synth = FindingSynthesizer()
        finding = _make_finding(
            reviewing_agents=["claude"],
            affected_files=["auth/login.py"],
        )
        review = AgentReview(
            agent_name="gemini",
            findings=[],
            overall_risk_assessment="Reviewed auth/login.py — no issues found",
        )
        reviews = [_make_review("claude", [finding]), review]
        has_dissent = synth._check_silent_dissent(finding, ["gemini"], reviews)
        assert has_dissent is True


# ─── Routing Table Tests ─────────────────────────────────────────────────────


class TestRoutingTable:
    """Test the evidence-driven debate routing table."""

    def test_all_three_agents_found_auto_confirmed(self):
        """All 3 agents found the same issue → auto_confirmed."""
        synth = FindingSynthesizer()
        lr = [LineRange(file_path="app.py", start_line=10, end_line=15)]
        reviews = [
            _make_review("claude", [_make_finding(reviewing_agents=["claude"], line_ranges=lr)]),
            _make_review("codex", [_make_finding(reviewing_agents=["codex"], line_ranges=lr)]),
            _make_review("gemini", [_make_finding(reviewing_agents=["gemini"], line_ranges=lr)]),
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        assert result[0].debate_tag == DebateTag.AUTO_CONFIRMED
        assert result[0].status == FindingStatus.CONFIRMED

    def test_two_of_three_no_dissent_auto_confirmed(self):
        """2 of 3 agents found it, no silent dissent → auto_confirmed, LIKELY."""
        synth = FindingSynthesizer()
        lr = [LineRange(file_path="app.py", start_line=10, end_line=15)]
        reviews = [
            _make_review("claude", [_make_finding(reviewing_agents=["claude"], line_ranges=lr)]),
            _make_review("codex", [_make_finding(reviewing_agents=["codex"], line_ranges=lr)]),
            _make_review("gemini", []),  # gemini ran, found nothing, no dissent
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        assert result[0].debate_tag == DebateTag.AUTO_CONFIRMED
        assert result[0].status == FindingStatus.LIKELY

    def test_two_of_three_with_dissent_needs_debate(self):
        """2 of 3 found, missing agent explicitly rejected same area → needs_debate."""
        synth = FindingSynthesizer()
        lr = [LineRange(file_path="auth/login.py", start_line=10, end_line=15)]
        rejected = _make_finding(
            status=FindingStatus.REJECTED,
            affected_files=["auth/login.py"],
            reviewing_agents=[],
            line_ranges=[LineRange(file_path="auth/login.py", start_line=12, end_line=18)],
        )
        reviews = [
            _make_review("claude", [_make_finding(reviewing_agents=["claude"], line_ranges=lr)]),
            _make_review("codex", [_make_finding(reviewing_agents=["codex"], line_ranges=lr)]),
            _make_review("gemini", [rejected]),
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        assert result[0].debate_tag == DebateTag.NEEDS_DEBATE

    def test_one_of_three_needs_debate(self):
        """1 of 3 found → needs_debate."""
        synth = FindingSynthesizer()
        reviews = [
            _make_review("claude", [_make_finding(reviewing_agents=["claude"])]),
            _make_review("codex", []),
            _make_review("gemini", []),
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        assert result[0].debate_tag == DebateTag.NEEDS_DEBATE

    def test_one_agent_mode_informational(self):
        """Only 1 agent ran → informational."""
        synth = FindingSynthesizer()
        reviews = [
            _make_review("claude", [_make_finding(reviewing_agents=["claude"])]),
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        assert result[0].debate_tag == DebateTag.INFORMATIONAL
        assert result[0].status == FindingStatus.UNCLEAR

    def test_two_agent_mode_both_found(self):
        """2-agent mode, both found → auto_confirmed."""
        synth = FindingSynthesizer()
        lr = [LineRange(file_path="app.py", start_line=10, end_line=15)]
        reviews = [
            _make_review("claude", [_make_finding(reviewing_agents=["claude"], line_ranges=lr)]),
            _make_review("codex", [_make_finding(reviewing_agents=["codex"], line_ranges=lr)]),
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        assert result[0].debate_tag == DebateTag.AUTO_CONFIRMED

    def test_two_agent_mode_one_found_needs_debate(self):
        """2-agent mode, 1 found 1 missed → needs_debate."""
        synth = FindingSynthesizer()
        reviews = [
            _make_review("claude", [_make_finding(reviewing_agents=["claude"])]),
            _make_review("codex", []),
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert len(result) == 1
        assert result[0].debate_tag == DebateTag.NEEDS_DEBATE


# ─── Pick By Preference Tests ────────────────────────────────────────────────


class TestPickByPreference:
    def test_picks_first_match(self):
        result = DebateEngine._pick_by_preference(
            ["codex", "claude", "gemini"], ["claude", "gemini"],
        )
        assert result == "claude"

    def test_picks_first_candidate_when_no_pref_match(self):
        result = DebateEngine._pick_by_preference(
            ["codex"], ["claude", "gemini"],
        )
        assert result == "claude"

    def test_returns_none_for_empty_candidates(self):
        result = DebateEngine._pick_by_preference(["codex"], [])
        assert result is None


# ─── Debate All Tests ────────────────────────────────────────────────────────


class TestDebateAll:
    @pytest.mark.asyncio
    async def test_budget_exhaustion(self):
        """Findings beyond the budget are skipped with UNCLEAR status."""
        settings = _make_settings(role_assignment="evidence")
        engine = DebateEngine(settings)

        findings = [
            _make_finding(
                title=f"Finding {i}",
                reviewing_agents=["claude"],
            )
            for i in range(3)
        ]

        # Mock _debate_single to consume 2 rounds each time
        debate_record = DebateRecord(
            finding_id="x",
            prosecutor_argument=AgentArgument(
                agent_name="claude", role="prosecutor",
                position="real_issue", argument="yes", confidence=0.8,
            ),
            defense_argument=AgentArgument(
                agent_name="codex", role="defense",
                position="false_positive", argument="no", confidence=0.4,
            ),
            judge_ruling=AgentArgument(
                agent_name="gemini", role="judge",
                position="Confirmed", argument="agreed", confidence=0.8,
            ),
            rounds_used=2,
            consensus=ConsensusOutcome.CONFIRMED,
        )

        with patch.object(engine, "_debate_single", new_callable=AsyncMock, return_value=debate_record):
            results = await engine.debate_all(
                findings=findings,
                context=PRContext(repo_name="test/repo", pr_title="Test"),
                intent=IntentProfile(),
                debate_budget=3,  # only enough for ~1.5 debates
            )

        # First finding debated (2 rounds), second debated would exceed budget
        assert len(results) >= 1
        # At least one finding should be marked budget-exhausted
        exhausted = [f for f in findings if f.debate_summary and "budget" in f.debate_summary.lower()]
        assert len(exhausted) >= 1

    @pytest.mark.asyncio
    async def test_debate_error_handled(self):
        """A debate that raises an exception marks the finding UNCLEAR."""
        settings = _make_settings(role_assignment="evidence")
        engine = DebateEngine(settings)

        finding = _make_finding(reviewing_agents=["claude"])

        with patch.object(engine, "_debate_single", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            results = await engine.debate_all(
                findings=[finding],
                context=PRContext(repo_name="test/repo", pr_title="Test"),
                intent=IntentProfile(),
                debate_budget=10,
            )

        assert results == []
        assert finding.status == FindingStatus.UNCLEAR


# ─── Apply Debate Result Tests ───────────────────────────────────────────────


class TestApplyDebateResult:
    def test_confirmed(self):
        settings = _make_settings()
        engine = DebateEngine(settings)
        finding = _make_finding()
        debate = DebateRecord(
            finding_id=finding.id,
            prosecutor_argument=AgentArgument(
                agent_name="claude", role="prosecutor",
                position="real_issue", argument="yes", confidence=0.9,
            ),
            defense_argument=AgentArgument(
                agent_name="codex", role="defense",
                position="false_positive", argument="no", confidence=0.3,
            ),
            judge_ruling=AgentArgument(
                agent_name="gemini", role="judge",
                position="Confirmed", argument="agreed", confidence=0.9,
            ),
            rounds_used=1,
            consensus=ConsensusOutcome.CONFIRMED,
            final_severity=Severity.HIGH,
            final_confidence=0.9,
        )
        engine._apply_debate_result(finding, debate)
        assert finding.status == FindingStatus.CONFIRMED
        assert finding.confidence == 0.9
        assert finding.consensus_outcome == "Confirmed"

    def test_rejected(self):
        settings = _make_settings()
        engine = DebateEngine(settings)
        finding = _make_finding()
        debate = DebateRecord(
            finding_id=finding.id,
            prosecutor_argument=AgentArgument(
                agent_name="claude", role="prosecutor",
                position="real_issue", argument="yes", confidence=0.4,
            ),
            defense_argument=AgentArgument(
                agent_name="codex", role="defense",
                position="false_positive", argument="no", confidence=0.9,
            ),
            judge_ruling=AgentArgument(
                agent_name="gemini", role="judge",
                position="Rejected", argument="not real", confidence=0.8,
            ),
            rounds_used=1,
            consensus=ConsensusOutcome.REJECTED,
            final_severity=Severity.LOW,
            final_confidence=0.2,
        )
        engine._apply_debate_result(finding, debate)
        assert finding.status == FindingStatus.REJECTED
        assert finding.confidence == 0.2
        assert finding.consensus_outcome == "Rejected"
