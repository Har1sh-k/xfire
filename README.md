# CrossFire

> _"Multiple agents. One verdict. Zero blind spots."_

CrossFire is an AI-powered PR security review system that reviews code the way senior security engineers do — by actually *reading and reasoning about it*, not by running regex scanners.

## How It Works

1. **Context Building** — Extracts deep context from PRs: diffs, full files, related files, git history, configs
2. **Intent Inference** — Understands what the repo does and what capabilities are intended
3. **Independent Agent Reviews** — Multiple AI agents independently review the code like security engineers
4. **Adversarial Debate** — Agents debate findings (prosecutor vs defense vs judge) to eliminate false positives
5. **Consensus Verdict** — Evidence-based consensus determines which findings are real

## Key Differentiators

- **No SAST / No regex** — Agents READ and REASON about code, not pattern match
- **Purpose-aware** — Understands intended capabilities vs actual vulnerabilities
- **Multi-agent cross-validation** — Independent reviews catch what one agent misses
- **Adversarial debate** — Eliminates false positives through evidence-based argumentation
- **Deep context** — Agents see full files, related files, git history, and configs

## Quick Start

```bash
pip install crossfire

# Initialize config in your repo
crossfire init

# Analyze a GitHub PR
crossfire analyze-pr --repo owner/repo --pr 123 --github-token $GITHUB_TOKEN

# Analyze a local diff
crossfire analyze-diff --patch changes.patch --repo-dir /path/to/repo

# Analyze staged changes
crossfire analyze-diff --staged --repo-dir .
```

## Configuration

Create `.crossfire/config.yaml` in your repo (or run `crossfire init`):

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
  fail_on: high
  min_confidence: 0.7
  require_debate: true
```

See `.crossfire/config.example.yaml` for full configuration options.

## Development

```bash
# Setup
make setup

# Run tests
make test

# Lint
make lint

# Run demo
make demo
```

## License

MIT
