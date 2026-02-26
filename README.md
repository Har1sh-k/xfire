# CrossFire

> _"Multiple agents. One verdict. Zero blind spots."_

CrossFire is an AI-powered PR security review tool that catches vulnerabilities the way a senior security engineer would — by reading and reasoning about code, not running regex scanners.

It runs multiple AI agents independently, has them debate every finding, and only flags what survives adversarial cross-examination.

---

## How It Works

CrossFire has three pipelines:

| Pipeline | Command | What it reviews |
|----------|---------|-----------------|
| **Code Review** | `code-review .` | Entire repo as-is — no diff, no PR, no commits |
| **PR Review** | `analyze-pr` | A GitHub pull request diff via GitHub API |
| **Baseline-Aware Scan** | `scan .` | Any commit range, auto-builds baseline, delta scanning |

---

### Code Review Pipeline

Audits the entire codebase as it currently stands. No diff, no PR, no commit context — agents read every source file and assess the full security posture.

```
crossfire code-review .
        |
        v
  ContextBuilder.build_from_repo(repo_dir)
  (walks all source files — .py, .ts, .go, .rs, .yaml, etc.)
  (no diff hunks; full file contents for every file)
        |
        v
  Intent Inferrer
  (README, package metadata, file structure, dependencies →
   what does this repo DO? what trust boundaries exist?)
        |
        v
  Skills (pre-compute context signals)
  (data flow, git blame, config risks, dependency analysis, test gaps)
        |
        v
  Independent Agent Reviews  [Claude | Codex | Gemini]
  (CODE_REVIEW_SYSTEM_PROMPT — "audit the codebase", not "review a PR")
  (each agent reads full file contents, traces data flows end-to-end)
        |
        v
  Finding Synthesizer
  (cluster duplicates, cross-validation boost, purpose-aware adjustments)
        |
        v
  Adversarial Debate (per finding)
  Round 1: Prosecutor argues → Defense responds
  Round 2: Judge asks clarifying questions (if defense disagrees)
           Both sides respond → Judge issues final ruling
        |
        v
  Policy Engine  →  Output (Markdown / JSON / SARIF)
```

---

### PR Review Pipeline

Reviews a GitHub pull request — what changed, what security implications those changes introduce.

```
crossfire analyze-pr --repo owner/repo --pr 123
        |
        v
  GitHub API → diff, file contents (head + base), commits, metadata
        |
        v
  Context Builder + Intent Inferrer
  (diff hunks, full changed files, git history, configs)
        |
        v
  Skills + Independent Reviews + Debate + Policy
        |
        v
  Output (Markdown / JSON / SARIF / GitHub PR comment)
```

---

### Baseline-Aware Scan Pipeline

```
crossfire scan . --base main --head feature
        |
        v
  DiffResolver → diff_text, head_commit, base_commit
        |
        v
  BaselineManager.exists()?
    NO  → build baseline (IntentInferrer on whole repo)
          write .crossfire/baseline/context.md + intent.json
    YES → FastModel (claude-haiku-4-5): "does this diff change security model?"
          if yes → rebuild baseline
        |
        v
  baseline.load() → context.md, intent, known_findings
        |
        v
  FastModel → build_context_system_prompt(baseline, diff[:2000])
    → repo-specific system prompt (replaces generic REVIEW_SYSTEM_PROMPT)
        |
        v
  ContextBuilder.build_from_diff() → PRContext
        |
        v
  Skills (same as stateless pipeline)
        |
        v
  ReviewEngine.run_independent_reviews(..., system_prompt=context_prompt)
        |
        v
  FindingSynthesizer → merged findings
        |
        v
  baseline.filter_known(findings)
    → (new_findings, known_skipped)    ← delta scanning
        |
        v
  DebateEngine on new_findings only
        |
        v
  PolicyEngine.apply()
        |
        v
  [AUTO] baseline.update_after_scan(commit, confirmed)
    → scan_state.json + known_findings.json updated
        |
        v
  CrossFireReport  →  "X new findings | Y known findings skipped"
```

### Why this works

- **No SAST, no rules engine** — agents read and reason, they don't pattern-match
- **Three pipelines for every scenario** — whole-repo audit, PR diff review, or continuous baseline-aware scanning
- **Purpose-aware** — intent inference understands what the repo is supposed to do, so "intended capabilities" aren't flagged as bugs
- **Independent reviews** — agents never see each other's output during review; blind spots from one are caught by another
- **Adversarial debate** — every finding is stress-tested before it reaches you; false positives get eliminated, not passed through
- **Skills provide grounding** — data flow traces, git blame, dependency diffs, and config analysis give agents concrete evidence to argue from
- **Delta scanning** — confirmed findings are fingerprinted and persisted; repeat scans only debate what's new
- **Repo-specific prompts** — the fast model adapts the generic audit template to your repo's exact capabilities and trust boundaries

