"""Codex agent adapter — agentic file-access review via OpenAI API."""

from __future__ import annotations

import json
import os

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.agents.tools import MAX_TOOL_ITERATIONS, OPENAI_TOOLS, execute_tool
from crossfire.auth import get_codex_api_key

logger = structlog.get_logger()


class CodexAgent(BaseAgent):
    """Agent adapter for Codex (OpenAI API with agentic tool-use)."""

    name = "codex"

    async def _run_cli(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Codex CLI.

        The Codex CLI is natively agentic and has its own file access.
        It runs in the repo directory (cwd=self.repo_dir set by BaseAgent).
        """
        cmd = [self.config.cli_command]
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        cmd.extend(["-q", full_prompt])
        cmd.extend(self.config.cli_args)
        return await self._run_subprocess(cmd)

    async def _run_api(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via OpenAI API with agentic tool-use loop.

        Auth priority:
          1. OPENAI_API_KEY env var
          2. OPENAI_API_KEY stored in ~/.codex/auth.json (Codex CLI credentials)

        The agent loops calling read_file / search_files / list_directory until
        it produces its final response (max MAX_TOOL_ITERATIONS).
        """
        try:
            import openai
        except ImportError:
            raise AgentError(self.name, "openai package not installed")

        api_key = os.environ.get(self.config.api_key_env, "").strip() or None
        auth_label = "api_key"

        if not api_key:
            api_key = get_codex_api_key(refresh_if_needed=False)
            auth_label = "codex_cli"

        if not api_key:
            raise AgentError(
                self.name,
                (
                    f"No credentials found. Options:\n"
                    f"  1. Set {self.config.api_key_env} env var.\n"
                    f"  2. Log in via the Codex CLI (credentials auto-detected from ~/.codex/auth.json)."
                ),
            )

        client = openai.AsyncOpenAI(api_key=api_key, timeout=self.config.timeout)
        logger.info("agent.api.start", agent=self.name, model=self.config.model, auth=auth_label)

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        extra_params: dict = {}
        if self.config.enable_thinking:
            # o3 and o1 models support reasoning_effort
            extra_params["reasoning_effort"] = self.config.reasoning_effort

        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response = await client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    tools=OPENAI_TOOLS,
                    tool_choice="auto",
                    **extra_params,
                )
            except Exception as e:
                if isinstance(e, AgentError):
                    raise
                raise AgentError(self.name, f"API call failed: {e}")

            choice = response.choices[0]
            message = choice.message
            messages.append(message.model_dump(exclude_unset=True))

            if choice.finish_reason == "tool_calls" and message.tool_calls:
                logger.info(
                    "agent.tool_use",
                    agent=self.name,
                    iteration=iteration,
                    tools=[tc.function.name for tc in message.tool_calls],
                )
                for tool_call in message.tool_calls:
                    try:
                        tool_input = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        tool_input = {}
                    result = execute_tool(tool_call.function.name, tool_input, self.repo_dir)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        }
                    )
            else:
                # Final text response
                content = message.content
                if content is None:
                    raise AgentError(self.name, "API returned empty response")
                return content

        raise AgentError(
            self.name,
            f"Exceeded {MAX_TOOL_ITERATIONS} tool-use iterations without a final response.",
        )
