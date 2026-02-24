"""Claude agent adapter — Claude Code CLI + Anthropic API."""

from __future__ import annotations

import os
import tempfile

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.config.settings import AgentConfig

logger = structlog.get_logger()


class ClaudeAgent(BaseAgent):
    """Agent adapter for Claude (Claude Code CLI or Anthropic API)."""

    name = "claude"

    async def _run_cli(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Claude Code CLI.

        Command: claude -p "{prompt}" --system-prompt "{system_prompt}" --output-format json
        """
        cmd = [self.config.cli_command]

        # Write prompt to temp file to avoid shell escaping issues
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(prompt)
            prompt_file = f.name

        try:
            cmd.extend(["-p", prompt, "--output-format", "json"])

            if system_prompt:
                cmd.extend(["--system-prompt", system_prompt])

            # Add any extra CLI args from config
            cmd.extend(self.config.cli_args)

            return await self._run_subprocess(cmd)
        finally:
            try:
                os.unlink(prompt_file)
            except OSError:
                pass

    async def _run_api(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Anthropic API."""
        try:
            import anthropic
        except ImportError:
            raise AgentError(self.name, "anthropic package not installed")

        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise AgentError(self.name, f"API key not found in env var {self.config.api_key_env}")

        client = anthropic.Anthropic(api_key=api_key)

        logger.info("agent.api.start", agent=self.name, model=self.config.model)

        try:
            response = client.messages.create(
                model=self.config.model,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=8192,
            )
            return response.content[0].text
        except Exception as e:
            raise AgentError(self.name, f"API call failed: {e}")
