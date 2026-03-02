<div align="center">

# xFire

### _Multiple agents. One verdict. Zero blind spots._

[![PyPI](https://img.shields.io/pypi/v/xfire)](https://pypi.org/project/xfire/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue)](https://github.com/Har1sh-k/xfire/blob/main/LICENSE)
[![Substack](https://img.shields.io/badge/substack-subscribe-orange?logo=substack)](https://harishkolla.substack.com/)

</div>

xFire is an AI-powered multi-agent security review tool. Three independent AI agents — Claude, Codex, and Gemini — each review your code blind, then argue about it under structured adversarial cross-examination. Only vulnerabilities that survive the debate make the final report.

---

## How It Works

```
                    +-----------+     +-----------+     +-----------+
                    |  Claude   |     |  Codex    |     |  Gemini   |
                    +-----+-----+     +-----+-----+     +-----+-----+
                          |                 |                 |
  PR / Repo               |    blind review (parallel)       |
      |                   +--------+--------+--------+--------+
      v                            |
+-------------+   +-----------+    v           +-------------+   +-----------+
|  Context    |-->|  Intent   |-->[ Findings ]-| Adversarial |-->|  Verdict  |
|  Building   |   | Inference |   [ Synthesis] |   Debate    |   |  & Report |
+-------------+   +-----------+                +-------------+   +-----------+
```

**Stage by stage:** Context building gathers the diff, dependencies, and repo structure. Intent inference figures out what the code is _supposed_ to do. Three agents review independently — no agent sees another's output. The synthesis layer clusters and cross-validates findings. Disputed findings enter an adversarial debate: prosecutor, defense, judge. The consensus algorithm weighs evidence quality, unanimity, and purpose-aware overrides to produce a final verdict.

> For the full architectural deep dive, see [docs/architecture.md](https://github.com/Har1sh-k/xfire/blob/main/docs/architecture.md).

---

## Why xFire

| | |
|---|---|
| **No SAST, no rules engine** | Agents read and reason about code, not pattern-match |
| **Purpose-aware** | Intent inference understands what the repo is supposed to do — intended capabilities with proper controls are never flagged |
| **Three independent reviewers** | Claude, Codex, and Gemini review in isolation; blind spots from one are caught by another |
| **Adversarial debate** | Every disputed finding goes through prosecutor → defense → judge cross-examination |
| **Three pipelines** | Whole-repo audit, GitHub PR diff review, or continuous baseline-aware delta scanning |
| **Live terminal UI** | Animated phase spinners, per-agent status, live debate chat streaming |

---

## Installation

Requires Python 3.11+.

```bash
pip install xfire
```

Or from source:

```bash
git clone https://github.com/Har1sh-k/xfire
cd xfire
pip install -e ".[dev]"
```

You need at least one agent CLI or API key:

| Agent | CLI | API key env |
|-------|-----|-------------|
| Claude | [claude.ai/code](https://claude.ai/code) | `ANTHROPIC_API_KEY` |
| Codex | [github.com/openai/codex](https://github.com/openai/codex) | `OPENAI_API_KEY` |
| Gemini | [ai.google.dev](https://ai.google.dev/gemini-api/docs/gemini-cli) | `GOOGLE_API_KEY` |

---

## Quick Start

```bash
# Initialize config
xfire init

# Verify agents are reachable
xfire test-llm

# Audit the whole repo
xfire code-review .

# Review a GitHub PR
xfire analyze-pr --repo owner/repo --pr 123 --github-token $GITHUB_TOKEN

# Baseline-aware delta scan
xfire scan . --since-last-scan

# Stream live debate chat as each agent responds
xfire code-review . --debate

# Full debug trace + markdown log
xfire code-review . --debug

# Play synthetic UI demo (no LLM calls)
xfire demo --ui
```

---

## Configuration

Run `xfire init` to generate `.xfire/config.yaml`. The key settings:

```yaml
agents:
  claude:
    enabled: true
    mode: cli          # cli | api
  codex:
    enabled: true
    mode: cli
  gemini:
    enabled: true
    mode: cli

severity_gate:
  fail_on: high        # minimum severity to fail CI
  min_confidence: 0.7
```

Full config reference: [`docs/architecture.md`](https://github.com/Har1sh-k/xfire/blob/main/docs/architecture.md)

---

## CI/CD Integration

### Stateless PR Review

```yaml
- name: xFire security review
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: |
    pip install xfire
    xfire analyze-pr \
      --repo ${{ github.repository }} \
      --pr ${{ github.event.pull_request.number }} \
      --github-token ${{ secrets.GITHUB_TOKEN }} \
      --format sarif --output xfire.sarif --post-comment

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: xfire.sarif
```

### Baseline-Aware Scan (recommended for main)

```yaml
- name: Restore xFire baseline
  uses: actions/cache@v4
  with:
    path: .xfire/baseline/
    key: xfire-baseline-${{ github.ref_name }}

- name: xFire baseline scan
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: |
    pip install xfire
    xfire scan . --since-last-scan --format sarif --output xfire.sarif

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: xfire.sarif

- name: Save xFire baseline
  uses: actions/cache/save@v4
  with:
    path: .xfire/baseline/
    key: xfire-baseline-${{ github.ref_name }}
```

---

## Development

```bash
make setup      # install with dev dependencies
make test       # run all tests
make test-unit  # unit tests only
make lint       # lint + type-check
make format     # auto-fix formatting
make demo       # run synthetic UI demo (no LLM calls)
```

---

## Documentation

| Doc | What it covers |
|-----|----------------|
| [Architecture](https://github.com/Har1sh-k/xfire/blob/main/docs/architecture.md) | Full pipeline diagrams, component inventory, call graphs, data models, config flow |
| [Debate Engine](https://github.com/Har1sh-k/xfire/blob/main/docs/debate-engine.md) | Role assignment, debate flow, silent dissent, budget tiers, consensus algorithm, evidence scoring |
| [Review Methodology](https://github.com/Har1sh-k/xfire/blob/main/docs/review-methodology.md) | How agents review code, purpose-aware decision framework |
| [Prompting Strategy](https://github.com/Har1sh-k/xfire/blob/main/docs/prompting-strategy.md) | Prompt design philosophy, debate prompt structure |
| [Finding Schema](https://github.com/Har1sh-k/xfire/blob/main/docs/finding-schema.md) | Finding model, 50 categories, evidence requirements, debate routing |
| [Threat Model](https://github.com/Har1sh-k/xfire/blob/main/docs/threat-model.md) | What xFire detects, prompt injection guardrails, trust model |
| [Evaluation Plan](https://github.com/Har1sh-k/xfire/blob/main/docs/eval-plan.md) | Test fixtures, precision/recall metrics |

---

## License

GNU General Public License v3.0 — see [LICENSE](https://github.com/Har1sh-k/xfire/blob/main/LICENSE) for details.

---

<div align="center">

_Built with structured adversarial reasoning. No rules engines. No regex scanners._

[Blog](https://harishkolla.substack.com/) · [Docs](https://github.com/Har1sh-k/xfire/tree/main/docs) · [PyPI](https://pypi.org/project/xfire/)

</div>
