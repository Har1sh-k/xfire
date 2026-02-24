"""Tests for core data models."""

from crossfire.core.models import (
    AgentArgument,
    AgentReview,
    BlastRadius,
    CitedEvidence,
    ConsensusOutcome,
    CrossFireReport,
    DebateRecord,
    DebateTag,
    DiffHunk,
    Evidence,
    Exploitability,
    FileContext,
    Finding,
    FindingCategory,
    FindingStatus,
    IntentProfile,
    LineRange,
    PRContext,
    PurposeAssessment,
    RelatedFile,
    SecurityControl,
    Severity,
    TrustBoundary,
)


class TestEnums:
    def test_severity_values(self):
        assert Severity.CRITICAL == "Critical"
        assert Severity.HIGH == "High"
        assert Severity.MEDIUM == "Medium"
        assert Severity.LOW == "Low"

    def test_finding_category_has_all_expected(self):
        assert FindingCategory.COMMAND_INJECTION == "COMMAND_INJECTION"
        assert FindingCategory.RACE_CONDITION == "RACE_CONDITION"
        assert FindingCategory.CONNECTION_LEAK == "CONNECTION_LEAK"

    def test_consensus_outcome_values(self):
        assert ConsensusOutcome.CONFIRMED == "Confirmed"
        assert ConsensusOutcome.REJECTED == "Rejected"

    def test_debate_tag_values(self):
        assert DebateTag.NEEDS_DEBATE == "needs_debate"
        assert DebateTag.AUTO_CONFIRMED == "auto_confirmed"


class TestDiffHunk:
    def test_create_minimal(self):
        hunk = DiffHunk(
            file_path="test.py",
            old_start=1,
            old_count=5,
            new_start=1,
            new_count=7,
            content="@@ -1,5 +1,7 @@\n+new line",
        )
        assert hunk.file_path == "test.py"
        assert hunk.added_lines == []
        assert hunk.removed_lines == []

    def test_create_with_lines(self):
        hunk = DiffHunk(
            file_path="test.py",
            old_start=1,
            old_count=5,
            new_start=1,
            new_count=7,
            content="@@ -1,5 +1,7 @@",
            added_lines=["import os", "import sys"],
            removed_lines=["import os"],
        )
        assert len(hunk.added_lines) == 2
        assert len(hunk.removed_lines) == 1


class TestFileContext:
    def test_create_new_file(self):
        fc = FileContext(path="new_file.py", is_new=True)
        assert fc.is_new is True
        assert fc.is_deleted is False
        assert fc.language is None
        assert fc.related_files == []

    def test_create_renamed_file(self):
        fc = FileContext(
            path="new_name.py",
            is_renamed=True,
            old_path="old_name.py",
        )
        assert fc.is_renamed is True
        assert fc.old_path == "old_name.py"


class TestRelatedFile:
    def test_create(self):
        rf = RelatedFile(
            path="utils.py",
            relationship="imports",
            relevance="Changed file imports utility functions",
        )
        assert rf.content is None
        assert rf.relationship == "imports"


class TestPRContext:
    def test_create_minimal(self):
        ctx = PRContext(
            repo_name="owner/repo",
            pr_title="Fix auth bug",
        )
        assert ctx.pr_number is None
        assert ctx.files == []
        assert ctx.base_branch == "main"

    def test_create_full(self):
        ctx = PRContext(
            repo_name="owner/repo",
            pr_number=42,
            pr_title="Add new feature",
            pr_description="This PR adds...",
            author="dev",
            base_branch="main",
            head_branch="feature",
            files=[FileContext(path="app.py")],
            commit_messages=["feat: add feature"],
            labels=["enhancement"],
        )
        assert ctx.pr_number == 42
        assert len(ctx.files) == 1


