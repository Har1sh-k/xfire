"""Debug markdown log writer for CrossFire.

Writes a full trace of context, intent, agent interactions, debates,
and findings to a timestamped markdown file.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Event collector — hooked into structlog as a processor
# ---------------------------------------------------------------------------


class DebugCollector:
    """Thread-safe structlog processor that buffers all log events in memory.

    Usage::

        collector = DebugCollector()
        # Register collector.processor in structlog's processor chain BEFORE
        # starting the pipeline.  After the pipeline finishes, call
        # write_markdown() to flush the debug file.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []

    def processor(self, logger: Any, method: str, event_dict: dict) -> dict:
        """structlog processor — appends every event and passes it through."""
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": method.upper(),
            "event": event_dict.get("event", ""),
        }
        # Attach any extra key=value context (exclude event/timestamp)
        extras = {
            k: v for k, v in event_dict.items()
            if k not in ("event", "timestamp", "_record")
        }
        if extras:
            entry["extras"] = extras
        with self._lock:
            self._events.append(entry)
        return event_dict  # pass through unchanged

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)


# ---------------------------------------------------------------------------
# Markdown writer
# ---------------------------------------------------------------------------


def write_debug_markdown(
    report: Any,  # CrossFireReport — avoid circular import
    collector: DebugCollector,
    command_info: dict[str, Any] | None = None,
) -> Path:
    """Write the full debug markdown and return the file path.

    Args:
        report: CrossFireReport from the pipeline.
        collector: DebugCollector that captured pipeline log events.
        command_info: dict of CLI flags used (for the header section).
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(f"crossfire-debug-{timestamp}.md")

    lines: list[str] = []
    w = lines.append  # shorthand

    w(f"# CrossFire Debug Log")
    w(f"")
    w(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ")
    if report.review_duration_seconds:
        w(f"**Duration:** {report.review_duration_seconds:.1f}s  ")
    w(f"**Repo:** {report.repo_name}  ")
    if command_info:
        w(f"**Command flags:**")
        for k, v in command_info.items():
            if v is not None and v is not False:
                w(f"  - `{k}`: `{v}`")
    w(f"")
    w(f"---")
    w(f"")

    # ------------------------------------------------------------------
    # Pipeline Events
    # ------------------------------------------------------------------
    events = collector.events
    if events:
        w(f"## Pipeline Events")
        w(f"")
        w(f"| Time | Level | Event | Details |")
        w(f"|------|-------|-------|---------|")
        for ev in events:
            extras_str = ""
            if ev.get("extras"):
                extras_str = " ".join(
                    f"`{k}={v}`" for k, v in ev["extras"].items()
                )
            level_badge = {
                "INFO": "info",
                "WARNING": "warning",
                "ERROR": "**ERROR**",
                "DEBUG": "debug",
            }.get(ev["level"], ev["level"])
            w(
                f"| {ev['time']} | {level_badge} | `{ev['event']}` "
                f"| {extras_str} |"
            )
        w(f"")
        w(f"---")
        w(f"")

    # ------------------------------------------------------------------
    # Intent Profile
    # ------------------------------------------------------------------
    intent = report.intent
    w(f"## Intent Profile")
    w(f"")
    if intent.repo_purpose:
        w(f"### Repo Purpose")
        w(f"")
        w(intent.repo_purpose)
        w(f"")
    if intent.deployment_context:
        w(f"**Deployment Context:** {intent.deployment_context}  ")
        w(f"")
    if intent.pr_intent:
        w(f"**PR Intent:** {intent.pr_intent}  ")
    if intent.risk_surface_change:
        w(f"**Risk Surface Change:** {intent.risk_surface_change}  ")
    w(f"")

    if intent.intended_capabilities:
        w(f"### Capabilities ({len(intent.intended_capabilities)})")
        w(f"")
        for cap in intent.intended_capabilities:
            w(f"- {cap}")
        w(f"")

    if intent.security_controls_detected:
        w(f"### Security Controls ({len(intent.security_controls_detected)})")
        w(f"")
        w(f"| Type | Location | Description |")
        w(f"|------|----------|-------------|")
        for sc in intent.security_controls_detected:
            covers = ", ".join(sc.covers) if sc.covers else ""
            desc = sc.description
            if covers:
                desc += f" *(covers: {covers})*"
            w(f"| `{sc.control_type}` | `{sc.location}` | {desc} |")
        w(f"")

    if intent.trust_boundaries:
        w(f"### Trust Boundaries ({len(intent.trust_boundaries)})")
        w(f"")
        for tb in intent.trust_boundaries:
            w(f"#### {tb.name}")
            w(f"")
            w(tb.description)
            if tb.untrusted_inputs:
                w(f"")
                w(f"**Untrusted inputs:** {', '.join(f'`{i}`' for i in tb.untrusted_inputs)}")
            if tb.controls:
                w(f"**Controls:** {', '.join(f'`{c}`' for c in tb.controls)}")
            w(f"")

    if intent.sensitive_paths:
        w(f"### Sensitive Paths")
        w(f"")
        for p in intent.sensitive_paths:
            w(f"- `{p}`")
        w(f"")

    w(f"---")
    w(f"")

    # ------------------------------------------------------------------
    # Context
    # ------------------------------------------------------------------
    ctx = report.context
    w(f"## Context")
    w(f"")
    w(f"**Repo:** {ctx.repo_name}  ")
    if ctx.pr_title:
        w(f"**PR/Title:** {ctx.pr_title}  ")
    w(f"**Files Changed:** {len(ctx.files)}  ")
    w(f"")

    if ctx.files:
        w(f"### Files")
        w(f"")
        w(f"| File | Language | Status |")
        w(f"|------|----------|--------|")
        for f in ctx.files:
            status = "new" if f.is_new else ("deleted" if f.is_deleted else "modified")
            lang = f.language or "—"
            w(f"| `{f.path}` | {lang} | {status} |")
        w(f"")

    if ctx.directory_structure:
        w(f"### Directory Structure (excerpt)")
        w(f"")
        w(f"```")
        # Trim if very large
        ds = ctx.directory_structure
        if len(ds) > 3000:
            ds = ds[:3000] + "\n... (truncated)"
        w(ds)
        w(f"```")
        w(f"")

    if ctx.readme_content:
        w(f"### README (excerpt)")
        w(f"")
        excerpt = ctx.readme_content[:1000]
        if len(ctx.readme_content) > 1000:
            excerpt += "\n... (truncated)"
        w(f"```")
        w(excerpt)
        w(f"```")
        w(f"")

    w(f"---")
    w(f"")

    # ------------------------------------------------------------------
    # Agent Reviews
    # ------------------------------------------------------------------
    if report.agent_reviews:
        w(f"## Agent Reviews")
        w(f"")
        for rev in report.agent_reviews:
            duration_str = ""
            if rev.review_duration_seconds:
                duration_str = f" — {rev.review_duration_seconds:.1f}s"
            w(f"### {rev.agent_name.capitalize()}{duration_str}")
            w(f"")

            if rev.review_methodology:
                w(f"**Methodology:** {rev.review_methodology}")
                w(f"")
            if rev.files_analyzed:
                w(f"**Files analyzed:** {', '.join(f'`{f}`' for f in rev.files_analyzed)}")
                w(f"")

            if rev.thinking_trace:
                w(f"<details><summary>Reasoning trace ({len(rev.thinking_trace)} chars)</summary>")
                w(f"")
                w(f"```")
                w(rev.thinking_trace)
                w(f"```")
                w(f"")
                w(f"</details>")
                w(f"")

            if rev.findings:
                w(f"**Raw findings from this agent ({len(rev.findings)}):**")
                w(f"")
                for i, finding in enumerate(rev.findings, 1):
                    w(f"#### {i}. {finding.title}")
                    w(f"")
                    w(f"**Severity:** {finding.severity.value} | **Confidence:** {finding.confidence:.2f} | **Status:** {finding.status.value}")
                    if finding.affected_files:
                        w(f"**Files:** {', '.join(f'`{af}`' for af in finding.affected_files)}")
                    if finding.rationale_summary:
                        w(f"")
                        w(finding.rationale_summary)
                    w(f"")
            else:
                w(f"*No findings from this agent.*")
                w(f"")

        w(f"---")
        w(f"")

    # ------------------------------------------------------------------
    # Debates
    # ------------------------------------------------------------------
    if report.debates:
        w(f"## Debates ({len(report.debates)})")
        w(f"")
        for debate in report.debates:
            # Find corresponding finding title
            finding_title = debate.finding_id
            for finding in report.findings:
                if finding.id == debate.finding_id:
                    finding_title = finding.title
                    break

            w(f"### {finding_title}")
            w(f"")
            p = debate.prosecutor_argument
            d = debate.defense_argument
            j = debate.judge_ruling
            w(f"**Prosecutor ({p.agent_name}):** {p.position}")
            w(f"")
            w(f"> {p.argument[:500]}{'...' if len(p.argument) > 500 else ''}")
            w(f"")
            w(f"**Defense ({d.agent_name}):** {d.position}")
            w(f"")
            w(f"> {d.argument[:500]}{'...' if len(d.argument) > 500 else ''}")
            w(f"")
            if debate.round_2_prosecution:
                rp = debate.round_2_prosecution
                rd = debate.round_2_defense
                w(f"**Round 2 — Prosecution ({rp.agent_name}):**")
                w(f"> {rp.argument[:300]}{'...' if len(rp.argument) > 300 else ''}")
                w(f"")
                if rd:
                    w(f"**Round 2 — Defense ({rd.agent_name}):**")
                    w(f"> {rd.argument[:300]}{'...' if len(rd.argument) > 300 else ''}")
                    w(f"")
            w(f"**Judge ({j.agent_name}):** {j.position}")
            w(f"")
            w(f"> {j.argument[:400]}{'...' if len(j.argument) > 400 else ''}")
            w(f"")
            w(f"**Consensus:** `{debate.consensus.value}` | **Final Severity:** {debate.final_severity.value} | **Evidence quality:** {debate.evidence_quality}")
            w(f"")

        w(f"---")
        w(f"")

    # ------------------------------------------------------------------
    # Final report (full markdown)
    # ------------------------------------------------------------------
    from crossfire.output.markdown_report import generate_markdown_report

    w(f"## Final Report")
    w(f"")
    w(generate_markdown_report(report))

    # Write file
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
