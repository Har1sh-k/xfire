"""
UI/UX demo scenarios for CrossFire — runs the real HackerUI with synthetic events.

No LLM calls are made. Every structlog event is emitted manually with realistic
delays to mimic actual pipeline timing. Use this to validate visual changes to
the terminal UI without consuming API credits.

Scenarios
---------
1. both_accept     — defence immediately concedes, finding confirmed in one round
2. judge_questions — full two-round debate with judge clarification questions
3. defender_wins   — prosecution makes a case, defence successfully rebuts, finding rejected

Run via:  crossfire demo --ui
          crossfire demo --ui --scenario both_accept
"""

from __future__ import annotations

import asyncio
import structlog as _sl

from crossfire.cli_ui import HackerUI, render_banner, render_stats
from rich.console import Console


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _phase(logger, start_event: str, done_event: str, delay: float, **done_fields) -> None:
    logger.info(start_event)
    await asyncio.sleep(delay)
    logger.info(done_event, **done_fields)


async def _run_pipeline_phases(logger, agents: list[str]) -> None:
    """Emit the six pipeline phases before the debate."""
    logger.info("pipeline.context_building")
    await asyncio.sleep(3.0)
    logger.info("pipeline.context_ready", files=12, repo="acme/webapp")

    logger.info("pipeline.intent_inference", mode="llm_enriched")
    await asyncio.sleep(2.0)
    logger.info("pipeline.intent_ready", purpose="web application", capabilities=6, controls=3)

    logger.info("pipeline.skills_running")
    await asyncio.sleep(2.5)
    logger.info(
        "pipeline.skills_complete",
        skills=["code_navigation", "data_flow", "dependency_analysis", "config_analysis"],
    )

    logger.info("pipeline.agent_reviews")
    logger.info("review.start", agents=agents)
    for agent in agents:
        await asyncio.sleep(4.0)
        logger.info("review.agent_complete", agent=agent, findings=1)
    logger.info("pipeline.reviews_complete", agent_count=len(agents), total_findings=len(agents))

    logger.info("pipeline.synthesizing")
    await asyncio.sleep(1.5)
    logger.info("pipeline.synthesis_complete", merged_findings=1)

    logger.info("pipeline.debate_starting", count=1, budget=3)


async def _end_pipeline(logger) -> None:
    await asyncio.sleep(0.5)
    logger.info("pipeline.debate_complete", debates=1)