---

## Installation

Requires Python 3.11+.

```bash
pip install crossfire
```

Or install from source:

```bash
git clone https://github.com/your-org/crossfire
cd crossfire
pip install -e ".[dev]"
```

---

## Quick Start

```bash
# Initialize config in your repo
crossfire init

# --- Code Review Pipeline (whole repo, no diff) ---

# Audit the entire codebase as-is
crossfire code-review .

# Limit to 50 files (faster, good for large repos)
crossfire code-review . --max-files 50

# --- PR Review Pipeline (GitHub PR diff) ---

# Analyze a GitHub PR
crossfire analyze-pr --repo owner/repo --pr 123 --github-token $GITHUB_TOKEN

# Analyze a local patch file
crossfire analyze-diff --patch changes.patch --repo-dir /path/to/repo

# Analyze staged changes before committing
crossfire analyze-diff --staged --repo-dir .

# --- Baseline-Aware Scan Pipeline (persistent, delta scanning) ---

# Build repo baseline once (or run scan — it auto-builds)
crossfire baseline .

# Scan last 5 commits (auto-builds baseline if missing)
crossfire scan . --last 5

# Scan a branch range
crossfire scan . --base main --head feature-branch

# Scan a specific commit range
crossfire scan . --range HEAD~3..HEAD

# Scan all commits since last scan
crossfire scan . --since-last-scan

# Scan all commits since a date
crossfire scan . --since 2026-02-01

# --- Utilities ---

# Check your config is valid
crossfire config-check

# Run a demo with a built-in fixture
crossfire demo --fixture auth_bypass_regression
```

---

## Configuration

Run `crossfire init` to generate `.crossfire/config.yaml` in your repo, or create it manually:

```yaml
repo:
  purpose: ""                          # override if intent inference gets it wrong
  intended_capabilities: []            # capabilities that should NOT be flagged
  sensitive_paths:
    - "auth/"
    - "payments/"
    - "migrations/"

analysis:
  context_depth: deep                  # shallow | medium | deep
  max_related_files: 20
  include_test_files: true

agents:
  claude:
    enabled: true
    mode: cli                          # cli | api
    cli_command: "claude"
    cli_args: ["--output-format", "json"]
    model: "claude-sonnet-4-20250514"
    api_key_env: "ANTHROPIC_API_KEY"
    timeout: 300
  codex:
    enabled: true
    mode: cli
    cli_command: "codex"
    model: "o3-mini"
    api_key_env: "OPENAI_API_KEY"
    timeout: 300
  gemini:
    enabled: true
    mode: cli
    cli_command: "gemini"
    model: "gemini-2.5-pro"
    api_key_env: "GOOGLE_API_KEY"
    timeout: 300

  debate:
    role_assignment: evidence          # evidence | rotate | fixed
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
  fail_on: high                        # minimum severity to fail CI
  min_confidence: 0.7
  require_debate: true

# Fast model — used for cheap intent-change detection and context-aware prompt generation
fast_model:
  provider: claude
  model: "claude-haiku-4-5-20251001"   # cheap, fast model for pre-scan checks
  api_key_env: "ANTHROPIC_API_KEY"     # falls back to CLI if key not set
  cli_command: "claude"
  timeout: 60

suppressions: []
```

See `.crossfire/config.example.yaml` for the full reference with all options.

---

## Agents

Each agent can run in two modes:

| Mode | How it works | When to use |
|------|-------------|-------------|
| `cli` | Spawns the agent's CLI tool as a subprocess | Claude Code, Codex CLI, Gemini CLI installed locally |
| `api` | Calls the provider's SDK directly | API keys available, no CLI tools installed |

### Supported agents

| Agent | CLI tool | API SDK | Default model |
|-------|----------|---------|---------------|
| Claude | `claude` (Claude Code) | `anthropic` | `claude-sonnet-4-20250514` |
| Codex | `codex` | `openai` | `o3-mini` |
| Gemini | `gemini` | `google-generativeai` | `gemini-2.5-pro` |

You can enable/disable any agent and mix CLI + API modes. A minimum of 2 agents is required for debate.

---

## Skills

Skills run before agent reviews and inject context signals into each agent's prompt. They do not produce findings directly — agents decide what matters.

| Skill | What it does |
|-------|-------------|
| **Data Flow Tracing** | Traces source → sink paths (HTTP params, env vars → exec, eval, subprocess) |
| **Git Archeology** | Git blame, file history, security-related commit search, code age |
| **Config Analysis** | CI workflow risks (`pull_request_target`), Dockerfile secrets, CORS permissiveness |
| **Dependency Analysis** | Diffs dependency manifests, flags added/changed/removed packages, known-risky packages |
| **Test Coverage Check** | Identifies changed files with no corresponding test files |
| **Code Navigation** | Import tracing, caller discovery, symbol definitions |

