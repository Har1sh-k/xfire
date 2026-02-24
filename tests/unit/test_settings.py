"""Tests for configuration loading (settings.py)."""

import os
from pathlib import Path

import pytest

from crossfire.config.settings import (
    AgentConfig,
    ConfigError,
    CrossFireSettings,
    _deep_merge,
    _find_config_file,
    _load_yaml_config,
    _parse_agents_config,
    load_settings,
)


class TestDeepMerge:
    def test_flat_override(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert _deep_merge(base, override) == {"a": 1, "b": 3}

    def test_nested_merge(self):
        base = {"repo": {"purpose": "", "paths": ["a/"]}}
        override = {"repo": {"purpose": "web app"}}
        result = _deep_merge(base, override)
        assert result["repo"]["purpose"] == "web app"
        assert result["repo"]["paths"] == ["a/"]

    def test_add_new_key(self):
        base = {"a": 1}
        override = {"b": 2}
        assert _deep_merge(base, override) == {"a": 1, "b": 2}

    def test_empty_override(self):
        base = {"a": 1}
        assert _deep_merge(base, {}) == {"a": 1}

    def test_does_not_mutate_base(self):
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        _deep_merge(base, override)
        assert "y" not in base["a"]


class TestFindConfigFile:
    def test_returns_none_when_no_config(self, tmp_path):
        assert _find_config_file(str(tmp_path)) is None

    def test_finds_config_in_repo(self, tmp_path):
        cfg_dir = tmp_path / ".crossfire"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.yaml"
        cfg_file.write_text("repo:\n  purpose: test\n")
        result = _find_config_file(str(tmp_path))
        assert result == cfg_file

    def test_env_override(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "custom.yaml"
        cfg_file.write_text("repo:\n  purpose: env\n")
        monkeypatch.setenv("CROSSFIRE_CONFIG_PATH", str(cfg_file))
        result = _find_config_file(str(tmp_path))
        assert result == cfg_file


class TestLoadYamlConfig:
    def test_valid_yaml(self, tmp_path):
        f = tmp_path / "config.yaml"
        f.write_text("repo:\n  purpose: hello\n")
        result = _load_yaml_config(f)
        assert result == {"repo": {"purpose": "hello"}}

    def test_empty_yaml_returns_dict(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        result = _load_yaml_config(f)
        assert result == {}

    def test_invalid_yaml_raises(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text(":\n  bad: [unclosed")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            _load_yaml_config(f)


class TestParseAgentsConfig:
    def test_parses_agents(self):
        raw = {
            "agents": {
                "claude": {"enabled": True, "mode": "cli", "cli_command": "claude"},
            },
        }
        agents, debate, skills = _parse_agents_config(raw)
        assert "claude" in agents
        assert agents["claude"].enabled is True

    def test_extracts_debate_config(self):
        raw = {
            "agents": {
                "debate": {"max_rounds": 3},
                "claude": {"enabled": True},
            },
        }
        agents, debate, skills = _parse_agents_config(raw)
        assert debate.max_rounds == 3
        assert "debate" not in agents

    def test_default_when_empty(self):
        raw = {"agents": {}}
        agents, debate, skills = _parse_agents_config(raw)
        assert agents == {}
        assert debate.max_rounds == 2  # default


class TestLoadSettings:
    def test_loads_defaults(self):
        settings = load_settings()
        assert isinstance(settings, CrossFireSettings)
        assert "claude" in settings.agents

    def test_cli_overrides_applied(self):
        settings = load_settings(
            cli_overrides={"analysis": {"context_depth": "shallow"}},
        )
        assert settings.analysis.context_depth == "shallow"

    def test_config_file_merged(self, tmp_path):
        cfg_dir = tmp_path / ".crossfire"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.yaml"
        cfg_file.write_text("repo:\n  purpose: test project\n")
        settings = load_settings(repo_dir=str(tmp_path))
        assert settings.repo.purpose == "test project"
