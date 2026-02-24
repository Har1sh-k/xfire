"""Markdown report generator for CrossFire."""

from __future__ import annotations

from crossfire.core.models import (
    CrossFireReport,
    DebateRecord,
    Finding,
    FindingStatus,
    Severity,
)

SEVERITY_EMOJI = {
    Severity.CRITICAL: "🚨",
    Severity.HIGH: "🔴",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🔵",
}

STATUS_EMOJI = {
    FindingStatus.CONFIRMED: "🚨",
    FindingStatus.LIKELY: "⚠️",
    FindingStatus.UNCLEAR: "🔍",
    FindingStatus.REJECTED: "✅",
}


def generate_markdown_report(report: CrossFireReport) -> str:
    """Generate a markdown report from a CrossFire analysis report."""
    parts: list[str] = []

    # Header
    pr_ref = f" #{report.pr_number}" if report.pr_number else ""
    parts.append(f"# 🔥 CrossFire Security Review — {report.repo_name}{pr_ref}")
    parts.append("")
    parts.append("> Multiple agents. One verdict. Zero blind spots.")
    parts.append("")

    # Summary table
    confirmed = [f for f in report.findings if f.status == FindingStatus.CONFIRMED]
    likely = [f for f in report.findings if f.status == FindingStatus.LIKELY]
    unclear = [f for f in report.findings if f.status == FindingStatus.UNCLEAR]
    rejected = [f for f in report.findings if f.status == FindingStatus.REJECTED]

    parts.append("## Summary")
    parts.append("| Status | Count |")
    parts.append("|--------|-------|")
    parts.append(f"| 🚨 Confirmed | {len(confirmed)} |")
    parts.append(f"| ⚠️ Likely | {len(likely)} |")
    parts.append(f"| 🔍 Needs Review | {len(unclear)} |")
    parts.append(f"| ✅ Rejected | {len(rejected)} |")
    parts.append("")

    # Overall risk
    risk_upper = report.overall_risk.upper()
    parts.append(f"**Overall Risk: {risk_upper}**")
    if confirmed:
        parts.append(f" — {len(confirmed)} confirmed finding(s) require immediate attention.")
    parts.append("")

    # Agents info
    agent_status = " | ".join(
        f"{name} ✓" for name in report.agents_used
    ) if report.agents_used else "No agents"
    depth = report.context.directory_structure[:20] + "..." if report.context.directory_structure else "N/A"
    file_count = len(report.context.files)
    duration = f"{report.review_duration_seconds:.0f}s" if report.review_duration_seconds else "N/A"

    parts.append(f"**Agents:** {agent_status}  ")
    parts.append(f"**Files Analyzed:** {file_count} | **Review Duration:** {duration}")
    parts.append("")
    parts.append("---")
    parts.append("")

    # Confirmed findings
    if confirmed:
        parts.append("## 🚨 Confirmed Findings")
        parts.append("")
        for i, f in enumerate(confirmed, 1):
            parts.append(_format_finding(f, i))
            # Include debate log if available
            debate = _find_debate(f, report.debates)
            if debate:
                parts.append(_format_debate_log(debate))
            parts.append("")

    # Likely findings
    if likely:
        parts.append("## ⚠️ Likely Findings")
        parts.append("")
        for i, f in enumerate(likely, 1):
            parts.append(_format_finding(f, i))
            parts.append("")

    # Unclear findings
    if unclear:
        parts.append("## 🔍 Needs Human Review")
        parts.append("")
        for i, f in enumerate(unclear, 1):
            parts.append(_format_finding(f, i))
            parts.append("")

    # Rejected findings (collapsed)
    if rejected:
        parts.append("## ✅ Rejected Findings (False Positives)")
        parts.append("")
        parts.append(f"<details><summary>{len(rejected)} finding(s) rejected after review</summary>")
        parts.append("")
        for f in rejected:
            parts.append(f"### {f.title} [REJECTED]")
            parts.append(f"**Why rejected:** {f.debate_summary or f.rationale_summary or 'Rejected during review.'}")
            parts.append("")
        parts.append("</details>")
        parts.append("")

    # No findings case
    if not report.findings:
        parts.append("## ✅ No Security Issues Found")
        parts.append("")
        parts.append("All agents reviewed the PR and found no security issues or dangerous bugs.")
        parts.append("")

    return "\n".join(parts)


