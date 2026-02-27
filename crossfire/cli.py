"""CrossFire CLI — AI-powered PR security review.

Usage:
    crossfire analyze-pr --repo owner/repo --pr 123
    crossfire analyze-diff --patch changes.patch --repo-dir .
    crossfire analyze-diff --staged --repo-dir .
    crossfire init
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()
app = typer.Typer(
    name="crossfire",
    help="Multiple agents. One verdict. Zero blind spots.",
    no_args_is_help=True,
)


def _parse_agents_list(agents: str | None) -> list[str] | None:
    """Parse comma-separated agent list."""
    if not agents:
        return None
    return [a.strip() for a in agents.split(",") if a.strip()]


def _handle_error(message: str, exc: Exception | None = None) -> NoReturn:
    """Print a user-friendly error and exit."""
    console.print(f"[red]Error:[/red] {message}")
    if exc:
        console.print(f"[dim]{type(exc).__name__}: {exc}[/dim]")
    raise typer.Exit(1)


async def _preflight_check(settings) -> dict[str, tuple[bool, str]]:
    """Ping each enabled agent to verify it is reachable before running pipeline.

    For CLI mode: runs `<cli_command> --version` as a subprocess.
    For API mode: checks that the API key env var is set.
    Returns {agent_name: (ok, message)}.
    """
    import asyncio
    import os
    import sys

    results: dict[str, tuple[bool, str]] = {}

    for name, cfg in settings.agents.items():
        if not cfg.enabled:
            continue
        if cfg.mode == "cli":
            cmd = cfg.cli_command
            if sys.platform == "win32":
                import os as _os
                cmd = _os.path.normpath(cmd)
                if cmd.lower().endswith((".cmd", ".bat")):
                    cmd_exe = _os.path.join(
                        os.environ.get("SystemRoot", "C:\\Windows"), "System32", "cmd.exe"
                    )
                    full_cmd = [cmd_exe, "/c", cmd, "--version"]
                else:
                    full_cmd = [cmd, "--version"]
            else:
                full_cmd = [cfg.cli_command, "--version"]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *full_cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                if proc.returncode == 0:
                    version = stdout.decode(errors="replace").strip().split("\n")[0][:40]
                    results[name] = (True, version)
                else:
                    results[name] = (False, f"exited {proc.returncode}")
            except FileNotFoundError:
                results[name] = (False, f"not found: {cfg.cli_command}")
            except asyncio.TimeoutError:
                results[name] = (False, "timed out")
            except Exception as e:
                results[name] = (False, str(e)[:80])
        else:
            # API mode — check key is present
            key = os.environ.get(cfg.api_key_env or "")
            if key:
                results[name] = (True, f"API key set ({cfg.api_key_env})")
            else:
                results[name] = (False, f"API key not set ({cfg.api_key_env})")

    return results


def _print_preflight(results: dict[str, tuple[bool, str]]) -> bool:
    """Print pre-flight results table. Returns True if at least one agent is reachable."""
    from rich.table import Table

    table = Table(title="Agent Pre-flight Check", border_style="dim")
    table.add_column("Agent", style="bold")
    table.add_column("Status")
    table.add_column("Details", style="dim")

    any_ok = False
    for name, (ok, msg) in results.items():
        if ok:
            table.add_row(name, "[green]✓ reachable[/green]", msg)
            any_ok = True
        else:
            table.add_row(name, "[red]✗ unreachable[/red]", msg)

    console.print(table)
    return any_ok


@app.command()
def analyze_pr(
    repo: str = typer.Option(..., help="GitHub repo in owner/repo format"),
    pr: int = typer.Option(..., help="PR number"),
    github_token: str | None = typer.Option(None, envvar="GITHUB_TOKEN", help="GitHub token"),
    agents: str | None = typer.Option(None, help="Comma-separated agent list (claude,codex,gemini)"),
    skip_debate: bool = typer.Option(False, help="Skip adversarial debate phase"),
    context_depth: str | None = typer.Option(None, help="Context depth: shallow|medium|deep"),
    output: str | None = typer.Option(None, help="Output file path"),
    format: str = typer.Option("markdown", help="Output format: markdown|json|sarif"),
    post_comment: bool = typer.Option(False, help="Post review as GitHub PR comment"),
    cache_dir: str | None = typer.Option(
        None, envvar="CROSSFIRE_CACHE_DIR",
        help="Cache directory for context/intent persistence across runs",
    ),
    verbose: bool = typer.Option(False, help="Enable verbose logging"),
    dry_run: bool = typer.Option(False, help="Show what would be analyzed without calling agents"),
) -> None:
    """Analyze a GitHub pull request for security issues."""
    import asyncio

    from crossfire.config.settings import ConfigError, load_settings
    from crossfire.core.orchestrator import CrossFireOrchestrator

    cli_overrides: dict = {}
    if context_depth:
        cli_overrides["analysis"] = {"context_depth": context_depth}

    try:
        settings = load_settings(cli_overrides=cli_overrides)
    except ConfigError as e:
        _handle_error(str(e))

    agent_list = _parse_agents_list(agents)
    if agent_list:
        for name in list(settings.agents.keys()):
            if name not in agent_list:
                settings.agents[name].enabled = False

    if not github_token:
        _handle_error("GitHub token required. Set GITHUB_TOKEN or use --github-token.")

    console.print(Panel(
        f"[bold]CrossFire Security Review[/bold]\n"
        f"Repo: {repo} | PR: #{pr}\n"
        f"Agents: {', '.join(n for n, c in settings.agents.items() if c.enabled)}\n"
        f"Context: {settings.analysis.context_depth} | Debate: {'skip' if skip_debate else 'enabled'}",
        title="🔥 CrossFire",
        border_style="red",
    ))

    if dry_run:
        console.print("[yellow]Dry run mode — would analyze the above, exiting.[/yellow]")
        raise typer.Exit(0)

    orchestrator = CrossFireOrchestrator(settings, cache_dir=cache_dir)
    try:
        report = asyncio.run(orchestrator.analyze_pr(
            repo=repo,
            pr_number=pr,
            github_token=github_token,
            skip_debate=skip_debate,
        ))
    except Exception as e:
        _handle_error(f"Analysis failed: {e}", e)

    _output_report(
        report, format, output, post_comment,
        repo=repo, pr_number=pr, github_token=github_token,
    )

    _check_severity_gate(report, settings)


@app.command()
def analyze_diff(
    patch: str | None = typer.Option(None, help="Path to a diff/patch file"),
    repo_dir: str = typer.Option(".", help="Path to the repository root"),
    staged: bool = typer.Option(False, help="Analyze staged changes in the repo"),
    base: str | None = typer.Option(None, help="Base branch/commit for comparison"),
    head: str | None = typer.Option(None, help="Head branch/commit for comparison"),
    agents: str | None = typer.Option(None, help="Comma-separated agent list"),
    skip_debate: bool = typer.Option(False, help="Skip adversarial debate phase"),
    context_depth: str | None = typer.Option(None, help="Context depth: shallow|medium|deep"),
    output: str | None = typer.Option(None, help="Output file path"),
    format: str = typer.Option("markdown", help="Output format: markdown|json|sarif"),
    cache_dir: str | None = typer.Option(
        None, envvar="CROSSFIRE_CACHE_DIR",
        help="Cache directory for context/intent persistence across runs",
    ),
    verbose: bool = typer.Option(False, help="Enable verbose logging"),
    dry_run: bool = typer.Option(False, help="Show what would be analyzed without calling agents"),
) -> None:
    """Analyze a local diff or staged changes."""
    import asyncio

    from crossfire.config.settings import ConfigError, load_settings
    from crossfire.core.orchestrator import CrossFireOrchestrator

    if not patch and not staged and not (base and head):
        _handle_error("Must specify --patch, --staged, or --base/--head.")

    cli_overrides: dict = {}
    if context_depth:
        cli_overrides["analysis"] = {"context_depth": context_depth}

    try:
        settings = load_settings(repo_dir=repo_dir, cli_overrides=cli_overrides)
    except ConfigError as e:
        _handle_error(str(e))

    agent_list = _parse_agents_list(agents)
    if agent_list:
        for name in list(settings.agents.keys()):
            if name not in agent_list:
                settings.agents[name].enabled = False

    mode = "patch" if patch else ("staged" if staged else "range")
    console.print(Panel(
        f"[bold]CrossFire Security Review[/bold]\n"
        f"Mode: {mode} | Repo: {repo_dir}\n"
        f"Agents: {', '.join(n for n, c in settings.agents.items() if c.enabled)}\n"
        f"Context: {settings.analysis.context_depth} | Debate: {'skip' if skip_debate else 'enabled'}",
        title="🔥 CrossFire",
        border_style="red",
    ))

    if dry_run:
        console.print("[yellow]Dry run mode — would analyze the above, exiting.[/yellow]")
        raise typer.Exit(0)

    orchestrator = CrossFireOrchestrator(settings, cache_dir=cache_dir)
    try:
        report = asyncio.run(orchestrator.analyze_diff(
            repo_dir=repo_dir,
            patch_path=patch,
            staged=staged,
            base_ref=base,
            head_ref=head,
            skip_debate=skip_debate,
        ))
    except FileNotFoundError as e:
        _handle_error(str(e))
    except Exception as e:
        _handle_error(f"Analysis failed: {e}", e)

    _output_report(report, format, output, False)

    _check_severity_gate(report, settings)


@app.command()
def code_review(
    repo_dir: str = typer.Argument(".", help="Path to the repository root"),
    agents: str | None = typer.Option(None, help="Comma-separated: claude,codex,gemini"),
    skip_debate: bool = typer.Option(False, help="Skip adversarial debate phase"),
    max_files: int = typer.Option(150, help="Maximum number of source files to scan"),
    format: str = typer.Option("markdown", help="Output format: markdown|json|sarif"),
    output: str | None = typer.Option(None, help="Output file path"),
    verbose: bool = typer.Option(False, help="Enable verbose logging"),
    dry_run: bool = typer.Option(False, help="Show what would be analyzed without calling agents"),
) -> None:
    """Full codebase security audit — no diff, no PR. Scans the whole repo as-is."""
    import asyncio

    from crossfire.config.settings import ConfigError, load_settings
    from crossfire.core.orchestrator import CrossFireOrchestrator

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    try:
        settings = load_settings(repo_dir=repo_dir)
    except ConfigError as e:
        _handle_error(str(e))

    agent_list = _parse_agents_list(agents)
    if agent_list:
        for name in list(settings.agents.keys()):
            if name not in agent_list:
                settings.agents[name].enabled = False

    console.print(Panel(
        f"[bold]CrossFire Code Review[/bold]\n"
        f"Repo: {repo_dir} | Max files: {max_files}\n"
        f"Agents: {', '.join(n for n, c in settings.agents.items() if c.enabled)}\n"
        f"Debate: {'skip' if skip_debate else 'enabled'}",
        title="🔥 CrossFire",
        border_style="red",
    ))

    if dry_run:
        console.print("[yellow]Dry run mode — would audit the above, exiting.[/yellow]")
        raise typer.Exit(0)

    # Pre-flight: verify agents are reachable
    console.print("[dim]Checking agent reachability...[/dim]")
    preflight = asyncio.run(_preflight_check(settings))
    any_ok = _print_preflight(preflight)
    if not any_ok:
        _handle_error(
            "No agents are reachable. Check your CLI tool paths in .crossfire/config.yaml."
        )

    orchestrator = CrossFireOrchestrator(settings)
    try:
        report = asyncio.run(orchestrator.code_review(
            repo_dir=repo_dir,
            max_files=max_files,
            skip_debate=skip_debate,
        ))
    except Exception as e:
        _handle_error(f"Code review failed: {e}", e)

    _output_report(report, format, output, False)
    _check_severity_gate(report, settings)


@app.command()
def baseline(
    repo_dir: str = typer.Argument(".", help="Path to the repository root"),
    force: bool = typer.Option(False, help="Rebuild baseline even if one already exists"),
    verbose: bool = typer.Option(False, help="Enable verbose logging"),
) -> None:
    """Build persistent repo baseline context in .crossfire/baseline/."""
    from crossfire.config.settings import ConfigError, load_settings
    from crossfire.core.baseline import BaselineManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    try:
        settings = load_settings(repo_dir=repo_dir)
    except ConfigError as e:
        _handle_error(str(e))

    mgr = BaselineManager(repo_dir)

    if mgr.exists() and not force:
        console.print(
            "[yellow]Baseline already exists at .crossfire/baseline/[/yellow]\n"
            "Use [bold]--force[/bold] to rebuild."
        )
        # Show summary of existing baseline
        try:
            b = mgr.load()
            console.print(f"  Purpose: {b.intent.repo_purpose[:120]}")
            console.print(f"  Capabilities: {len(b.intent.intended_capabilities)}")
            console.print(f"  Trust boundaries: {len(b.intent.trust_boundaries)}")
            console.print(f"  Security controls: {len(b.intent.security_controls_detected)}")
            if b.scan_state:
                console.print(f"  Baseline commit: {b.scan_state.baseline_commit[:12] or 'unknown'}")
                console.print(f"  Known findings: {len(b.known_findings)}")
        except Exception:
            pass
        return

    console.print(Panel(
        f"[bold]CrossFire Baseline Builder[/bold]\n"
        f"Repo: {repo_dir}",
        title="🔥 CrossFire",
        border_style="yellow",
    ))

    import asyncio

    # Pre-flight check
    console.print("[dim]Checking agent reachability...[/dim]")
    preflight = asyncio.run(_preflight_check(settings))
    _print_preflight(preflight)

    # Use Claude Sonnet for LLM-based threat model if reachable
    from crossfire.agents.claude_adapter import ClaudeAgent
    claude_cfg = settings.agents.get("claude")
    intent_agent = (
        ClaudeAgent(claude_cfg)
        if claude_cfg and claude_cfg.enabled and preflight.get("claude", (False,))[0]
        else None
    )
    if intent_agent:
        console.print("[dim]Using Claude Sonnet for threat-model-quality intent inference...[/dim]")
    else:
        console.print("[yellow]Claude unreachable — using heuristic intent inference.[/yellow]")

    try:
        b = mgr.build(settings=settings, agent=intent_agent)
    except Exception as e:
        _handle_error(f"Baseline build failed: {e}", e)

    console.print("[green]Baseline built successfully.[/green]")
    console.print(f"  Purpose: {b.intent.repo_purpose[:120]}")
    console.print(f"  Capabilities detected: {len(b.intent.intended_capabilities)}")
    console.print(f"  Trust boundaries: {len(b.intent.trust_boundaries)}")
    console.print(f"  Security controls: {len(b.intent.security_controls_detected)}")
    console.print(f"  Sensitive paths: {len(b.intent.sensitive_paths)}")
    console.print(f"\nFiles written to: {repo_dir}/.crossfire/baseline/")


@app.command()
def scan(
    repo_dir: str = typer.Argument(".", help="Path to the repository root"),
    # Input mode options (exactly one required)
    base: str | None = typer.Option(None, help="Base branch/commit (use with --head)"),
    head: str | None = typer.Option(None, help="Head branch/commit (use with --base)"),
    range: str | None = typer.Option(None, "--range", help="Commit range e.g. abc123~1..abc123"),
    diff: str | None = typer.Option(None, "--diff", help="Path to a .patch file"),
    since_last_scan: bool = typer.Option(False, help="Scan all commits since last scan"),
    since: str | None = typer.Option(None, "--since", help="All commits since date (2026-02-01)"),
    last: int | None = typer.Option(None, "--last", help="Last N commits"),
    # Standard options
    agents: str | None = typer.Option(None, help="Comma-separated: claude,codex,gemini"),
    skip_debate: bool = typer.Option(False, help="Skip adversarial debate phase"),
    context_depth: str | None = typer.Option(None, help="Context depth: shallow|medium|deep"),
    format: str = typer.Option("markdown", help="Output format: markdown|json|sarif"),
    output: str | None = typer.Option(None, help="Output file path"),
    verbose: bool = typer.Option(False, help="Enable verbose logging"),
    dry_run: bool = typer.Option(False, help="Show what would be analyzed without calling agents"),
) -> None:
    """Baseline-aware scan: auto-builds baseline, runs full pipeline, skips known findings."""
    import asyncio

    from crossfire.agents.fast_model import FastModel
    from crossfire.config.settings import ConfigError, load_settings
    from crossfire.core.baseline import BaselineManager
    from crossfire.core.diff_resolver import DiffResolver, DiffResolverError
    from crossfire.core.orchestrator import CrossFireOrchestrator

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    # Validate input modes — exactly one required
    mode_count = sum([
        bool(base and head),
        bool(range),
        bool(diff),
        bool(since_last_scan),
        bool(since),
        bool(last),
    ])
    if mode_count == 0:
        _handle_error(
            "Must specify one input mode: --base/--head, --range, --diff, "
            "--since-last-scan, --since, or --last."
        )
    if mode_count > 1:
        _handle_error("Only one input mode can be used at a time.")

    cli_overrides: dict = {}
    if context_depth:
        cli_overrides["analysis"] = {"context_depth": context_depth}

    try:
        settings = load_settings(repo_dir=repo_dir, cli_overrides=cli_overrides)
    except ConfigError as e:
        _handle_error(str(e))

    agent_list = _parse_agents_list(agents)
    if agent_list:
        for name in list(settings.agents.keys()):
            if name not in agent_list:
                settings.agents[name].enabled = False

    # Resolve diff
    try:
        if base and head:
            diff_result = DiffResolver.from_refs(repo_dir, base, head)
            mode_desc = f"{base}..{head}"
        elif range:
            diff_result = DiffResolver.from_range(repo_dir, range)
            mode_desc = range
        elif diff:
            diff_result = DiffResolver.from_patch(diff, repo_dir)
            mode_desc = f"patch:{diff}"
        elif since_last_scan:
            mgr_tmp = BaselineManager(repo_dir)
            scan_state = None
            if mgr_tmp.exists():
                b_tmp = mgr_tmp.load()
                scan_state = b_tmp.scan_state
            diff_result = DiffResolver.from_since_last_scan(repo_dir, scan_state or object())
            mode_desc = "since-last-scan"
        elif since:
            diff_result = DiffResolver.from_since_date(repo_dir, since)
            mode_desc = f"since:{since}"
        else:
            diff_result = DiffResolver.from_last_n(repo_dir, last)
            mode_desc = f"last-{last}-commits"
    except DiffResolverError as e:
        _handle_error(str(e))
    except Exception as e:
        _handle_error(f"Failed to resolve diff: {e}", e)

    base_label = diff_result.base_commit[:12] if diff_result.base_commit else "unknown"
    head_label = diff_result.head_commit[:12] if diff_result.head_commit else "unknown"
    console.print(Panel(
        f"[bold]CrossFire Scan[/bold]\n"
        f"Repo: {repo_dir} | Range: {mode_desc}\n"
        f"Base: {base_label} → Head: {head_label} | "
        f"Diff: {diff_result.diff_text.count(chr(10))} lines\n"
        f"Baseline built from: {base_label} (state before diff)\n"
        f"Agents: {', '.join(n for n, c in settings.agents.items() if c.enabled)}\n"
        f"Context: {settings.analysis.context_depth} | Debate: {'skip' if skip_debate else 'enabled'}",
        title="🔥 CrossFire",
        border_style="red",
    ))

    if dry_run:
        console.print("[yellow]Dry run mode — would scan the above, exiting.[/yellow]")
        raise typer.Exit(0)

    if not diff_result.diff_text.strip():
        console.print("[yellow]No diff content found — nothing to scan.[/yellow]")
        raise typer.Exit(0)

    # Pre-flight: verify agents are reachable before starting
    console.print("[dim]Checking agent reachability...[/dim]")
    preflight = asyncio.run(_preflight_check(settings))
    any_ok = _print_preflight(preflight)
    if not any_ok:
        _handle_error(
            "No agents are reachable. Check your CLI tool paths in .crossfire/config.yaml."
        )

    # Build Claude agent for LLM-based intent/threat-model inference
    from crossfire.agents.claude_adapter import ClaudeAgent
    claude_cfg = settings.agents.get("claude")
    intent_agent = ClaudeAgent(claude_cfg) if claude_cfg and claude_cfg.enabled and preflight.get("claude", (False,))[0] else None

    # Baseline management
    mgr = BaselineManager(repo_dir)
    fast_model = FastModel(settings.fast_model)

    baseline_obj = None

    # base_commit is the "before" state — baseline should reflect the repo
    # at that point, not whatever is currently checked out.
    base_ref = diff_result.base_commit

    if not mgr.exists():
        console.print(
            "[yellow]No baseline found. Building baseline first (threat modelling with Sonnet)...[/yellow]"
        )
        if base_ref:
            console.print(f"[dim]  Reading repo state from base commit {base_ref[:12]}[/dim]")
        try:
            baseline_obj = mgr.build(settings=settings, base_ref=base_ref, agent=intent_agent)
            console.print("[green]Baseline built.[/green]")
        except Exception as e:
            _handle_error(f"Baseline build failed: {e}", e)
    else:
        # Check if intent changed
        console.print("[dim]Checking if diff changes repo intent...[/dim]")
        try:
            intent_changed = asyncio.run(
                mgr.check_intent_changed(diff_result.diff_text, fast_model)
            )
            if intent_changed:
                console.print(
                    "[yellow]Diff changes repo security model — rebuilding baseline "
                    f"from {base_ref[:12] if base_ref else 'working tree'}...[/yellow]"
                )
                try:
                    baseline_obj = mgr.build(settings=settings, base_ref=base_ref, agent=intent_agent)
                    console.print("[green]Baseline rebuilt.[/green]")
                except Exception as e:
                    _handle_error(f"Baseline rebuild failed: {e}", e)
        except Exception as e:
            console.print(f"[dim]Intent check error ({e}) — using existing baseline.[/dim]")

    # Load baseline
    if baseline_obj is None:
        try:
            baseline_obj = mgr.load()
        except Exception as e:
            _handle_error(f"Failed to load baseline: {e}", e)

    # Run pipeline
    orchestrator = CrossFireOrchestrator(settings)
    try:
        report_result = asyncio.run(orchestrator.scan_with_baseline(
            repo_dir=repo_dir,
            diff_result=diff_result,
            baseline=baseline_obj,
            fast_model=fast_model,
            skip_debate=skip_debate,
        ))
    except Exception as e:
        _handle_error(f"Scan failed: {e}", e)

    # Print delta summary
    console.print(f"\n[bold]Scan complete.[/bold] {report_result.summary}")

    _output_report(report_result, format, output, False)
    _check_severity_gate(report_result, settings)


@app.command()
def report(
    input: str = typer.Option(..., help="Path to a CrossFire JSON results file"),
    format: str = typer.Option("markdown", help="Output format: markdown|json|sarif"),
    output: str | None = typer.Option(None, help="Output file path"),
) -> None:
    """Generate a report from existing analysis results."""
    from crossfire.core.models import CrossFireReport

    input_path = Path(input)
    if not input_path.exists():
        _handle_error(f"Input file not found: {input}")

    try:
        data = json.loads(input_path.read_text())
    except json.JSONDecodeError as e:
        _handle_error(f"Invalid JSON in {input}: {e}")

    try:
        cf_report = CrossFireReport(**data)
    except Exception as e:
        _handle_error(f"Invalid report schema in {input}: {e}")

    _output_report(cf_report, format, output, False)


@app.command()
def init() -> None:
    """Initialize CrossFire configuration in the current repository."""
    config_dir = Path.cwd() / ".crossfire"
    config_dir.mkdir(exist_ok=True)

    config_file = config_dir / "config.yaml"
    if config_file.exists():
        console.print("[yellow]Config already exists at .crossfire/config.yaml[/yellow]")
        raise typer.Exit(0)

    example = Path(__file__).parent.parent / ".crossfire" / "config.example.yaml"
    if example.exists():
        shutil.copy(example, config_file)
    else:
        config_file.write_text(_default_config_yaml())

    console.print("[green]Created .crossfire/config.yaml[/green]")
    console.print("Edit the config to customize your security review settings.")


@app.command()
def config_check(
    repo_dir: str = typer.Option(".", help="Path to the repository root"),
) -> None:
    """Validate the CrossFire configuration."""
    from crossfire.config.settings import ConfigError, load_settings

    try:
        settings = load_settings(repo_dir=repo_dir)
        console.print("[green]Configuration is valid.[/green]")
        console.print(f"  Agents: {', '.join(n for n, c in settings.agents.items() if c.enabled)}")
        console.print(f"  Context depth: {settings.analysis.context_depth}")
        console.print(f"  Debate: {settings.debate.role_assignment}")
        console.print(f"  Severity gate: fail on {settings.severity_gate.fail_on}+")
    except (ConfigError, Exception) as e:
        _handle_error(f"Configuration error: {e}")


@app.command(name="test-llm")
def test_llm(
    repo_dir: str = typer.Option(".", help="Path to the repository root"),
    agents: str | None = typer.Option(None, help="Comma-separated agent list to test (default: all enabled)"),
    timeout: int = typer.Option(30, help="Timeout in seconds per agent"),
) -> None:
    """Test LLM connectivity by sending a small prompt to each enabled agent."""
    import asyncio
    import time as _time

    from rich.table import Table

    from crossfire.agents.review_engine import AGENT_CLASSES
    from crossfire.config.settings import ConfigError, load_settings

    try:
        settings = load_settings(repo_dir=repo_dir)
    except ConfigError as e:
        _handle_error(str(e))

    agent_list = _parse_agents_list(agents)
    if agent_list:
        for name in list(settings.agents.keys()):
            if name not in agent_list:
                settings.agents[name].enabled = False

    enabled = {n: c for n, c in settings.agents.items() if c.enabled}
    if not enabled:
        _handle_error("No agents are enabled. Check .crossfire/config.yaml.")

    console.print(
        f"[dim]Testing {len(enabled)} agent(s): {', '.join(enabled.keys())}[/dim]\n"
    )

    test_prompt = "Respond with exactly one word: hello"
    test_system = "You are a connectivity test. Respond as briefly as possible."

    async def _test_agent(name: str, config) -> tuple[str, bool, str, float]:
        cls = AGENT_CLASSES.get(name)
        if not cls:
            return (name, False, f"unknown agent type: {name}", 0.0)
        agent = cls(config)
        t0 = _time.monotonic()
        try:
            raw = await asyncio.wait_for(
                agent.execute(test_prompt, test_system),
                timeout=timeout,
            )
            elapsed = _time.monotonic() - t0
            snippet = raw.strip().replace("\n", " ")[:60]
            return (name, True, snippet, elapsed)
        except asyncio.TimeoutError:
            return (name, False, f"timed out after {timeout}s", _time.monotonic() - t0)
        except Exception as e:
            return (name, False, str(e)[:80], _time.monotonic() - t0)

    async def _run_all():
        return await asyncio.gather(
            *[_test_agent(n, c) for n, c in enabled.items()]
        )

    results = asyncio.run(_run_all())

    table = Table(title="LLM Connectivity Test", border_style="dim")
    table.add_column("Agent", style="bold")
    table.add_column("Status")
    table.add_column("Response", style="dim")
    table.add_column("Latency", justify="right")

    all_ok = True
    for name, ok, msg, elapsed in results:
        latency = f"{elapsed:.1f}s"
        if ok:
            table.add_row(name, "[green]connected[/green]", msg, latency)
        else:
            table.add_row(name, "[red]failed[/red]", msg, latency)
            all_ok = False

    console.print(table)

    if all_ok:
        console.print(f"\n[green]All {len(enabled)} agent(s) connected successfully.[/green]")
    else:
        failed = sum(1 for _, ok, _, _ in results if not ok)
        console.print(f"\n[red]{failed}/{len(enabled)} agent(s) failed.[/red]")
        raise typer.Exit(1)


@app.command()
def demo(
    fixture: str = typer.Option(..., help="Fixture name (e.g., auth_bypass_regression)"),
    format: str = typer.Option("markdown", help="Output format: markdown|json|sarif"),
    verbose: bool = typer.Option(False, help="Enable verbose logging"),
) -> None:
    """Run analysis against a fixture PR for testing/demo."""
    fixtures_dir = Path(__file__).parent.parent / "tests" / "fixtures" / "prs" / fixture
    if not fixtures_dir.exists():
        console.print(f"[red]Error:[/red] Fixture not found: {fixture}")
        available = [
            p.name for p in (Path(__file__).parent.parent / "tests" / "fixtures" / "prs").iterdir()
            if p.is_dir()
        ]
        console.print(f"Available fixtures: {', '.join(available)}")
        raise typer.Exit(1)

    import asyncio

    from crossfire.config.settings import load_settings
    from crossfire.core.context_builder import parse_diff
    from crossfire.core.models import PRContext
    from crossfire.core.orchestrator import CrossFireOrchestrator

    console.print(Panel(
        f"[bold]CrossFire Demo[/bold]\n"
        f"Fixture: {fixture}",
        title="🔥 CrossFire",
        border_style="red",
    ))

    # Load fixture data
    diff_path = fixtures_dir / "diff.patch"
    context_path = fixtures_dir / "context.json"

    if not diff_path.exists():
        _handle_error(f"diff.patch not found in fixture {fixture}")

    diff_text = diff_path.read_text(errors="replace")
    files = parse_diff(diff_text)

    # Load context metadata
    context_meta: dict = {}
    if context_path.exists():
        context_meta = json.loads(context_path.read_text())

    pr_context = PRContext(
        repo_name=context_meta.get("repo_name", f"fixture/{fixture}"),
        pr_title=context_meta.get("pr_title", fixture.replace("_", " ").title()),
        pr_description=context_meta.get("pr_description", ""),
        files=files,
    )

    settings = load_settings()
    orchestrator = CrossFireOrchestrator(settings)
    report_result = asyncio.run(orchestrator._run_pipeline(pr_context, skip_debate=False))

    _output_report(report_result, format, None, False)


def _check_severity_gate(report: object, settings: object) -> None:
    """Check severity gate and exit with code 1 if findings breach the threshold."""
    from crossfire.config.settings import CrossFireSettings
    from crossfire.core.models import CrossFireReport
    from crossfire.core.severity import should_fail_ci

    if not isinstance(report, CrossFireReport):
        raise TypeError(f"Expected CrossFireReport, got {type(report).__name__}")
    if not isinstance(settings, CrossFireSettings):
        raise TypeError(f"Expected CrossFireSettings, got {type(settings).__name__}")

    if should_fail_ci(
        findings=report.findings,
        fail_on=settings.severity_gate.fail_on,
        min_confidence=settings.severity_gate.min_confidence,
        require_debate=settings.severity_gate.require_debate,
    ):
        console.print(
            f"[red]Severity gate FAILED:[/red] findings at or above "
            f"{settings.severity_gate.fail_on} severity with confidence >= "
            f"{settings.severity_gate.min_confidence}"
        )
        raise typer.Exit(1)


def _output_report(
    report: object,
    fmt: str,
    output_path: str | None,
    post_comment: bool,
    repo: str | None = None,
    pr_number: int | None = None,
    github_token: str | None = None,
) -> None:
    """Format and output the report."""
    import asyncio

    from crossfire.core.models import CrossFireReport
    from crossfire.output.json_report import generate_json_report
    from crossfire.output.markdown_report import generate_markdown_report
    from crossfire.output.sarif_report import generate_sarif_report

    if not isinstance(report, CrossFireReport):
        raise TypeError(f"Expected CrossFireReport, got {type(report).__name__}")

    if fmt == "json":
        content = generate_json_report(report)
    elif fmt == "sarif":
        content = generate_sarif_report(report)
    else:
        content = generate_markdown_report(report)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        console.print(f"[green]Report written to {output_path}[/green]")
    else:
        console.print(content)

    if post_comment and repo and pr_number and github_token:
        from crossfire.integrations.github.comment_poster import post_review_comment

        # Always post markdown format as the PR comment
        md_content = generate_markdown_report(report)
        success = asyncio.run(post_review_comment(
            repo=repo,
            pr_number=pr_number,
            token=github_token,
            body=md_content,
        ))
        if success:
            console.print(f"[green]Review comment posted to {repo}#{pr_number}[/green]")
        else:
            console.print(f"[red]Failed to post review comment to {repo}#{pr_number}[/red]")


def _default_config_yaml() -> str:
    """Return default config YAML content."""
    return """\
