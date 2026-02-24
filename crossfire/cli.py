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
from typing import Optional

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


def _handle_error(message: str, exc: Exception | None = None) -> None:
    """Print a user-friendly error and exit."""
    console.print(f"[red]Error:[/red] {message}")
    if exc:
        console.print(f"[dim]{type(exc).__name__}: {exc}[/dim]")
    raise typer.Exit(1)


@app.command()
def analyze_pr(
    repo: str = typer.Option(..., help="GitHub repo in owner/repo format"),
    pr: int = typer.Option(..., help="PR number"),
    github_token: Optional[str] = typer.Option(None, envvar="GITHUB_TOKEN", help="GitHub token"),
    agents: Optional[str] = typer.Option(None, help="Comma-separated agent list (claude,codex,gemini)"),
    skip_debate: bool = typer.Option(False, help="Skip adversarial debate phase"),
    context_depth: Optional[str] = typer.Option(None, help="Context depth: shallow|medium|deep"),
    output: Optional[str] = typer.Option(None, help="Output file path"),
    format: str = typer.Option("markdown", help="Output format: markdown|json|sarif"),
    post_comment: bool = typer.Option(False, help="Post review as GitHub PR comment"),
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
        return  # unreachable, for type checker

    agent_list = _parse_agents_list(agents)
    if agent_list:
        for name in list(settings.agents.keys()):
            if name not in agent_list:
                settings.agents[name].enabled = False

    if not github_token:
        _handle_error("GitHub token required. Set GITHUB_TOKEN or use --github-token.")
        return

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

    orchestrator = CrossFireOrchestrator(settings)
    try:
        report = asyncio.run(orchestrator.analyze_pr(
            repo=repo,
            pr_number=pr,
            github_token=github_token,
            skip_debate=skip_debate,
        ))
    except Exception as e:
        _handle_error(f"Analysis failed: {e}", e)
        return

    _output_report(
        report, format, output, post_comment,
        repo=repo, pr_number=pr, github_token=github_token,
    )

    _check_severity_gate(report, settings)


@app.command()
def analyze_diff(
    patch: Optional[str] = typer.Option(None, help="Path to a diff/patch file"),
    repo_dir: str = typer.Option(".", help="Path to the repository root"),
    staged: bool = typer.Option(False, help="Analyze staged changes in the repo"),
    base: Optional[str] = typer.Option(None, help="Base branch/commit for comparison"),
    head: Optional[str] = typer.Option(None, help="Head branch/commit for comparison"),
    agents: Optional[str] = typer.Option(None, help="Comma-separated agent list"),
    skip_debate: bool = typer.Option(False, help="Skip adversarial debate phase"),
    context_depth: Optional[str] = typer.Option(None, help="Context depth: shallow|medium|deep"),
    output: Optional[str] = typer.Option(None, help="Output file path"),
    format: str = typer.Option("markdown", help="Output format: markdown|json|sarif"),
    verbose: bool = typer.Option(False, help="Enable verbose logging"),
    dry_run: bool = typer.Option(False, help="Show what would be analyzed without calling agents"),
) -> None:
    """Analyze a local diff or staged changes."""
    import asyncio

    from crossfire.config.settings import ConfigError, load_settings
    from crossfire.core.orchestrator import CrossFireOrchestrator

    if not patch and not staged and not (base and head):
        _handle_error("Must specify --patch, --staged, or --base/--head.")
        return

    cli_overrides: dict = {}
    if context_depth:
        cli_overrides["analysis"] = {"context_depth": context_depth}

    try:
        settings = load_settings(repo_dir=repo_dir, cli_overrides=cli_overrides)
    except ConfigError as e:
        _handle_error(str(e))
        return

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

    orchestrator = CrossFireOrchestrator(settings)
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
        return
    except Exception as e:
        _handle_error(f"Analysis failed: {e}", e)
        return

    _output_report(report, format, output, False)

    _check_severity_gate(report, settings)


@app.command()
def report(
    input: str = typer.Option(..., help="Path to a CrossFire JSON results file"),
    format: str = typer.Option("markdown", help="Output format: markdown|json|sarif"),
    output: Optional[str] = typer.Option(None, help="Output file path"),
) -> None:
    """Generate a report from existing analysis results."""
    from crossfire.core.models import CrossFireReport

    input_path = Path(input)
    if not input_path.exists():
        _handle_error(f"Input file not found: {input}")
        return

    try:
        data = json.loads(input_path.read_text())
    except json.JSONDecodeError as e:
        _handle_error(f"Invalid JSON in {input}: {e}")
        return

    try:
        cf_report = CrossFireReport(**data)
    except Exception as e:
        _handle_error(f"Invalid report schema in {input}: {e}")
        return

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
        return

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
    role_assignment: rotate
    fixed_roles:
      prosecutor: claude
      defense: codex
      judge: gemini
    enable_rebuttal: true
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
