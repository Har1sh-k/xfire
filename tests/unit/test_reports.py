"""Tests for report generators — markdown, JSON, SARIF."""

import json

from xfire.core.models import (
    CrossFireReport,
    Evidence,
    Finding,
    FindingCategory,
    FindingStatus,
    IntentProfile,
    PRContext,
    Severity,
)
from xfire.output.json_report import generate_json_report
from xfire.output.markdown_report import generate_markdown_report
from xfire.output.sarif_report import generate_sarif_report


def _make_report(
    findings: list[Finding] | None = None,
    agents_used: list[str] | None = None,
) -> CrossFireReport:
    return CrossFireReport(
        repo_name="org/repo",
        pr_number=42,
        pr_title="Fix auth bypass",
        context=PRContext(repo_name="org/repo", pr_title="Fix auth bypass"),
        intent=IntentProfile(repo_purpose="Web API"),
        findings=findings or [],
        agents_used=agents_used or ["claude", "codex", "gemini"],
    )


def _make_finding(
    title: str = "SQL Injection",
    severity: Severity = Severity.HIGH,
    status: FindingStatus = FindingStatus.CONFIRMED,
    agents: list[str] | None = None,
) -> Finding:
    return Finding(
        title=title,
        category=FindingCategory.SQL_INJECTION,
        severity=severity,
        confidence=0.85,
        status=status,
        affected_files=["auth/login.py"],
        reviewing_agents=agents or ["claude", "codex"],
        evidence=[
            Evidence(
                source="claude",
                evidence_type="code_reading",
                description="Unsanitized query",
                file_path="auth/login.py",
                code_snippet="cursor.execute(f'SELECT * FROM users WHERE id={uid}')",
            ),
        ],
        rationale_summary="User input interpolated into SQL query",
    )


class TestMarkdownReport:
    def test_empty_findings(self):
        report = _make_report()
        md = generate_markdown_report(report)
        assert "No Security Issues Found" in md
        assert "xfire Security Review" in md

    def test_confirmed_finding(self):
        report = _make_report(findings=[_make_finding()])
        md = generate_markdown_report(report)
        assert "Confirmed Findings" in md
        assert "SQL Injection" in md
        assert "auth/login.py" in md

    def test_found_by_shows_correct_ratio(self):
        finding = _make_finding(agents=["claude"])
        report = _make_report(findings=[finding], agents_used=["claude", "codex", "gemini"])
        md = generate_markdown_report(report)
        assert "1/3 agents" in md

    def test_rejected_findings_collapsed(self):
        finding = _make_finding(status=FindingStatus.REJECTED)
        report = _make_report(findings=[finding])
        md = generate_markdown_report(report)
        assert "Rejected Findings" in md
        assert "<details>" in md

    def test_multiple_statuses(self):
        findings = [
            _make_finding(title="Confirmed Issue", status=FindingStatus.CONFIRMED),
            _make_finding(title="Likely Issue", status=FindingStatus.LIKELY),
            _make_finding(title="Unclear Issue", status=FindingStatus.UNCLEAR),
        ]
        report = _make_report(findings=findings)
        md = generate_markdown_report(report)
        assert "Confirmed Findings" in md
        assert "Likely Findings" in md
        assert "Needs Human Review" in md


class TestJsonReport:
    def test_serialization(self):
        report = _make_report(findings=[_make_finding()])
        output = generate_json_report(report)
        data = json.loads(output)
        assert data["repo_name"] == "org/repo"
        assert len(data["findings"]) == 1
        assert data["findings"][0]["title"] == "SQL Injection"

    def test_empty_findings(self):
        report = _make_report()
        output = generate_json_report(report)
        data = json.loads(output)
        assert data["findings"] == []

    def test_enums_serialized(self):
        report = _make_report(findings=[_make_finding()])
        output = generate_json_report(report)
        data = json.loads(output)
        assert data["findings"][0]["severity"] == "High"
        assert data["findings"][0]["category"] == "SQL_INJECTION"


class TestSarifReport:
    def test_basic_structure(self):
        report = _make_report(findings=[_make_finding()])
        output = generate_sarif_report(report)
        sarif = json.loads(output)
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"]) == 1
        assert sarif["runs"][0]["tool"]["driver"]["name"] == "xfire"

    def test_results_present(self):
        report = _make_report(findings=[_make_finding()])
        sarif = json.loads(generate_sarif_report(report))
        results = sarif["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["ruleId"] == "SQL_INJECTION"
        assert results[0]["level"] == "error"

    def test_rejected_findings_excluded(self):
        finding = _make_finding(status=FindingStatus.REJECTED)
        report = _make_report(findings=[finding])
        sarif = json.loads(generate_sarif_report(report))
        assert len(sarif["runs"][0]["results"]) == 0

    def test_severity_level_mapping(self):
        findings = [
            _make_finding(severity=Severity.CRITICAL, title="Critical"),
            _make_finding(severity=Severity.MEDIUM, title="Medium"),
            _make_finding(severity=Severity.LOW, title="Low"),
        ]
        report = _make_report(findings=findings)
        sarif = json.loads(generate_sarif_report(report))
        results = sarif["runs"][0]["results"]
        levels = {r["message"]["text"].split(":")[0]: r["level"] for r in results}
        assert levels["Critical"] == "error"
        assert levels["Medium"] == "warning"
        assert levels["Low"] == "note"

    def test_partial_fingerprints_present(self):
        report = _make_report(findings=[_make_finding()])
        sarif = json.loads(generate_sarif_report(report))
        result = sarif["runs"][0]["results"][0]
        assert "partialFingerprints" in result
        assert "xfire/v1" in result["partialFingerprints"]

    def test_rank_present(self):
        report = _make_report(findings=[_make_finding()])
        sarif = json.loads(generate_sarif_report(report))
        result = sarif["runs"][0]["results"][0]
        assert "rank" in result
        assert result["rank"] == 75.0  # HIGH severity

    def test_rules_deduplicated(self):
        findings = [
            _make_finding(title="Issue 1"),
            _make_finding(title="Issue 2"),  # same category
        ]
        report = _make_report(findings=findings)
        sarif = json.loads(generate_sarif_report(report))
        rules = sarif["runs"][0]["tool"]["driver"]["rules"]
        assert len(rules) == 1  # deduplicated by category

    def test_run_properties(self):
        report = _make_report(findings=[_make_finding()])
        sarif = json.loads(generate_sarif_report(report))
        props = sarif["runs"][0]["properties"]
        assert "xfire:agentsUsed" in props
        assert "xfire:overallRisk" in props

    def test_empty_findings(self):
        report = _make_report()
        sarif = json.loads(generate_sarif_report(report))
        assert len(sarif["runs"][0]["results"]) == 0
        assert len(sarif["runs"][0]["tool"]["driver"]["rules"]) == 0
