"""Tests for CLI helper functions."""

import pytest
import typer

from crossfire.cli import _parse_agents_list


class TestParseAgentsList:
    def test_none_input(self):
        assert _parse_agents_list(None) is None

    def test_empty_string(self):
        assert _parse_agents_list("") is None

    def test_single_agent(self):
        assert _parse_agents_list("claude") == ["claude"]

    def test_multiple_agents(self):
        result = _parse_agents_list("claude,codex,gemini")
        assert result == ["claude", "codex", "gemini"]

    def test_strips_whitespace(self):
        result = _parse_agents_list("claude , codex , gemini")
        assert result == ["claude", "codex", "gemini"]

    def test_skips_empty_entries(self):
        result = _parse_agents_list("claude,,codex,")
        assert result == ["claude", "codex"]