def _format_finding(finding: Finding, index: int) -> str:
    """Format a single finding for the markdown report."""
    parts: list[str] = []

    emoji = SEVERITY_EMOJI.get(finding.severity, "")
    parts.append(f"### CF-{index:03d}: {finding.title}")

    agents_str = ", ".join(finding.reviewing_agents)
    agent_count = len(finding.reviewing_agents)
    parts.append(
        f"**Severity:** {finding.severity.value} | "
        f"**Confidence:** {finding.confidence:.2f} | "
        f"**Exploitability:** {finding.exploitability.value}  "
    )
    parts.append(
        f"**Blast Radius:** {finding.blast_radius.value} | "
        f"**Found by:** {agents_str} ({agent_count}/{agent_count} agent{'s' if agent_count > 1 else ''})"
    )
    parts.append("")

    # Affected files
    if finding.affected_files:
        files_str = ", ".join(f"`{f}`" for f in finding.affected_files)
        lines_str = ""
        if finding.line_ranges:
            lines_str = " lines " + ", ".join(
                f"{lr.start_line}-{lr.end_line}" for lr in finding.line_ranges
            )
        parts.append(f"**Affected:** {files_str}{lines_str}")
        parts.append("")

    # Rationale
    if finding.rationale_summary:
        parts.append(f"**What's wrong:**  ")
        parts.append(finding.rationale_summary)
        parts.append("")

    # Data flow trace
    if finding.data_flow_trace:
        parts.append(f"**Data flow:**  ")
        parts.append(f"`{finding.data_flow_trace}`")
        parts.append("")

    # Evidence
    if finding.evidence:
        parts.append("**Evidence:**")
        for ev in finding.evidence:
            parts.append(f"- `{ev.file_path or 'N/A'}` — {ev.description}")
            if ev.code_snippet:
                parts.append(f"  ```\n  {ev.code_snippet}\n  ```")
        parts.append("")

    # Purpose assessment
    pa = finding.purpose_aware_assessment
    if pa.assessment:
        parts.append(f"**Purpose Assessment:**  ")
        parts.append(pa.assessment)
        parts.append("")

    # Mitigations
    if finding.mitigations:
        parts.append("**Mitigations:**")
        for i, m in enumerate(finding.mitigations, 1):
            parts.append(f"{i}. {m}")
        parts.append("")

    return "\n".join(parts)


def _find_debate(finding: Finding, debates: list[DebateRecord]) -> DebateRecord | None:
    """Find the debate record for a finding."""
    for debate in debates:
        if debate.finding_id == finding.id:
            return debate
    return None


def _format_debate_log(debate: DebateRecord) -> str:
    """Format a debate record as a collapsed log."""
    parts: list[str] = []
    parts.append("<details><summary>🔍 Debate Log</summary>")
    parts.append("")

    p = debate.prosecutor_argument
    d = debate.defense_argument
    j = debate.judge_ruling

    parts.append(f"**Prosecutor ({p.agent_name}):** {p.position} — {p.argument[:300]}...")
    parts.append("")
    parts.append(f"**Defense ({d.agent_name}):** {d.position} — {d.argument[:300]}...")
    parts.append("")

    if debate.rebuttal:
        r = debate.rebuttal
        parts.append(f"**Rebuttal ({r.agent_name}):** {r.argument[:200]}...")
        parts.append("")

    parts.append(f"**Judge ({j.agent_name}):** {j.position}")
    parts.append(f"Evidence quality: {debate.evidence_quality}")
    parts.append("")
    parts.append("</details>")

    return "\n".join(parts)