---

## Output Formats

```bash
# Default: markdown printed to stdout
crossfire analyze-pr --repo owner/repo --pr 123

# JSON (machine-readable, CI-friendly)
crossfire analyze-pr --repo owner/repo --pr 123 --format json

# SARIF (GitHub Code Scanning, IDE integration)
crossfire analyze-pr --repo owner/repo --pr 123 --format sarif --output report.sarif

# Write to file
crossfire analyze-pr --repo owner/repo --pr 123 --output report.md

# Post as GitHub PR comment automatically
crossfire analyze-pr --repo owner/repo --pr 123 --post-comment --github-token $GITHUB_TOKEN
```

---

## CI/CD Integration

### GitHub Actions — Stateless PR Review

```yaml
- name: CrossFire Security Review
  run: |
    pip install crossfire
    crossfire analyze-pr \
      --repo ${{ github.repository }} \
      --pr ${{ github.event.pull_request.number }} \
      --github-token ${{ secrets.GITHUB_TOKEN }} \
      --format sarif \
      --output crossfire.sarif \
      --post-comment

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: crossfire.sarif
```

### GitHub Actions — Baseline-Aware Scan (recommended for main branch)

```yaml
- name: CrossFire Baseline Scan
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: |
    pip install crossfire
    # Cache the baseline across runs
    # (cache .crossfire/baseline/ on your runner or in a workflow artifact)
    crossfire scan . \
      --since-last-scan \
      --format sarif \
      --output crossfire.sarif

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: crossfire.sarif
```

The severity gate (`fail_on: high`, `min_confidence: 0.7`) will exit with code 1 if any confirmed high-severity finding clears the debate. Set `fail_on: critical` to be less strict.

---

## Development

```bash
# Install with dev dependencies
make setup

# Run all tests
make test

# Run unit tests only
make test-unit

# Lint + type-check
make lint

# Auto-fix formatting
make format

# Run built-in demo
make demo
```

### Project structure

```
crossfire/
  cli.py                    # Typer CLI entry point (9 commands)
  config/
    defaults.py             # Default configuration values
    settings.py             # Config loader (CLI > env > YAML > defaults)
  core/
    models.py               # All Pydantic v2 models
    orchestrator.py         # Main pipeline (code_review, analyze_pr, analyze_diff, scan_with_baseline)
    context_builder.py      # Diff parsing + full-repo walking + file enrichment
    intent_inference.py     # What does this repo do?
    finding_synthesizer.py  # Cluster + dedupe + adjust findings
    policy_engine.py        # Suppression rules
    severity.py             # CI gate logic
    baseline.py             # BaselineManager — .crossfire/baseline/ read/write
    diff_resolver.py        # Resolves all scan input modes → DiffResult
  agents/
    base.py                 # CLI + API dual-mode base class
    claude_adapter.py
    codex_adapter.py
    gemini_adapter.py
    fast_model.py           # FastModel — API-first, CLI-fallback cheap inference
    review_engine.py        # Parallel independent reviews (+ system_prompt param)
    debate_engine.py        # 2-round judge-led debate
    consensus.py            # Evidence-based verdict logic
    prompts/
      review_prompt.py      # PR review prompt + CODE_REVIEW_SYSTEM_PROMPT (whole-repo)
      context_prompt.py     # Repo-specific prompt generation + intent-change check
      prosecutor_prompt.py
      defense_prompt.py
      judge_prompt.py
      guardrails.py         # Structural prompt injection protection
  skills/
    data_flow_tracing.py
    git_archeology.py
    config_analysis.py
    dependency_analysis.py
    test_coverage_check.py
    code_navigation.py
  output/
    markdown_report.py
    json_report.py
    sarif_report.py
  integrations/
    github/
      pr_loader.py          # GitHub API client
      comment_poster.py     # PR comment posting
tests/
  unit/                     # Fast, no network/LLM calls
  integration/              # End-to-end with fixtures
  fixtures/                 # Sample PRs for evaluation
docs/
  architecture.md           # Detailed component inventory + wiring diagram
.crossfire/
  baseline/                 # Auto-created by `crossfire baseline` or `crossfire scan`
    context.md              # Human-readable repo context
    intent.json             # Serialized IntentProfile
    scan_state.json         # Last scanned commit + timestamps
    known_findings.json     # Confirmed findings from previous scans
```

---

## Requirements

- Python 3.11+
- At least one agent configured (Claude, Codex, or Gemini)
- For `analyze-pr`: a GitHub token with `repo` read access
- For `cli` mode: the respective CLI tool installed and on `$PATH`
- For `api` mode: the API key set in the relevant environment variable

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.
