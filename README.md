<div align="center">

# CrossFire

### _Multiple agents. One verdict. Zero blind spots._

[![PyPI](https://img.shields.io/pypi/v/xfire)](https://pypi.org/project/xfire/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue)](https://github.com/Har1sh-k/xfire/blob/main/LICENSE)
[![Substack](https://img.shields.io/badge/substack-subscribe-orange?logo=substack)](https://harishkolla.substack.com/)

</div>

CrossFire is an AI-powered multi-agent security review tool. It runs three independent AI agents, forces them to debate every finding under adversarial cross-examination, and only surfaces what survives. False positives get eliminated before they reach you.

---

## Why CrossFire

- **No SAST, no rules engine** — agents read and reason, not pattern-match
- **Three pipelines** — whole-repo audit, GitHub PR diff review, or continuous baseline-aware delta scanning
- **Purpose-aware** — intent inference understands what the repo is supposed to do, so intended capabilities aren't flagged as bugs
- **Independent reviews** — agents never see each other's output; blind spots from one are caught by another
- **Adversarial debate** — every finding is stress-tested before it reaches you
- **Live terminal UI** — animated phase-by-phase status, per-agent spinners, debate chat viewer

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

# Play synthetic UI demo (no LLM calls — all 3 debate scenarios)
xfire demo --ui

# Run one specific UI demo scenario
xfire demo --ui --scenario both_accept
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
- name: xfire security review
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
- name: Restore xfire baseline
  uses: actions/cache@v4
  with:
    path: .xfire/baseline/
    key: xfire-baseline-${{ github.ref_name }}

- name: xfire baseline scan
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: |
    pip install xfire
    xfire scan . --since-last-scan --format sarif --output xfire.sarif

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: xfire.sarif

- name: Save xfire baseline
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

For architecture details, pipeline diagrams, component inventory, and data models see [`docs/architecture.md`](https://github.com/Har1sh-k/xfire/blob/main/docs/architecture.md).

---

## License

GNU General Public License v3.0 — see [LICENSE](https://github.com/Har1sh-k/xfire/blob/main/LICENSE) for details.

---

<div align="center">

Built with structured adversarial reasoning. No rules engines. No regex scanners.

</div>
