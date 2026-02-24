"""Configuration loading for CrossFire.

Priority: CLI flags > environment variables > .crossfire/config.yaml > defaults.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from crossfire.config.defaults import DEFAULT_CONFIG


class AgentConfig(BaseModel):
    """Configuration for a single agent."""

    enabled: bool = True
    mode: str = "cli"  # "cli" | "api"
    cli_command: str = ""
    cli_args: list[str] = Field(default_factory=list)
    model: str = ""
    api_key_env: str = ""
    timeout: int = 300


class DebateConfig(BaseModel):
    """Configuration for the debate system."""

    role_assignment: str = "evidence"  # "evidence" | "rotate" | "fixed"
    fixed_roles: dict[str, str] = Field(
        default_factory=lambda: {
            "prosecutor": "claude",
            "defense": "codex",
            "judge": "gemini",
        }
    )
    defense_preference: list[str] = Field(
        default_factory=lambda: ["codex", "claude", "gemini"],
    )
    judge_preference: list[str] = Field(
        default_factory=lambda: ["codex", "gemini", "claude"],
    )
    max_rounds: int = 2  # min 1 (defense concedes), max 2 (judge-led round 2)
    require_evidence_citations: bool = True
    min_agents_for_debate: int = 2


class SkillsConfig(BaseModel):
    """Configuration for which skills are enabled."""

    code_navigation: bool = True
    data_flow_tracing: bool = True
    git_archeology: bool = True
    config_analysis: bool = True
    dependency_analysis: bool = True
    test_coverage_check: bool = True


class RepoConfig(BaseModel):
    """Repository-specific configuration."""

    purpose: str = ""
    intended_capabilities: list[str] = Field(default_factory=list)
    sensitive_paths: list[str] = Field(
        default_factory=lambda: ["auth/", "payments/", "migrations/"]
    )


class AnalysisConfig(BaseModel):
    """Analysis behavior configuration."""

    context_depth: str = "deep"  # "shallow" | "medium" | "deep"
    max_related_files: int = 20
    include_test_files: bool = True


class SeverityGateConfig(BaseModel):
    """CI gating configuration."""

    fail_on: str = "high"  # minimum severity to fail CI
    min_confidence: float = 0.7
    require_debate: bool = True


class CrossFireSettings(BaseModel):
    """Root configuration model for CrossFire."""

    repo: RepoConfig = Field(default_factory=RepoConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    debate: DebateConfig = Field(default_factory=DebateConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    severity_gate: SeverityGateConfig = Field(default_factory=SeverityGateConfig)
    suppressions: list[dict[str, Any]] = Field(default_factory=list)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _find_config_file(repo_dir: str | None = None) -> Path | None:
    """Find .crossfire/config.yaml in the repo or env override."""
    env_path = os.environ.get("CROSSFIRE_CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    search_dir = Path(repo_dir) if repo_dir else Path.cwd()
    config_path = search_dir / ".crossfire" / "config.yaml"
    if config_path.exists():
        return config_path

    return None


class ConfigError(Exception):
    """Error loading or validating CrossFire configuration."""


def _load_yaml_config(path: Path) -> dict:
    """Load and parse a YAML config file."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e
    return data if isinstance(data, dict) else {}


def _parse_agents_config(raw: dict) -> tuple[dict[str, AgentConfig], DebateConfig, SkillsConfig]:
    """Parse the agents section into typed configs."""
    agents_raw = raw.get("agents", {})

    debate_raw = agents_raw.pop("debate", {})
    skills_raw = agents_raw.pop("skills", {})

    agents: dict[str, AgentConfig] = {}
    for name, cfg in agents_raw.items():
        if isinstance(cfg, dict):
            agents[name] = AgentConfig(**cfg)

    debate = DebateConfig(**debate_raw) if debate_raw else DebateConfig()
    skills = SkillsConfig(**skills_raw) if skills_raw else SkillsConfig()

    return agents, debate, skills


def load_settings(
    repo_dir: str | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> CrossFireSettings:
    """Load settings with priority: CLI > env > config.yaml > defaults.

    Args:
        repo_dir: Path to the repository root (for finding config file).
        cli_overrides: Overrides from CLI flags.

    Returns:
        Fully resolved CrossFireSettings.
    """
    # Start with defaults (deep copy to avoid mutating the shared DEFAULT_CONFIG)
    merged = copy.deepcopy(DEFAULT_CONFIG)

    # Layer config file
    config_path = _find_config_file(repo_dir)
    if config_path:
        file_config = _load_yaml_config(config_path)
        merged = _deep_merge(merged, file_config)

    # Layer CLI overrides
    if cli_overrides:
        merged = _deep_merge(merged, cli_overrides)

    # Parse into typed config
    try:
        agents, debate, skills = _parse_agents_config(merged)

        return CrossFireSettings(
            repo=RepoConfig(**merged.get("repo", {})),
            analysis=AnalysisConfig(**merged.get("analysis", {})),
            agents=agents,
            debate=debate,
            skills=skills,
            severity_gate=SeverityGateConfig(**merged.get("severity_gate", {})),
            suppressions=merged.get("suppressions", []),
        )
    except Exception as e:
        raise ConfigError(f"Invalid configuration: {e}") from e
