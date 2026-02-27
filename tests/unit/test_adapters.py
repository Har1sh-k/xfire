"""Tests for agent adapters (Claude, Codex, Gemini) — mock subprocess and API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crossfire.agents.base import AgentError
from crossfire.agents.claude_adapter import ClaudeAgent
from crossfire.agents.codex_adapter import CodexAgent
from crossfire.agents.gemini_adapter import GeminiAgent
from crossfire.config.settings import AgentConfig


class TestClaudeAdapter:
    @pytest.mark.asyncio
    async def test_cli_mode(self):
        config = AgentConfig(mode="cli", cli_command="claude", cli_args=["--output-format", "json"])
        agent = ClaudeAgent(config)
        with patch.object(agent, "_run_subprocess", new_callable=AsyncMock, return_value='{"findings":[]}') as mock:
            result = await agent.execute("prompt", "system")
        assert result == '{"findings":[]}'
        mock.assert_called_once()
        cmd = mock.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd

    @pytest.mark.asyncio
    async def test_api_mode(self):
        config = AgentConfig(mode="api", api_key_env="ANTHROPIC_API_KEY", model="claude-sonnet-4-20250514")
        agent = ClaudeAgent(config)
        with patch.object(agent, "_run_api", new_callable=AsyncMock, return_value='{"findings":[]}'):
            result = await agent.execute("prompt", "system")
        assert result == '{"findings":[]}'


class TestCodexAdapter:
    @pytest.mark.asyncio
    async def test_cli_mode(self):
        config = AgentConfig(mode="cli", cli_command="codex")
        agent = CodexAgent(config)
        with patch.object(agent, "_run_subprocess", new_callable=AsyncMock, return_value='{"findings":[]}') as mock:
            result = await agent.execute("prompt", "system")
        assert result == '{"findings":[]}'
        cmd = mock.call_args[0][0]
        assert cmd[0] == "codex"
        assert "exec" in cmd

    @pytest.mark.asyncio
    async def test_api_mode(self):
        config = AgentConfig(mode="api", api_key_env="OPENAI_API_KEY", model="o3-mini")
        agent = CodexAgent(config)
        with patch.object(agent, "_run_api", new_callable=AsyncMock, return_value='{"findings":[]}'):
            result = await agent.execute("prompt", "system")
        assert result == '{"findings":[]}'


class TestGeminiAdapter:
    @pytest.mark.asyncio
    async def test_cli_mode(self):
        config = AgentConfig(mode="cli", cli_command="gemini")
        agent = GeminiAgent(config)
        with patch.object(agent, "_run_subprocess", new_callable=AsyncMock, return_value='{"findings":[]}') as mock:
            result = await agent.execute("prompt", "system")
        assert result == '{"findings":[]}'
        cmd = mock.call_args[0][0]
        assert cmd[0] == "gemini"

    @pytest.mark.asyncio
    async def test_api_mode(self):
        config = AgentConfig(mode="api", api_key_env="GOOGLE_API_KEY", model="gemini-2.5-pro")
        agent = GeminiAgent(config)
        with patch.object(agent, "_run_api", new_callable=AsyncMock, return_value='{"findings":[]}'):
            result = await agent.execute("prompt", "system")
        assert result == '{"findings":[]}'
