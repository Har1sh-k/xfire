"""Shared test fixtures and factories for CrossFire tests."""

import pathlib

import pytest

from crossfire.config.settings import AgentConfig, CrossFireSettings, DebateConfig
from crossfire.core.models import (
    AgentReview,
    Finding,
    FindingCategory,
    FindingStatus,
    IntentProfile,
    PRContext,
    Severity,
)

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "prs"


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    return FIXTURES_DIR


@pytest.fixture
def auth_bypass_fixture() -> pathlib.Path:
    return FIXTURES_DIR / "auth_bypass_regression"


@pytest.fixture
def command_injection_fixture() -> pathlib.Path:
    return FIXTURES_DIR / "command_injection_exposure"


@pytest.fixture
def intended_exec_fixture() -> pathlib.Path:
    return FIXTURES_DIR / "intended_exec_with_sandbox"


@pytest.fixture
def secret_logging_fixture() -> pathlib.Path:
    return FIXTURES_DIR / "secret_logging"


@pytest.fixture
def destructive_migration_fixture() -> pathlib.Path:
    return FIXTURES_DIR / "destructive_migration"


@pytest.fixture
def race_condition_fixture() -> pathlib.Path:
    return FIXTURES_DIR / "race_condition_data_corruption"


@pytest.fixture
def safe_refactor_fixture() -> pathlib.Path:
    return FIXTURES_DIR / "safe_refactor_no_issues"


# ---------------------------------------------------------------------------
# Shared factory functions (plain functions, not fixtures)
# ---------------------------------------------------------------------------


def make_finding(**kwargs) -> Finding:
    """Create a Finding with sensible defaults. Override any field via kwargs."""
    defaults = {
        "title": "Test Finding",
        "category": FindingCategory.COMMAND_INJECTION,
        "severity": Severity.HIGH,
        "confidence": 0.8,
        "affected_files": ["app.py"],
        "reviewing_agents": ["claude"],
    }
    defaults.update(kwargs)
    return Finding(**defaults)


def make_review(agent: str, findings: list[Finding] | None = None, **kwargs) -> AgentReview:
    """Create an AgentReview with defaults."""
    return AgentReview(agent_name=agent, findings=findings or [], **kwargs)


def make_settings(
    agents: dict | None = None,
    role_assignment: str = "evidence",
    **debate_kwargs,
) -> CrossFireSettings:
    """Create CrossFireSettings with sensible defaults."""
    if agents is None:
        agents = {
            "claude": AgentConfig(enabled=True, cli_command="claude"),
            "codex": AgentConfig(enabled=True, cli_command="codex"),
            "gemini": AgentConfig(enabled=True, cli_command="gemini"),
        }
    return CrossFireSettings(
        agents=agents,
        debate=DebateConfig(role_assignment=role_assignment, **debate_kwargs),
    )


def make_context(**kwargs) -> PRContext:
    """Create a PRContext with sensible defaults."""
    defaults = {
        "repo_name": "test/repo",
        "pr_title": "Test PR",
    }
    defaults.update(kwargs)
    return PRContext(**defaults)


def make_intent(**kwargs) -> IntentProfile:
    """Create an IntentProfile with sensible defaults."""
    return IntentProfile(**kwargs)
