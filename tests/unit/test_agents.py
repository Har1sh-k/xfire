"""Tests for agent base class and adapters."""

import asyncio
import json

import pytest

from xfire.agents.base import AgentError, BaseAgent
from xfire.config.settings import AgentConfig


class DummyAgent(BaseAgent):
    """Concrete agent for testing BaseAgent functionality."""

    name = "dummy"

    async def _run_cli(self, prompt, system_prompt, context_files):
        return '{"result": "cli"}'

    async def _run_api(self, prompt, system_prompt, context_files):
        return '{"result": "api"}'


class TestBaseAgentExecute:
    def test_cli_mode(self):
        config = AgentConfig(mode="cli")
        agent = DummyAgent(config)
        result = asyncio.get_event_loop().run_until_complete(
            agent.execute("hello", "system")
        )
        assert result == '{"result": "cli"}'

    def test_api_mode(self):
        config = AgentConfig(mode="api")
        agent = DummyAgent(config)
        result = asyncio.get_event_loop().run_until_complete(
            agent.execute("hello", "system")
        )
        assert result == '{"result": "api"}'


class TestParseJsonResponse:
    def _agent(self):
        return DummyAgent(AgentConfig())

    def test_plain_json(self):
        agent = self._agent()
        result = agent.parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_code_block(self):
        agent = self._agent()
        raw = '```json\n{"key": "value"}\n```'
        result = agent.parse_json_response(raw)
        assert result == {"key": "value"}

    def test_json_with_preamble(self):
        agent = self._agent()
        raw = 'Here is the analysis:\n\n{"findings": []}'
        result = agent.parse_json_response(raw)
        assert result == {"findings": []}

    def test_json_with_surrounding_text(self):
        agent = self._agent()
        raw = 'Analysis:\n{"severity": "High"}\nDone.'
        result = agent.parse_json_response(raw)
        assert result == {"severity": "High"}

    def test_invalid_json_raises(self):
        agent = self._agent()
        with pytest.raises(AgentError, match="Could not parse JSON"):
            agent.parse_json_response("no json here at all")

    def test_nested_json(self):
        agent = self._agent()
        data = {"findings": [{"title": "test", "severity": "High"}]}
        result = agent.parse_json_response(json.dumps(data))
        assert result == data


class TestAgentError:
    def test_error_message_format(self):
        err = AgentError("claude", "API timeout")
        assert "claude" in str(err)
        assert "API timeout" in str(err)
        assert err.agent_name == "claude"