def _configure_structlog(ui: HackerUI) -> _sl.BoundLogger:
    _sl.configure(
        processors=[ui.processor],
        wrapper_class=_sl.BoundLogger,
        context_class=dict,
        logger_factory=_sl.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    return _sl.get_logger()


def _print_header(console: Console, title: str, subtitle: str) -> None:
    console.print(render_banner())
    console.print(render_stats(repo="acme/webapp", mode=title))
    from rich.text import Text
    t = Text()
    t.append(f"  {subtitle}", style="dim cyan")
    console.print(t)
    console.print("")


# ---------------------------------------------------------------------------
# Scenario 1 — Both agents accept (defence concedes immediately)
# ---------------------------------------------------------------------------

async def scenario_both_accept(console: Console | None = None) -> None:
    """
    Finding: SQL Injection in /api/users
    Both claude and codex flag it. Defence immediately concedes in round 1.
    Verdict: CONFIRMED HIGH — fastest possible debate path.
    """
    console = console or Console()
    agents = ["claude", "codex"]

    ui = HackerUI(
        repo="acme/webapp",
        mode="demo · scenario 1",
        agents=agents,
        debate_enabled=True,
        show_debate=True,
        console=console,
    )

    _print_header(console, "demo · scenario 1/3", "both agents accept — defence concedes")

    logger = _configure_structlog(ui)

    with ui:
        await _run_pipeline_phases(logger, agents)

        # Debate — single round, defence concedes
        finding = "SQL Injection in /api/users search parameter"
        logger.info("debate.start", finding=finding, severity="high",
                    prosecutor="claude", defense="codex", judge="gemini", has_judge=True)
        await asyncio.sleep(1.0)

        logger.info(
            "debate.argument",
            finding=finding,
            agent="claude",
            role="prosecution",
            position="confirmed",
            argument=(
                "The `search` parameter on line 47 of `api/users.py` is interpolated directly "
                "into a raw SQL string: `f\"SELECT * FROM users WHERE name = '{search}'\"`. "
                "No parameterisation, no escaping. A crafted input like `' OR '1'='1` dumps "
                "the entire users table. I traced the value from the HTTP request through "
                "Flask's `request.args.get()` with zero sanitisation before it reaches the "
                "query. This is a textbook CWE-89."
            ),
        )
        await asyncio.sleep(2.0)

        logger.info(
            "debate.argument",
            finding=finding,
            agent="codex",
            role="defense",
            position="confirmed",
            argument=(
                "I concede. I independently found the same issue on line 47. "
                "The f-string interpolation is unambiguous and there is no ORM, no "
                "prepared statement, and no input validation wrapper anywhere in the call "
                "chain. This is a genuine HIGH severity injection vulnerability."
            ),
        )
        await asyncio.sleep(2.0)

        logger.info(
            "debate.argument",
            finding=finding,
            agent="gemini",
            role="judge",
            position="confirmed",
            argument=(
                "Both reviewers are in agreement. The finding is well-evidenced with a "
                "concrete code path and no mitigating controls. Verdict: CONFIRMED at HIGH "
                "severity. Remediation: replace the f-string query with parameterised "
                "statements — `cursor.execute('SELECT * FROM users WHERE name = %s', (search,))`."
            ),
        )
        await asyncio.sleep(1.5)

        logger.info(
            "debate.verdict",
            finding=finding,
            consensus="confirmed",
            final_severity="high",
            evidence_quality="strong",
        )
        await asyncio.sleep(0.5)

        logger.info("debate.complete", finding=finding, rounds=1, consensus="confirmed")
        await _end_pipeline(logger)


# ---------------------------------------------------------------------------
# Scenario 2 — Full debate with judge clarification questions
# ---------------------------------------------------------------------------

async def scenario_judge_questions(console: Console | None = None) -> None:
    """
    Finding: SSRF via webhook URL parameter
    Round 1: prosecution presents, defence argues the URL is filtered.
    Judge asks two clarifying questions.
    Round 2: rebuttal + counter + judge rules confirmed.
    Verdict: CONFIRMED MEDIUM.
    """
    console = console or Console()
    agents = ["claude", "codex", "gemini"]

    ui = HackerUI(
        repo="acme/webapp",
        mode="demo · scenario 2",
        agents=agents,
        debate_enabled=True,
        show_debate=True,
        console=console,
    )

    _print_header(console, "demo · scenario 2/3", "judge clarification questions — two full rounds")

    logger = _configure_structlog(ui)

    with ui:
        await _run_pipeline_phases(logger, agents)

        finding = "SSRF via user-controlled webhook URL in /api/notifications"
        logger.info("debate.start", finding=finding, severity="medium",
                    prosecutor="claude", defense="codex", judge="gemini", has_judge=True)
        await asyncio.sleep(1.0)

        # Round 1 — prosecution
        logger.info(
            "debate.argument",
            finding=finding,
            agent="claude",
            role="prosecution",
            position="confirmed",
            argument=(
                "In `notifications/webhook.py` line 83 the application issues an outbound "
                "HTTP request to a URL supplied directly from the POST body: "
                "`requests.post(payload['webhook_url'], json=event_data)`. "
                "An attacker can point this at internal infrastructure — the metadata "
                "endpoint, Redis, or any internal HTTP service. I see no allowlist, "
                "no scheme check, and no DNS rebinding protection. CWE-918."
            ),
        )
        await asyncio.sleep(2.5)

        # Round 1 — defence
        logger.info(
            "debate.argument",
            finding=finding,
            agent="codex",
            role="defense",
            position="likely",
            argument=(
                "There is a `_validate_webhook_url()` helper called on line 79 before "
                "the request is made. It checks that the scheme is `https` and calls "
                "`socket.getaddrinfo()` to resolve the host. The prosecution has not "
                "addressed whether this validation is sufficient. If the allowlist covers "
                "private RFC-1918 ranges the risk is materially reduced."
            ),
        )
        await asyncio.sleep(2.5)

        # Judge clarification questions
        logger.info(
            "debate.judge_questions",
            finding=finding,
            agent="gemini",
            questions=(
                "1. Does `_validate_webhook_url()` explicitly block RFC-1918 addresses "
                "(10.x, 172.16-31.x, 192.168.x) and the link-local range 169.254.x.x "
                "(cloud metadata)?\n"
                "2. Is the DNS resolution result cached or re-resolved at request time, "
                "which would leave the door open to DNS-rebinding attacks?"
            ),
        )
        await asyncio.sleep(3.0)

        # Round 2 — rebuttal
        logger.info(
            "debate.argument",
            finding=finding,
            agent="claude",
            role="rebuttal",
            position="confirmed",
            argument=(
                "I reviewed `_validate_webhook_url()`. It only checks `scheme == 'https'` "
                "and does a single DNS resolution — it does NOT check for RFC-1918 or "
                "169.254.0.0/16. The resolved IP is discarded; the actual `requests.post()` "
                "call re-resolves the hostname at connect time, so DNS rebinding is trivially "
                "possible. The validation is cosmetic. SSRF remains unmitigated."
            ),
        )
        await asyncio.sleep(2.5)

        # Round 2 — counter
        logger.info(
            "debate.argument",
            finding=finding,
            agent="codex",
            role="counter",
            position="likely",
            argument=(
                "I accept the rebuttal on DNS rebinding. However the service runs inside "
                "a VPC with strict egress rules — outbound traffic to 169.254.169.254 is "
                "blocked at the network layer according to the terraform config in "
                "`infra/vpc.tf`. The residual risk is therefore reduced but not zero."
            ),
        )
        await asyncio.sleep(2.5)

        # Judge final verdict
        logger.info(
            "debate.argument",
            finding=finding,
            agent="gemini",
            role="judge",
            position="confirmed",
            argument=(
                "The application-level validation is insufficient: RFC-1918 and link-local "
                "ranges are not blocked in code, and DNS rebinding is possible. Network-level "
                "controls provide defence in depth but are not a substitute for input "
                "validation. Verdict: CONFIRMED at MEDIUM — the VPC egress rules lower "
                "exploitability but the vulnerability is real. Fix: validate the resolved "
                "IP against a blocklist before issuing the request."
            ),
        )
        await asyncio.sleep(1.5)

        logger.info(
            "debate.verdict",
            finding=finding,
            consensus="confirmed",
            final_severity="medium",
            evidence_quality="strong",
        )
        await asyncio.sleep(0.5)

        logger.info("debate.complete", finding=finding, rounds=2, consensus="confirmed")
        await _end_pipeline(logger)


# ---------------------------------------------------------------------------
# Scenario 3 — Defender wins (finding rejected)
# ---------------------------------------------------------------------------

async def scenario_defender_wins(console: Console | None = None) -> None:
    """
    Finding: Command injection via shell=True in admin task runner
    Round 1: prosecution flags it. Defence explains it's sandboxed + admin-only.
    Round 2: prosecution rebuts weakly, defence presents conclusive evidence.
    Judge rules: REJECTED — intentional sandboxed capability.
    """
    console = console or Console()
    agents = ["claude", "codex", "gemini"]

    ui = HackerUI(
        repo="acme/webapp",
        mode="demo · scenario 3",
        agents=agents,
        debate_enabled=True,
        show_debate=True,
        console=console,
    )

    _print_header(console, "demo · scenario 3/3", "defender wins — false positive rejected")

    logger = _configure_structlog(ui)

    with ui:
        await _run_pipeline_phases(logger, agents)

        finding = "Command injection via shell=True in admin task runner"
        logger.info("debate.start", finding=finding, severity="high",
                    prosecutor="codex", defense="claude", judge="gemini", has_judge=True)
        await asyncio.sleep(1.0)

        # Round 1 — prosecution
        logger.info(
            "debate.argument",
            finding=finding,
            agent="codex",
            role="prosecution",
            position="confirmed",
            argument=(
                "In `admin/tasks.py` line 112 a task command is executed with "
                "`subprocess.run(cmd, shell=True)` where `cmd` is assembled from a "
                "user-supplied task name: `cmd = f'python manage.py {task_name}'`. "
                "Shell metacharacters in `task_name` allow arbitrary command execution. "
                "This is CWE-78. The severity is HIGH — an attacker who can reach this "
                "endpoint can run arbitrary OS commands."
            ),
        )
        await asyncio.sleep(2.5)

        # Round 1 — defence
        logger.info(
            "debate.argument",
            finding=finding,
            agent="claude",
            role="defense",
            position="rejected",
            argument=(
                "Three mitigating facts the prosecution overlooked:\n"
                "1. The endpoint is behind `@require_role('superadmin')` — only two "
                "accounts have this role, both internal ops engineers.\n"
                "2. `task_name` is validated against an explicit allowlist in "
                "`ALLOWED_TASKS = {'migrate', 'collectstatic', 'clearsessions'}` "
                "on line 108 — any value not in this set raises `ValueError` before "
                "`subprocess.run` is called.\n"
                "3. The process runs inside a read-only Docker container with no "
                "network egress and a restricted seccomp profile per `docker-compose.yml`.\n"
                "This is an intentional, sandboxed admin capability documented in "
                "`docs/ops-runbook.md`. It is not a vulnerability."
            ),
        )
        await asyncio.sleep(3.0)

        # Round 2 — rebuttal (prosecution, weaker)
        logger.info(
            "debate.argument",
            finding=finding,
            agent="codex",
            role="rebuttal",
            position="likely",
            argument=(
                "I acknowledge the allowlist on line 108. However `shell=True` is still "
                "considered a code smell and the allowlist could be bypassed if a future "
                "developer adds a task name containing a shell metacharacter. The defence "
                "relies on defence-in-depth controls that could be removed. I lower my "
                "position to LIKELY but maintain there is residual risk."
            ),
        )
        await asyncio.sleep(2.5)

        # Round 2 — counter (defence, strong)
        logger.info(
            "debate.argument",
            finding=finding,
            agent="claude",
            role="counter",
            position="rejected",
            argument=(
                "The prosecution is now arguing about a hypothetical future state, not the "
                "current code. The allowlist is enforced before `subprocess.run` is ever "
                "reached — no shell metacharacter can survive it. The container restrictions "
                "are independently verified in CI via `tests/security/test_container.py`. "
                "Intent inference explicitly identified this as a known admin capability. "
                "This finding should be REJECTED as a false positive."
            ),
        )
        await asyncio.sleep(2.5)

        # Judge final verdict
        logger.info(
            "debate.argument",
            finding=finding,
            agent="gemini",
            role="judge",
            position="rejected",
            argument=(
                "The defence has provided three independent controls: role-based access "
                "restricting the surface to two accounts, an explicit string allowlist "
                "enforced before the subprocess call, and container-level sandboxing "
                "verified in CI. The prosecution's final position was speculative. "
                "Verdict: REJECTED. This is an intentional, well-controlled admin "
                "capability. No remediation required."
            ),
        )
        await asyncio.sleep(1.5)

        logger.info(
            "debate.verdict",
            finding=finding,
            consensus="rejected",
            final_severity="info",
            evidence_quality="strong",
        )
        await asyncio.sleep(0.5)

        logger.info("debate.complete", finding=finding, rounds=2, consensus="rejected")
        await _end_pipeline(logger)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, object] = {
    "both_accept":     scenario_both_accept,
    "judge_questions": scenario_judge_questions,
    "defender_wins":   scenario_defender_wins,
}

SCENARIO_LABELS: dict[str, str] = {
    "both_accept":     "Scenario 1 — both agents accept (defence concedes)",
    "judge_questions": "Scenario 2 — judge clarification questions, two rounds",
    "defender_wins":   "Scenario 3 — defender wins, finding rejected",
}
