"""Hacker-themed live terminal UI for CrossFire pipeline execution.

Inspired by Metasploit's banner style:  cyan accents, ASCII art header,
stats block, and per-phase spinners that resolve to checkmarks.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# Braille spinner animation frames (rotates at ~8 fps via Live refresh rate)
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _spinner() -> str:
    """Return the current braille spinner frame based on wall-clock time."""
    return _SPINNER_FRAMES[int(time.monotonic() * 8) % len(_SPINNER_FRAMES)]

from rich.console import Console, ConsoleOptions, RenderResult
from rich.live import Live
from rich.text import Text

# ---------------------------------------------------------------------------
# ASCII banner — CrossFire logo (block-style, 6 lines)
# ---------------------------------------------------------------------------

_LOGO_LINES = [
    " ██████╗██████╗  ██████╗ ███████╗███████╗███████╗██╗██████╗ ███████╗",
    "██╔════╝██╔══██╗██╔═══██╗██╔════╝██╔════╝██╔════╝██║██╔══██╗██╔════╝",
    "██║     ██████╔╝██║   ██║███████╗███████╗█████╗  ██║██████╔╝█████╗  ",
    "██║     ██╔══██╗██║   ██║╚════██║╚════██║██╔══╝  ██║██╔══██╗██╔══╝  ",
    "╚██████╗██║  ██║╚██████╔╝███████║███████║██║     ██║██║  ██║███████╗",
    " ╚═════╝╚═╝  ╚═╝ ╚═════╝╚══════╝╚══════╝╚═╝     ╚═╝╚═╝  ╚═╝╚══════╝",
]

# ---------------------------------------------------------------------------
# Module-level banner helpers (usable without a full instance)
# ---------------------------------------------------------------------------


def render_banner() -> Text:
    """Return the static CrossFire ASCII logo as a Rich Text object."""
    t = Text()
    t.append("\n")
    for line in _LOGO_LINES:
        t.append("  " + line + "\n", style="bold cyan")
    t.append(
        "             Multiple agents. One verdict. Zero blind spots.\n",
        style="dim cyan",
    )
    t.append("\n")
    return t


def render_stats(
    repo: str = "",
    mode: str = "",
    agents: list[str] | None = None,
    debate_enabled: bool = True,
    context_depth: str = "deep",
) -> Text:
    """Return a Metasploit-style stats block as a Rich Text object."""
    t = Text()
    agents = agents or []
    agents_str = "  ·  ".join(agents) if agents else "none"
    debate_str = "enabled" if debate_enabled else "disabled"

    t.append("     [ ", style="cyan")
    t.append("crossfire", style="bold white")
    t.append(" ]\n", style="cyan")

    t.append("  + -- --=[ ", style="cyan")
    t.append(f"{len(agents)} agents", style="bold white")
    t.append("  —  ", style="dim")
    t.append(agents_str, style="cyan")
    t.append(" ]\n", style="cyan")

    t.append("  + -- --=[ ", style="cyan")
    t.append("adversarial debate  ", style="bold white")
    t.append(debate_str, style="green" if debate_enabled else "dim")
    t.append("  ·  context: ", style="dim")
    t.append(context_depth, style="white")
    t.append(" ]\n", style="cyan")

    t.append("  + -- --=[ ", style="cyan")
    t.append(f"mode: {mode}  ·  repo: ", style="dim")
    t.append(repo or "local", style="white")
    t.append(" ]\n", style="cyan")

    return t


# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------

_PHASES = [
    ("context",   "Building context"),
    ("intent",    "Intent inference"),
    ("skills",    "Skills analysis"),
    ("reviews",   "Agent reviews"),
    ("synthesis", "Synthesizing"),
    ("debate",    "Adversarial debate"),
]

_EVENT_PHASE_MAP: dict[str, tuple[str, str]] = {
    "pipeline.context_building":   ("context",   "running"),
    "pipeline.context_ready":      ("context",   "done"),
    "pipeline.intent_inference":   ("intent",    "running"),
    "pipeline.intent_ready":       ("intent",    "done"),
    "pipeline.skills_running":     ("skills",    "running"),
    "pipeline.skills_complete":    ("skills",    "done"),
    "pipeline.agent_reviews":      ("reviews",   "running"),
    "pipeline.reviews_complete":   ("reviews",   "done"),
    "pipeline.synthesizing":       ("synthesis", "running"),
    "pipeline.synthesis_complete": ("synthesis", "done"),
    "pipeline.debate_starting":    ("debate",    "running"),
    "pipeline.debate_complete":    ("debate",    "done"),
}

# Events to show in the debug live log (keyed by event name → label)
_DEBUG_LOG_MAX = 8  # max lines shown in the live debug log section


# ---------------------------------------------------------------------------
# Live renderables — re-invoke _render() on every Rich refresh tick so the
# braille spinner actually animates between structlog events.
# ---------------------------------------------------------------------------


class _HackerRenderable:
    """Rich renderable that calls HackerUI._render() fresh on every refresh."""

    def __init__(self, ui: "HackerUI") -> None:
        self._ui = ui

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield self._ui._render()


class _AgentTestRenderable:
    """Rich renderable that calls AgentTestUI._render() fresh on every refresh."""

    def __init__(self, ui: "AgentTestUI") -> None:
        self._ui = ui

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield self._ui._render()


# ---------------------------------------------------------------------------
# HackerUI — pipeline live display
# ---------------------------------------------------------------------------


class HackerUI:
    """Manages the live hacker-style terminal display during pipeline execution.

    Usage::

        ui = HackerUI(repo="owner/repo", mode="patch", agents=["claude","codex"],
                      debate_enabled=True, context_depth="deep")
        console.print(render_banner())
        console.print(render_stats(...))
        with ui:                           # starts Rich Live display
            report = asyncio.run(pipeline())
        # Live display stays on screen (transient=False)
    """

    def __init__(
        self,
        repo: str = "",
        mode: str = "patch",
        agents: list[str] | None = None,
        debate_enabled: bool = True,
        context_depth: str = "deep",
        debug_mode: bool = False,
        show_debate: bool = False,
        console: Console | None = None,
    ) -> None:
        self._repo = repo
        self._mode = mode
        self._agents = agents or []
        self._debate_enabled = debate_enabled
        self._context_depth = context_depth
        self._debug_mode = debug_mode
        self._show_debate = show_debate
        self._console = console or Console()
        # Live debate state (tracked across argument events)
        self._debate_live_finding: str = ""
        self._debate_live_round: int = 0

        # Phase state: "pending" | "running" | "done" | "error"
        self._phase_status: dict[str, str] = {p: "pending" for p, _ in _PHASES}
        self._phase_elapsed: dict[str, float] = {}
        self._phase_start: dict[str, float] = {}
        self._phase_detail: dict[str, str] = {}

        # Per-agent review status
        self._agent_status: dict[str, str] = {a: "pending" for a in self._agents}
        self._agent_findings: dict[str, int] = {}
        self._agent_detail: dict[str, str] = {}

        # Current debate info
        self._debate_current: str = ""
        self._debate_count: int = 0
        self._debate_done: int = 0

        # Errors / warnings
        self._warnings: list[str] = []

        # Debug live log (ring buffer of recent events, only used when debug_mode=True)
        self._debug_events: list[tuple[str, str, str]] = []  # (time, event, extras)

        self._lock = threading.Lock()
        self._live = Live(
            _HackerRenderable(self),
            refresh_per_second=10,
            console=self._console,
            transient=False,
        )

    # ------------------------------------------------------------------
    # Compat: instance methods delegate to module-level functions
    # ------------------------------------------------------------------

    def render_banner(self) -> Text:
        return render_banner()

    def render_stats(self) -> Text:
        return render_stats(
            repo=self._repo,
            mode=self._mode,
            agents=self._agents,
            debate_enabled=self._debate_enabled,
            context_depth=self._context_depth,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def processor(self, logger: Any, method: str, event_dict: dict) -> dict:
        """structlog processor — captures pipeline events and updates display.

        Must be registered in structlog's processor chain BEFORE any renderer.
        Raises DropEvent so events are not printed to stdout.
        """
        import structlog

        event = event_dict.get("event", "")

        with self._lock:
            # Phase transitions
            if event in _EVENT_PHASE_MAP:
                phase, status = _EVENT_PHASE_MAP[event]
                self._phase_status[phase] = status
                if status == "running":
                    self._phase_start[phase] = time.monotonic()
                elif status == "done":
                    if phase in self._phase_start:
                        self._phase_elapsed[phase] = (
                            time.monotonic() - self._phase_start[phase]
                        )

            # Phase details from key events
            if event == "pipeline.intent_ready":
                caps = event_dict.get("capabilities", "")
                ctrls = event_dict.get("controls", "")
                self._phase_detail["intent"] = f"{caps} capabilities · {ctrls} controls"

            elif event == "pipeline.skills_complete":
                skills = event_dict.get("skills", [])
                if isinstance(skills, list):
                    self._phase_detail["skills"] = f"{len(skills)} skills"

            elif event == "review.start":
                agents = event_dict.get("agents", [])
                if isinstance(agents, list):
                    for a in agents:
                        self._agent_status[a] = "running"
                        self._phase_start[f"agent_{a}"] = time.monotonic()

            elif event == "review.agent_complete":
                agent = str(event_dict.get("agent", ""))
                findings = event_dict.get("findings", 0)
                self._agent_status[agent] = "done"
                self._agent_findings[agent] = int(findings)
                if f"agent_{agent}" in self._phase_start:
                    elapsed = time.monotonic() - self._phase_start[f"agent_{agent}"]
                    self._agent_detail[agent] = f"{findings}f  {elapsed:.0f}s"

            elif event == "review.agent_error":
                agent = str(event_dict.get("agent", ""))
                self._agent_status[agent] = "error"
                error = str(event_dict.get("error", ""))[:60]
                self._agent_detail[agent] = f"error: {error}"

            elif event == "debate.start":
                finding = str(event_dict.get("finding", ""))
                self._debate_current = finding[:50] + ("…" if len(finding) > 50 else "")
                self._debate_count += 1

            elif event == "debate.complete":
                self._debate_done += 1
                consensus = str(event_dict.get("consensus", "")).lower()
                self._phase_detail["debate"] = (
                    f"{self._debate_done}/{self._debate_count}  ·  last: {consensus}"
                )
                self._debate_current = ""

            elif event == "pipeline.all_agents_failed":
                self._warnings.append("All agents failed — review incomplete")

            # Capture live debate event data (must copy before lock releases)
            _debate_arg_event: dict | None = None
            _debate_verdict_event: dict | None = None
            if self._show_debate:
                if event == "debate.argument":
                    _debate_arg_event = dict(event_dict)
                elif event == "debate.verdict":
                    _debate_verdict_event = dict(event_dict)

            # Debug live log
            if self._debug_mode:
                ts = time.strftime("%H:%M:%S")
                extras_parts = [
                    f"{k}={v}"
                    for k, v in event_dict.items()
                    if k not in ("event", "timestamp", "_record", "level")
                ]
                extras = "  " + "  ".join(extras_parts[:4]) if extras_parts else ""
                self._debug_events.append((ts, event, extras))
                if len(self._debug_events) > _DEBUG_LOG_MAX:
                    self._debug_events.pop(0)

        # Render live debate bubbles above the Live area (outside lock)
        if _debate_arg_event is not None:
            self._print_debate_argument(_debate_arg_event)
        if _debate_verdict_event is not None:
            self._print_debate_verdict(_debate_verdict_event)

        # Trigger an immediate refresh so phase transitions appear instantly
        # (the renderable itself always calls _render() fresh on each tick)
        try:
            self._live.refresh()
        except Exception:
            pass  # Never let UI errors crash the pipeline

        raise structlog.DropEvent()

    def _print_debate_argument(self, event_dict: dict) -> None:
        """Print one debate speech bubble above the Live area."""
        from crossfire.output.debate_view import _RESPONSE_INDENT, _bubble
        from rich.rule import Rule

        agent = str(event_dict.get("agent", ""))
        role = str(event_dict.get("role", ""))
        position = str(event_dict.get("position", ""))
        argument = str(event_dict.get("argument", ""))
        finding = str(event_dict.get("finding", ""))

        # Section headers before the bubble
        if role == "prosecution":
            self._console.print("")
            self._console.print(Rule(
                f"  ⚔  {finding[:55]}{'…' if len(finding) > 55 else ''}  ",
                style="dim red", characters="─",
            ))
            self._console.print(Rule("  round 1  ", style="dim", characters="─"))
            self._console.print("")
        elif role == "rebuttal":
            self._console.print(Rule("  round 2  ", style="dim", characters="─"))
            self._console.print("")
        elif role == "judge":
            self._console.print(Rule("  verdict  ", style="dim white", characters="─"))
            self._console.print("")

        indent = _RESPONSE_INDENT if role in ("defense", "counter") else 0
        _bubble(self._console, agent, role, position, argument,
                indent=indent, is_judge=(role == "judge"))

    def _print_debate_verdict(self, event_dict: dict) -> None:
        """Print the consensus verdict panel above the Live area."""
        from rich.panel import Panel
        from rich.text import Text
        from crossfire.output.debate_view import _CONSENSUS_CONFIG, _SEVERITY_STYLE

        consensus = str(event_dict.get("consensus", "")).lower()
        severity = str(event_dict.get("final_severity", "")).lower()
        evidence = str(event_dict.get("evidence_quality", "—"))

        text_style, border_style = _CONSENSUS_CONFIG.get(consensus, ("bold white", "white"))
        sev_style = _SEVERITY_STYLE.get(severity, "white")

        verdict = Text(justify="center")
        verdict.append(f"  {consensus.upper()}  ", style=text_style)
        verdict.append("─", style="dim")
        verdict.append("  severity: ", style="dim")
        verdict.append(severity.upper(), style=sev_style)
        verdict.append("  ─  evidence: ", style="dim")
        verdict.append(evidence, style="cyan")
        verdict.append("  ")

        self._console.print(Panel(verdict, border_style=border_style, padding=(0, 2)))
        self._console.print("")

    def __enter__(self) -> "HackerUI":
        self._live.start()
        return self

    def __exit__(self, *_: Any) -> None:
        # Mark any still-running phases as done (pipeline ended)
        with self._lock:
            for phase, _ in _PHASES:
                if self._phase_status[phase] == "running":
                    self._phase_status[phase] = "done"
                    if phase in self._phase_start:
                        self._phase_elapsed[phase] = (
                            time.monotonic() - self._phase_start[phase]
                        )
        self._live.refresh()
        self._live.stop()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> Text:
        """Build the full live status display."""
        t = Text()

        # Phase rows
        for phase_key, phase_label in _PHASES:
            status = self._phase_status[phase_key]
            t.append("  ")
            t.append(*self._status_icon(status))
            t.append("  ")
            t.append(f"{phase_label:<22}", style=self._label_style(status))

            if status == "done":
                elapsed = self._phase_elapsed.get(phase_key, 0)
                t.append(f" {elapsed:>6.1f}s", style="dim")
                detail = self._phase_detail.get(phase_key, "")
                if detail:
                    t.append(f"  {detail}", style="dim cyan")
            elif status == "running":
                elapsed = time.monotonic() - self._phase_start.get(phase_key, time.monotonic())
                t.append(f" {elapsed:>6.1f}s", style="yellow")

            t.append("\n")

            # Expand agent rows under the reviews phase
            if phase_key == "reviews" and status in ("running", "done"):
                for agent in self._agents:
                    astatus = self._agent_status.get(agent, "pending")
                    t.append("       ")
                    t.append(*self._agent_icon(astatus))
                    t.append(f"  {agent:<10}", style=self._agent_style(astatus))
                    detail = self._agent_detail.get(agent, "")
                    if detail:
                        t.append(detail, style="dim")
                    t.append("\n")

            # Show current debate target
            if phase_key == "debate" and status == "running" and self._debate_current:
                t.append(f"       >> {self._debate_current}\n", style="dim cyan")

        # Warnings
        for warn in self._warnings:
            t.append(f"\n  [!] {warn}\n", style="bold yellow")

        # Debug live log section
        if self._debug_mode and self._debug_events:
            t.append("\n  ", style="dim")
            t.append("─" * 60 + "\n", style="dim")
            t.append("  live log\n", style="dim cyan")
            for ts, event, extras in self._debug_events:
                t.append(f"  {ts}  ", style="dim")
                t.append(f"{event}", style="cyan")
                if extras:
                    t.append(extras, style="dim")
                t.append("\n")

        return t

    @staticmethod
    def _status_icon(status: str) -> tuple[str, str]:
        icons = {
            "pending":  ("  ○  ", "dim"),
            "running":  (f"  {_spinner()}  ", "cyan"),
            "done":     ("  ✓  ", "bold green"),
            "error":    ("  ✗  ", "bold red"),
        }
        return icons.get(status, ("  ?  ", "dim"))

    @staticmethod
    def _agent_icon(status: str) -> tuple[str, str]:
        if status == "running":
            return (_spinner(), "cyan")
        icons = {
            "pending": ("●", "dim"),
            "done":    ("●", "green"),
            "error":   ("✗", "red"),
        }
        return icons.get(status, ("●", "dim"))

    @staticmethod
    def _label_style(status: str) -> str:
        return {
            "pending":  "dim",
            "running":  "bold white",
            "done":     "white",
            "error":    "red",
        }.get(status, "dim")

    @staticmethod
    def _agent_style(status: str) -> str:
        return {
            "pending": "dim",
            "running": "bold cyan",
            "done":    "white",
            "error":   "dim red",
        }.get(status, "dim")


# ---------------------------------------------------------------------------
# AgentTestUI — live display for the test-llm command
# ---------------------------------------------------------------------------


class AgentTestUI:
    """Live hacker-style display while testing agent connectivity.

    Usage::

        ui = AgentTestUI(agents=["claude", "codex", "gemini"], console=console)
        console.print(render_banner())
        with ui:
            results = asyncio.run(test_all_agents())
        # Live display disappears (transient=True); print table below
    """

    def __init__(
        self,
        agents: list[str],
        console: Console | None = None,
    ) -> None:
        self._agents = agents
        self._console = console or Console()
        self._status: dict[str, str] = {a: "pending" for a in agents}
        self._elapsed: dict[str, float] = {}
        self._result: dict[str, str] = {}
        self._start: dict[str, float] = {}
        self._lock = threading.Lock()
        self._live = Live(
            _AgentTestRenderable(self),
            refresh_per_second=10,
            console=self._console,
            transient=True,
        )

    def set_testing(self, agent: str) -> None:
        with self._lock:
            self._status[agent] = "testing"
            self._start[agent] = time.monotonic()
        try:
            self._live.refresh()
        except Exception:
            pass

    def set_done(self, agent: str, ok: bool, msg: str) -> None:
        with self._lock:
            self._status[agent] = "done" if ok else "error"
            if agent in self._start:
                self._elapsed[agent] = time.monotonic() - self._start[agent]
            self._result[agent] = msg[:55]
        try:
            self._live.refresh()
        except Exception:
            pass

    def __enter__(self) -> "AgentTestUI":
        self._live.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._live.stop()

    def _render(self) -> Text:
        t = Text()
        for agent in self._agents:
            status = self._status[agent]
            t.append("  ")
            t.append(*self._test_icon(status))
            t.append("  ")
            t.append(f"{agent:<12}", style=self._test_label_style(status))
            if status == "testing":
                elapsed = time.monotonic() - self._start.get(agent, time.monotonic())
                t.append(f" {elapsed:>5.1f}s", style="yellow")
            elif status in ("done", "error"):
                elapsed = self._elapsed.get(agent, 0)
                t.append(f" {elapsed:>5.1f}s", style="dim")
                result = self._result.get(agent, "")
                if result:
                    style = "dim cyan" if status == "done" else "dim red"
                    t.append(f"  {result}", style=style)
            t.append("\n")
        return t

    @staticmethod
    def _test_icon(status: str) -> tuple[str, str]:
        return {
            "pending": ("  ○  ", "dim"),
            "testing": (f"  {_spinner()}  ", "cyan"),
            "done":    ("  ✓  ", "bold green"),
            "error":   ("  ✗  ", "bold red"),
        }.get(status, ("  ?  ", "dim"))

    @staticmethod
    def _test_label_style(status: str) -> str:
        return {
            "pending": "dim",
            "testing": "bold white",
            "done":    "white",
            "error":   "red",
        }.get(status, "dim")
