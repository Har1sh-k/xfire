"""Fancy hacker-style chat renderer for CrossFire adversarial debate transcripts.

Each debate is rendered as a styled terminal chat:
  - Prosecution:  red border,  left-aligned
  - Defense:      cyan border, indented (visual "right side")
  - Judge:        bright-white border, full width, distinct icon
  - Consensus:    colored verdict panel (red=confirmed, green=rejected, yellow=modified)
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

# ── Styling maps ────────────────────────────────────────────────────────────

_AGENT_COLOR: dict[str, str] = {
    "claude": "cyan",
    "codex":  "blue",
    "gemini": "yellow",
}

# consensus value → (header text style, panel border style)
_CONSENSUS_CONFIG: dict[str, tuple[str, str]] = {
    "confirmed":    ("bold red",    "red"),
    "rejected":     ("bold green",  "green"),
    "modified":     ("bold yellow", "yellow"),
    "inconclusive": ("bold yellow", "yellow"),
}

_SEVERITY_STYLE: dict[str, str] = {
    "critical": "bold red",
    "high":     "red",
    "medium":   "yellow",
    "low":      "dim green",
    "info":     "dim",
}

# role → (header label style, panel border style)
_ROLE_CONFIG: dict[str, tuple[str, str]] = {
    "prosecution": ("bold red",          "red"),
    "rebuttal":    ("bold red",          "red"),
    "defense":     ("bold cyan",         "cyan"),
    "counter":     ("bold cyan",         "cyan"),
    "judge":       ("bold bright_white", "bright_white"),
}

# How far to indent "response" bubbles (defense/counter) to mimic a chat layout
_RESPONSE_INDENT = 6


# ── Public API ───────────────────────────────────────────────────────────────


def render_debates(report: Any, console: Console | None = None) -> None:
    """Render all debates in *report* as a hacker-style agent chat transcript.

    Call after the pipeline finishes (report.debates must be populated).
    """
    con = console or Console()

    if not report.debates:
        con.print("\n  [dim]No debates recorded in this report.[/dim]\n")
        return

    total = len(report.debates)

    con.print("")
    con.print(Rule(
        Text("  ⚔   adversarial debate transcript   ⚔  ", style="bold cyan"),
        style="cyan",
        characters="═",
    ))

    for i, debate in enumerate(report.debates, 1):
        _render_debate(debate, i, total, report, con)

    con.print(Rule(style="cyan", characters="═"))
    con.print("")


# ── Internal rendering ───────────────────────────────────────────────────────


def _render_debate(
    debate: Any, idx: int, total: int, report: Any, con: Console
) -> None:
    # ── Resolve finding metadata ─────────────────────────────────────────────
    title = debate.finding_id
    severity = ""
    for f in report.findings:
        if f.id == debate.finding_id:
            title = f.title
            severity = f.severity.value
            break

    sev_style = _SEVERITY_STYLE.get(severity.lower(), "white")

    # ── Debate header ────────────────────────────────────────────────────────
    con.print("")
    hdr = Text()
    hdr.append("  ⚔  ", style="bold red")
    hdr.append(f"debate {idx}/{total}", style="dim red")
    hdr.append("  ─  ", style="dim")
    hdr.append(title, style="bold white")
    if severity:
        hdr.append("  ·  ", style="dim")
        hdr.append(severity.upper(), style=sev_style)
    con.print(hdr)
    con.print(Rule(style="dim red", characters="─"))

    # ── Round 1 ─────────────────────────────────────────────────────────────
    _round_header(con, "round 1")
    p = debate.prosecutor_argument
    d = debate.defense_argument
    _bubble(con, p.agent_name, "prosecution", p.position, p.argument, indent=0)
    _bubble(con, d.agent_name, "defense",     d.position, d.argument, indent=_RESPONSE_INDENT)

    # ── Round 2 (optional) ──────────────────────────────────────────────────
    if debate.round_2_prosecution:
        _round_header(con, "round 2")
        rp = debate.round_2_prosecution
        _bubble(con, rp.agent_name, "rebuttal", rp.position, rp.argument, indent=0)
        if debate.round_2_defense:
            rd = debate.round_2_defense
            _bubble(con, rd.agent_name, "counter", rd.position, rd.argument, indent=_RESPONSE_INDENT)

    # ── Verdict ─────────────────────────────────────────────────────────────
    _round_header(con, "verdict", style="dim white")
    j = debate.judge_ruling
    _bubble(con, j.agent_name, "judge", j.position, j.argument, indent=0, is_judge=True)

    # ── Consensus box ────────────────────────────────────────────────────────
    cv = debate.consensus.value.lower()
    text_style, border_style = _CONSENSUS_CONFIG.get(cv, ("bold white", "white"))

    verdict = Text(justify="center")
    verdict.append(f"  {debate.consensus.value.upper()}  ", style=text_style)
    verdict.append("─", style="dim")
    verdict.append("  severity: ", style="dim")
    verdict.append(debate.final_severity.value.upper(), style=sev_style)
    verdict.append("  ─  evidence: ", style="dim")
    verdict.append(debate.evidence_quality or "—", style="cyan")
    verdict.append("  ")

    con.print(Panel(verdict, border_style=border_style, padding=(0, 2)))
    con.print("")


def _round_header(con: Console, label: str, style: str = "dim") -> None:
    con.print("")
    con.print(Rule(f"  {label}  ", style=style, characters="─"))
    con.print("")


def _bubble(
    con: Console,
    agent_name: str,
    role: str,
    position: str,
    argument: str,
    indent: int = 0,
    is_judge: bool = False,
) -> None:
    """Render one agent speech bubble: header line + bordered panel."""
    agent_color = _AGENT_COLOR.get(agent_name.lower(), "white")
    role_text_style, border = _ROLE_CONFIG.get(role, ("white", "white"))
    icon = "⚖ " if is_judge else "◉ "

    # ── Header: ◉ AGENTNAME  [role]  ·  POSITION ────────────────────────────
    header = Text()
    header.append(f"  {icon}", style=f"bold {agent_color}")
    header.append(agent_name.upper(), style=f"bold {agent_color}")
    header.append("  ", style="dim")
    header.append(f"[{role}]", style=role_text_style)
    if position:
        header.append("  ·  ", style="dim")
        header.append(position.upper(), style="dim white")

    # ── Argument panel ────────────────────────────────────────────────────────
    panel = Panel(
        argument,
        border_style=border,
        padding=(0, 2),
    )

    left_pad = (0, 0, 0, indent)
    con.print(Padding(header, pad=left_pad))
    con.print(Padding(panel,  pad=(0, 0, 1, indent)))
