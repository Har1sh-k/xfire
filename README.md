<div align="center">

# CrossFire

### _Multiple agents. One verdict. Zero blind spots._

![PyPI - coming soon](https://img.shields.io/badge/pypi-coming%20soon-lightgrey)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/Har1sh-k/CrossFire/ci.yml?branch=main&label=CI)](https://github.com/Har1sh-k/CrossFire/actions)
[![Substack](https://img.shields.io/badge/substack-subscribe-orange?logo=substack)](https://substack.com/@har1shk)

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
pip install crossfire
```

Or from source:

```bash
git clone https://github.com/Har1sh-k/CrossFire
cd CrossFire
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
crossfire init

# Verify agents are reachable
crossfire test-llm

# Audit the whole repo
crossfire code-review .

# Review a GitHub PR
crossfire analyze-pr --repo owner/repo --pr 123 --github-token $GITHUB_TOKEN

# Baseline-aware delta scan
crossfire scan . --since-last-scan

# View the adversarial debate after analysis
crossfire code-review . --debate

# Full debug trace + markdown log
crossfire code-review . --debug
```

---

## Configuration

Run `crossfire init` to generate `.crossfire/config.yaml`. The key settings:

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

Full config reference: [`docs/architecture.md`](docs/architecture.md)

---

## CI/CD Integration

### Stateless PR Review

```yaml
- name: CrossFire Security Review
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: |
    pip install crossfire
    crossfire analyze-pr \
      --repo ${{ github.repository }} \
      --pr ${{ github.event.pull_request.number }} \
      --github-token ${{ secrets.GITHUB_TOKEN }} \
      --format sarif --output crossfire.sarif --post-comment

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: crossfire.sarif
```

### Baseline-Aware Scan (recommended for main)

```yaml
- name: Restore CrossFire baseline
  uses: actions/cache@v4
  with:
    path: .crossfire/baseline/
    key: crossfire-baseline-${{ github.ref_name }}

- name: CrossFire Baseline Scan
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: |
    pip install crossfire
    crossfire scan . --since-last-scan --format sarif --output crossfire.sarif

- name: Upload SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: crossfire.sarif

- name: Save CrossFire baseline
  uses: actions/cache/save@v4
  with:
    path: .crossfire/baseline/
    key: crossfire-baseline-${{ github.ref_name }}
```

---

## Development

```bash
make setup      # install with dev dependencies
make test       # run all tests
make test-unit  # unit tests only
make lint       # lint + type-check
make format     # auto-fix formatting
make demo       # run built-in demo
```

For architecture details, pipeline diagrams, component inventory, and data models see [`docs/architecture.md`](docs/architecture.md).

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.

---

<div align="center">

Built with structured adversarial reasoning. No rules engines. No regex scanners.

</div>