# CrossFire Configuration

repo:
  purpose: ""
  intended_capabilities: []
  sensitive_paths:
    - "auth/"
    - "payments/"
    - "migrations/"

analysis:
  context_depth: deep
  max_related_files: 20
  include_test_files: true

agents:
  claude:
    enabled: true
    mode: cli
    cli_command: "claude"
    cli_args: ["--output-format", "json"]
    model: "claude-sonnet-4-20250514"
    api_key_env: "ANTHROPIC_API_KEY"
    timeout: 300
  codex:
    enabled: true
    mode: cli
    cli_command: "codex"
    cli_args: []
    model: "o3-mini"
    api_key_env: "OPENAI_API_KEY"
    timeout: 300
  gemini:
    enabled: true
    mode: cli
    cli_command: "gemini"
    cli_args: []
    model: "gemini-2.5-pro"
    api_key_env: "GOOGLE_API_KEY"
    timeout: 300

  debate:
    role_assignment: evidence
    fixed_roles:
      prosecutor: claude
      defense: codex
      judge: gemini
    defense_preference: [codex, claude, gemini]
    judge_preference: [codex, gemini, claude]
    max_rounds: 2
    require_evidence_citations: true
    min_agents_for_debate: 2

  skills:
    code_navigation: true
    data_flow_tracing: true
    git_archeology: true
    config_analysis: true
    dependency_analysis: true
    test_coverage_check: true

severity_gate:
  fail_on: high
  min_confidence: 0.7
  require_debate: true

suppressions: []
"""


if __name__ == "__main__":
    app()
