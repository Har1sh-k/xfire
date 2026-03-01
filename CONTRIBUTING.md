# Contributing to CrossFire

Thank you for your interest in contributing. CrossFire is an adversarial multi-agent security review tool — contributions that improve accuracy, reduce false positives, or extend pipeline coverage are especially welcome.

---

## Getting started

```bash
git clone https://github.com/Har1sh-k/xfire
cd xfire
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Verify your setup:

```bash
make test      # all 383+ unit tests should pass
make lint      # ruff + mypy
```

---

## What to work on

Check [open issues](https://github.com/Har1sh-k/xfire/issues) for ideas. Good first contributions:

- New skills (pre-compute context signals for agents)
- Additional test fixtures in `tests/fixtures/prs/`
- Output format improvements (SARIF, markdown)
- Documentation improvements

---

## Development workflow

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Add or update tests — all new code should have unit tests
4. Run `make test` and `make lint` — both must pass
5. Open a pull request against `main`

### Running tests

```bash
make test           # all tests
make test-unit      # unit tests only (fast, no network)
```

Tests in `tests/unit/` must not make real network or LLM calls. Use fixtures in `tests/fixtures/` and mock adapters.

### Linting

```bash
make lint           # ruff + mypy (strict)
make format         # auto-fix formatting with ruff
```

---

## Adding a new skill

Skills live in `xfire/skills/`. Each skill:

1. Inherits from `BaseSkill` (`xfire/skills/base.py`)
2. Implements `execute(repo_dir, changed_files) -> SkillResult`
3. Returns a `SkillResult` with a markdown `content` string injected into agent prompts
4. Is registered in `xfire/core/orchestrator.py:_run_skills()`
5. Has a toggle in `xfire/config/defaults.py` under `agents.skills`

---

## Adding a new agent

Agent adapters live in `xfire/agents/`. Each adapter:

1. Inherits from `BaseAgent` (`xfire/agents/base.py`)
2. Implements `_call_cli()` and `_call_api()` (or just one if the other isn't applicable)
3. Is registered in `xfire/agents/review_engine.py` and `xfire/agents/debate_engine.py`
4. Has a config section in `xfire/config/defaults.py`

---

## Pull request checklist

- [ ] Tests pass (`make test`)
- [ ] Lint passes (`make lint`)
- [ ] New functionality has unit tests
- [ ] Changes to prompts include evaluation against test fixtures
- [ ] PR description explains the change and its motivation

---

## Code style

- Python 3.11+
- Line length: 120 (ruff)
- Type annotations on all public functions
- `structlog` for logging — never `print()`
- Pydantic v2 for all data models

---

## Questions

Open a [GitHub Discussion](https://github.com/Har1sh-k/xfire/discussions) or file an issue.