class TestIntentProfile:
    def test_defaults(self):
        intent = IntentProfile()
        assert intent.repo_purpose == ""
        assert intent.intended_capabilities == []
        assert intent.trust_boundaries == []

    def test_with_controls(self):
        intent = IntentProfile(
            repo_purpose="Web API backend",
            intended_capabilities=["database_access", "file_uploads"],
            security_controls_detected=[
                SecurityControl(
                    control_type="auth",
                    location="middleware/auth.py",
                    description="JWT auth middleware",
                    covers=["/api/*"],
                )
            ],
            trust_boundaries=[
                TrustBoundary(
                    name="HTTP boundary",
                    description="All HTTP input is untrusted",
                    untrusted_inputs=["request.body", "request.params"],
                    controls=["auth_middleware", "input_validation"],
                )
            ],
        )
        assert len(intent.security_controls_detected) == 1
        assert len(intent.trust_boundaries) == 1


class TestFinding:
    def test_create_with_defaults(self):
        f = Finding(
            title="SQL Injection in login",
            category=FindingCategory.SQL_INJECTION,
            severity=Severity.CRITICAL,
        )
        assert f.id  # UUID auto-generated
        assert f.confidence == 0.5
        assert f.status == FindingStatus.UNCLEAR
        assert f.debate_tag == DebateTag.NEEDS_DEBATE

    def test_create_full(self):
        f = Finding(
            title="Command injection in deploy",
            category=FindingCategory.COMMAND_INJECTION,
            severity=Severity.CRITICAL,
            confidence=0.95,
            exploitability=Exploitability.LIKELY,
            blast_radius=BlastRadius.SYSTEM,
            status=FindingStatus.CONFIRMED,
            affected_files=["api/deploy.py"],
            line_ranges=[LineRange(file_path="api/deploy.py", start_line=42, end_line=47)],
            evidence=[
                Evidence(
                    source="claude",
                    evidence_type="code_reading",
                    description="User input passed to subprocess",
                    file_path="api/deploy.py",
                    code_snippet="subprocess.run(cmd, shell=True)",
                    confidence=0.95,
                )
            ],
            data_flow_trace="request.json['cmd'] → subprocess.run(cmd)",
            reviewing_agents=["claude", "codex"],
            purpose_aware_assessment=PurposeAssessment(
                is_intended_capability=False,
                trust_boundary_violated=True,
                untrusted_input_reaches_sink=True,
                assessment="Not intended. No sandbox.",
            ),
        )
        assert f.confidence == 0.95
        assert len(f.reviewing_agents) == 2
        assert f.purpose_aware_assessment.trust_boundary_violated is True


class TestAgentReview:
    def test_create(self):
        review = AgentReview(
            agent_name="claude",
            overall_risk_assessment="high",
            files_analyzed=["app.py", "auth.py"],
        )
        assert review.findings == []
        assert len(review.files_analyzed) == 2


class TestDebateRecord:
    def test_create(self):
        record = DebateRecord(
            finding_id="abc-123",
            prosecutor_argument=AgentArgument(
                agent_name="claude",
                role="prosecutor",
                position="real_issue",
                argument="This is exploitable because...",
                cited_evidence=[
                    CitedEvidence(
                        file_path="api/deploy.py",
                        line_range="42-47",
                        code_snippet="subprocess.run(cmd, shell=True)",
                        explanation="Direct shell execution of user input",
                    )
                ],
                confidence=0.9,
            ),
            defense_argument=AgentArgument(
                agent_name="codex",
                role="defense",
                position="real_issue",
                argument="I agree this is a real issue",
                confidence=0.85,
            ),
            judge_ruling=AgentArgument(
                agent_name="gemini",
                role="judge",
                position="real_issue",
                argument="Confirmed — both sides agree",
                confidence=0.95,
            ),
            consensus=ConsensusOutcome.CONFIRMED,
            final_severity=Severity.CRITICAL,
            final_confidence=0.95,
            evidence_quality="strong",
        )
        assert record.consensus == ConsensusOutcome.CONFIRMED
        assert record.rebuttal is None


class TestCrossFireReport:
    def test_create_minimal(self):
        report = CrossFireReport(
            repo_name="owner/repo",
            context=PRContext(repo_name="owner/repo", pr_title="Test"),
            intent=IntentProfile(),
        )
        assert report.overall_risk == "none"
        assert report.findings == []
        assert report.debates == []
