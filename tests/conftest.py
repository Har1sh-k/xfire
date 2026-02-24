"""Shared test fixtures for CrossFire tests."""

import pathlib

import pytest

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
