"""Tests for purpose-aware logic across the pipeline."""

from crossfire.core.finding_synthesizer import FindingSynthesizer
from crossfire.core.intent_inference import IntentInferrer
from crossfire.core.models import (
    AgentReview,
    DiffHunk,
    Evidence,
    FileContext,
    Finding,
    FindingCategory,
    IntentProfile,
    LineRange,
    PRContext,
    PurposeAssessment,
    SecurityControl,
    Severity,
    TrustBoundary,
)


class TestPurposeAwareIntentInference:
    """Test that intent inference correctly identifies intended capabilities."""

    def test_coding_agent_detects_intended_exec(self):
        """A coding agent repo should identify code execution as intended."""
        inferrer = IntentInferrer()
        ctx = PRContext(
            repo_name="test/coding-agent",
            pr_title="Add Python executor",
            readme_content="# CodingBot\n\nAn AI coding agent that executes code in sandboxes.",
            config_files={"requirements.txt": "langchain\ndocker\n"},
            directory_structure="codingbot/\n├── agents/\n├── sandbox/\n│   ├── executor.py\n├── tools/",
        )
        intent = inferrer.infer(ctx)
        assert "llm_powered" in intent.intended_capabilities
        assert "has_isolation" in intent.intended_capabilities
        assert "agent_tool_system" in intent.intended_capabilities

    def test_web_app_does_not_intend_exec(self):
        """A web app should NOT identify code execution as intended."""
        inferrer = IntentInferrer()
        ctx = PRContext(
            repo_name="test/webapp",
            pr_title="Add deploy endpoint",
            readme_content="# MyDashboard\n\nA web dashboard for analytics.",
            config_files={"requirements.txt": "flask\npsycopg2\n"},
            directory_structure="myapp/\n├── api/\n├── templates/\n├── static/",
        )
        intent = inferrer.infer(ctx)
        assert "web_server" in intent.intended_capabilities
        assert "has_isolation" not in intent.intended_capabilities
        assert "agent_tool_system" not in intent.intended_capabilities


class TestPurposeAwareSynthesizer:
    """Test that the synthesizer respects purpose-aware assessments."""

    def test_intended_capability_with_controls_reduced(self):
        """Finding for intended capability with controls should be reduced."""
        synth = FindingSynthesizer()
        finding = Finding(
            title="subprocess.run in executor",
            category=FindingCategory.COMMAND_INJECTION,
            severity=Severity.CRITICAL,
            confidence=0.9,
            affected_files=["sandbox/executor.py"],
            reviewing_agents=["claude"],
            purpose_aware_assessment=PurposeAssessment(
                is_intended_capability=True,
                isolation_controls_present=True,
                assessment="Intended code execution with Docker sandbox",
            ),
        )
        review = AgentReview(agent_name="claude", findings=[finding])
        result = synth.synthesize([review], IntentProfile())
        assert result[0].severity == Severity.MEDIUM  # reduced from Critical

    def test_unintended_capability_not_reduced(self):
        """Finding for unintended capability should NOT be reduced."""
        synth = FindingSynthesizer()
        finding = Finding(
            title="subprocess.run in API endpoint",
            category=FindingCategory.COMMAND_INJECTION,
            severity=Severity.CRITICAL,
            confidence=0.9,
            affected_files=["api/deploy.py"],
            reviewing_agents=["claude"],
            purpose_aware_assessment=PurposeAssessment(
                is_intended_capability=False,
                isolation_controls_present=False,
                assessment="Not intended. No sandbox.",
            ),
        )
        review = AgentReview(agent_name="claude", findings=[finding])
        result = synth.synthesize([review], IntentProfile())
        assert result[0].severity == Severity.CRITICAL  # NOT reduced

    def test_intended_without_controls_not_reduced(self):
        """Intended capability WITHOUT controls should not be reduced."""
        synth = FindingSynthesizer()
        finding = Finding(
            title="subprocess.run in executor",
            category=FindingCategory.COMMAND_INJECTION,
            severity=Severity.CRITICAL,
            confidence=0.9,
            affected_files=["sandbox/executor.py"],
            reviewing_agents=["claude"],
            purpose_aware_assessment=PurposeAssessment(
                is_intended_capability=True,
                isolation_controls_present=False,  # no controls!
                assessment="Intended but no sandbox",
            ),
        )
        review = AgentReview(agent_name="claude", findings=[finding])
        result = synth.synthesize([review], IntentProfile())
        assert result[0].severity == Severity.CRITICAL  # NOT reduced

    def test_sensitive_path_boosts_priority(self):
        """Findings in sensitive paths should get boosted confidence."""
        synth = FindingSynthesizer()
        finding = Finding(
            title="Auth bypass",
            category=FindingCategory.AUTH_BYPASS,
            severity=Severity.HIGH,
            confidence=0.6,
            affected_files=["auth/login.py"],
            reviewing_agents=["claude"],
        )
        review = AgentReview(agent_name="claude", findings=[finding])
        intent = IntentProfile(sensitive_paths=["auth/", "payments/"])
        result = synth.synthesize([review], intent)
        assert result[0].confidence > 0.6  # boosted


class TestSafeRefactorNoFalsePositives:
    """Test that safe refactors don't produce false positives."""

    def test_rename_no_findings(self):
        """A simple rename should produce no findings."""
        synth = FindingSynthesizer()
        # No findings from agents = empty synthesis
        result = synth.synthesize([], IntentProfile())
        assert result == []

    def test_empty_agent_reviews_no_findings(self):
        """Agent reviews with no findings should produce no output."""
        synth = FindingSynthesizer()
        reviews = [
            AgentReview(agent_name="claude", findings=[]),
            AgentReview(agent_name="codex", findings=[]),
        ]
        result = synth.synthesize(reviews, IntentProfile())
        assert result == []
