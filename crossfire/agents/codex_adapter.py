"""Codex agent adapter - OpenAI API with optional subscription OAuth fallback."""

from __future__ import annotations

import os

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.auth import get_codex_api_key

logger = structlog.get_logger()


class CodexAgent(BaseAgent):
    """Agent adapter for Codex (OpenAI API with auth-store fallback)."""

    name = "codex"

    async def _run_cli(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Codex CLI.

        Command: codex -q "{prompt}"
        """
        cmd = [self.config.cli_command]

        # Use quiet mode with full context prompt
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        cmd.extend(["-q", full_prompt])

        # Add any extra CLI args from config
        cmd.extend(self.config.cli_args)

        return await self._run_subprocess(cmd)

    async def _run_api(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via OpenAI API (async)."""
        try:
            import openai
        except ImportError:
            raise AgentError(self.name, "openai package not installed")

        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            api_key = get_codex_api_key(refresh_if_needed=True)

        if not api_key:
            raise AgentError(
                self.name,
                (
                    f"API key not found in env var {self.config.api_key_env}. "
                    "Run `crossfire auth login --provider codex` for subscription auth."
                ),
            )

        client = openai.AsyncOpenAI(api_key=api_key, timeout=self.config.timeout)

        logger.info("agent.api.start", agent=self.name, model=self.config.model)

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = await client.chat.completions.create(
                model=self.config.model,
                messages=messages,
            )
            content = response.choices[0].message.content
            if content is None:
                raise AgentError(self.name, "API returned empty response")
            return content
        except Exception as e:
            if isinstance(e, AgentError):
                raise
            raise AgentError(self.name, f"API call failed: {e}")
