"""Gemini agent adapter — Gemini CLI + Google AI API."""

from __future__ import annotations

import os
import tempfile

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.config.settings import AgentConfig

logger = structlog.get_logger()


class GeminiAgent(BaseAgent):
    """Agent adapter for Gemini (Google Gemini CLI or Google AI API)."""

    name = "gemini"

    async def _run_cli(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Gemini CLI.

        Command: gemini "{prompt}"
        """
        cmd = [self.config.cli_command]

        # Combine system prompt and user prompt for CLI
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        cmd.append(full_prompt)

        # Add any extra CLI args from config
        cmd.extend(self.config.cli_args)

        return await self._run_subprocess(cmd)

    async def _run_api(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Google Generative AI API."""
        try:
            import google.generativeai as genai
        except ImportError:
            raise AgentError(self.name, "google-generativeai package not installed")

        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise AgentError(self.name, f"API key not found in env var {self.config.api_key_env}")

        genai.configure(api_key=api_key)

        logger.info("agent.api.start", agent=self.name, model=self.config.model)

        try:
            model = genai.GenerativeModel(
                self.config.model,
                system_instruction=system_prompt if system_prompt else None,
            )
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            raise AgentError(self.name, f"API call failed: {e}")
