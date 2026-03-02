# Changelog

All notable changes to xFire will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - 2026-03-01

### Added

- New `docs/debate-engine.md` — deep reference for debate routing, silent dissent, budget tiers, consensus algorithm, evidence scoring, and configuration
- Architecture doc sections 9–13: intent inference signal reference, skills framework guide, error handling & fault tolerance, SARIF output format, policy engine & suppression rules
- Documented `xfire/agents/tools.py` (agent tool schemas) and `xfire/core/cache.py` (orphaned cache layer) in component inventory

### Changed

- Consistent xFire branding across all docs, README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
- README rewritten with ASCII pipeline diagram, feature table, documentation index
- Fixed finding category group count (7 → 8) and Pydantic model count (25+ → 23) in architecture doc
- Fixed FastModel model ID to full form `claude-haiku-4-5-20251001` in architecture doc

## [0.1.2] - 2026-02-28

### Added

- OpenClaw skill file (`.openclaw/skill.md`) for ClawHub marketplace distribution

### Changed

- Renamed all "CrossFire" references to "xfire" across docs, CLI help strings, report output, SARIF tool name, and test assertions
- Renamed GitHub Actions workflow file and job from `crossfire` to `xfire`
- Added missing env vars (`XFIRE_CACHE_DIR`, `XFIRE_AUTH_PATH`) to `.env.example`
- Fixed line length in CONTRIBUTING.md (100 → 120 to match pyproject.toml)
- GitHub comment poster now finds both old "CrossFire" and new "xfire" comments for update-in-place

## [0.1.1] - 2026-02-28

### Fixed

- Bumped version to 0.1.1
- Fixed Substack URL in pyproject.toml
- Removed broken CI badge from README

## [0.1.0] - 2026-02-28

Initial public release.

### Added

- **Multi-agent security review pipeline** — orchestrates Claude, Codex, and Gemini to independently review code for vulnerabilities
- **Adversarial debate engine** — 2-round prosecutor/defense/judge debate with live streaming to resolve disputed findings
- **Purpose-aware analysis** — intent inference engine that understands what the code is *supposed* to do, reducing false positives for intended capabilities
- **Finding synthesis** — union-find clustering, cross-validation confidence boosting, and deduplication across agents
- **6 built-in skills** — data flow tracing, git archeology, config analysis, dependency analysis, test coverage check, code navigation
- **3 review modes:**
  - `analyze-pr` — review a GitHub pull request
  - `analyze-diff` — review a local diff, staged changes, or patch file
  - `code-review` — full-repo security audit with no diff required
- **Baseline-aware scanning** — delta scanning that only reports new findings since the last baseline
- **3 output formats** — Markdown, JSON, and SARIF v2.1.0 with partialFingerprints, rank, and code snippets
- **GitHub integration** — automatic PR comment posting with update-in-place
- **Live terminal UI** — animated phase spinners, debate transcript viewer, and severity badges
- **CLI credential readers** — reads Claude, Codex, and Gemini credentials from their native CLI config files
- **Demo mode** — `xfire demo --ui` runs against 7 synthetic fixture scenarios without needing API keys
- **Policy engine** — suppression rules by category, file pattern, or title pattern
- **Severity gate** — `--fail-on` flag for CI integration that exits non-zero when severity threshold is breached
- **Context caching** — persists PR context and intent profiles to `.xfire/cache/` for faster re-runs
- **Prompt injection guardrails** — structural defenses in all agent prompts
- **Comprehensive test suite** — 380+ unit and integration tests
- **Full documentation** — architecture, threat model, finding schema, prompting strategy, review methodology, and evaluation plan

### Security

- All credentials read from environment variables or native CLI config files at runtime — nothing hardcoded
- Subprocess execution uses `asyncio.create_subprocess_exec` without `shell=True`
- Auth tokens isolated in `.xfire/auth.json` (gitignored)
- Structural prompt injection guards in `xfire/agents/prompts/guardrails.py`

[0.1.3]: https://github.com/Har1sh-k/xfire/releases/tag/v0.1.3
[0.1.2]: https://github.com/Har1sh-k/xfire/releases/tag/v0.1.2
[0.1.1]: https://github.com/Har1sh-k/xfire/releases/tag/v0.1.1
[0.1.0]: https://github.com/Har1sh-k/xfire/releases/tag/v0.1.0
