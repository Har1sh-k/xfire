"""Claude agent adapter — agentic file-access review with OAuth / API key support."""

from __future__ import annotations

import os

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.agents.tools import CLAUDE_TOOLS, MAX_TOOL_ITERATIONS, execute_tool
from crossfire.auth import get_claude_setup_token

logger = structlog.get_logger()

# Tools blocked in Claude CLI mode (read-only enforcement)
_WRITE_TOOLS = ["Write", "Edit", "MultiEdit", "Bash"]


class ClaudeAgent(BaseAgent):
    """Agent adapter for Claude (Claude Code CLI or Anthropic API)."""

    name = "claude"

    async def _run_cli(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Claude Code CLI with read-only agentic file access.

        Prompt is passed via stdin to avoid Windows CreateProcess 32K
        command-line limit (ERROR_FILENAME_EXCED_RANGE → FileNotFoundError).

        Flags mirror OpenClaw's DEFAULT_CLAUDE_BACKEND:
          claude -p --output-format json --dangerously-skip-permissions
        with --append-system-prompt and --add-dir added for CrossFire.
        """
        cmd = [self.config.cli_command]
        # -p without a value = non-interactive mode, reads prompt from stdin
        cmd.extend(["-p", "--output-format", "json", "--dangerously-skip-permissions"])

        if system_prompt:
            cmd.extend(["--append-system-prompt", system_prompt])

        if self.repo_dir:
            cmd.extend(["--add-dir", self.repo_dir])

        cmd.extend(["--disallowedTools", ",".join(_WRITE_TOOLS)])
        cmd.extend(self.config.cli_args)

        # Pass prompt via stdin — avoids Windows 32K command-line limit
        raw = await self._run_subprocess(cmd, stdin_data=prompt)
        return self._unwrap_cli_json(raw)

    @staticmethod
    def _unwrap_cli_json(raw: str) -> str:
        """Extract the actual response text from the Claude CLI JSON wrapper.

        Claude CLI with --output-format json wraps the response as:
          {"type":"result","subtype":"success","is_error":false,"result":"<actual text>",...}

        We extract only the ``result`` field so callers get Claude's raw text
        (which may itself be a JSON findings object) rather than the CLI envelope.
        Falls back to raw output on any parse failure.
        """
        import json as _json

        try:
            wrapper = _json.loads(raw.strip())
            if (
                isinstance(wrapper, dict)
                and wrapper.get("type") == "result"
                and not wrapper.get("is_error", False)
                and "result" in wrapper
            ):
                return str(wrapper["result"])
        except (_json.JSONDecodeError, TypeError):
            pass
        return raw

    async def _run_api(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Anthropic API with agentic tool-use loop.

        Auth priority:
          1. ANTHROPIC_API_KEY env var
          2. Claude setup-token in CrossFire auth store
          3. Claude Code CLI OAuth token (~/.claude/.credentials.json)

        The agent loops calling read_file / search_files / list_directory until
        it produces its final JSON review response (max MAX_TOOL_ITERATIONS).
        """
        try:
            import anthropic
        except ImportError:
            raise AgentError(self.name, "anthropic package not installed")

        api_key = os.environ.get(self.config.api_key_env, "").strip() or None

        if api_key:
            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=self.config.timeout)
            auth_label = "api_key"
        else:
            setup_token = get_claude_setup_token()
            if setup_token:
                client = anthropic.AsyncAnthropic(api_key=setup_token, timeout=self.config.timeout)
                auth_label = "setup_token"
            else:
                # NOTE: The Claude CLI OAuth token (~/.claude/.credentials.json)
                # does NOT work with api.anthropic.com — Anthropic explicitly
                # rejects it with "OAuth authentication is currently not supported."
                # Users need a real ANTHROPIC_API_KEY for API mode.
                raise AgentError(
                    self.name,
                    (
                        f"No credentials found for API mode. Options:\n"
                        f"  1. Set {self.config.api_key_env} env var (get key at console.anthropic.com).\n"
                        f"  2. Run `crossfire auth login --provider claude` to store a setup-token.\n"
                        f"  Note: CLI mode (default) uses your Claude Code login automatically."
                    ),
                )

        logger.info("agent.api.start", agent=self.name, model=self.config.model, auth=auth_label)

        messages: list[dict] = [{"role": "user", "content": prompt}]
        thinking_parts: list[str] = []

        # Extended thinking parameters (requires a supported model)
        extra_params: dict = {}
        if self.config.enable_thinking:
            extra_params["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.config.thinking_budget,
            }
            extra_params["betas"] = ["interleaved-thinking-2025-05-14"]

        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response = await client.messages.create(
                    model=self.config.model,
                    system=system_prompt,
                    messages=messages,
                    max_tokens=max(8192, self.config.thinking_budget + 4096)
                    if self.config.enable_thinking
                    else 8192,
                    tools=CLAUDE_TOOLS,
                    **extra_params,
                )
            except Exception as e:
                raise AgentError(self.name, f"API call failed: {e}")

            # Collect thinking blocks across all iterations
            for block in response.content:
                if block.type == "thinking" and hasattr(block, "thinking"):
                    thinking_parts.append(block.thinking)

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]

            if not tool_uses:
                if thinking_parts:
                    self.thinking_trace = "\n\n---\n\n".join(thinking_parts)
                    logger.info(
                        "agent.thinking_complete",
                        agent=self.name,
                        thinking_length=len(self.thinking_trace),
                    )
                if text_blocks:
                    return text_blocks[-1].text
                raise AgentError(self.name, "API returned no text content")

            logger.info(
                "agent.tool_use",
                agent=self.name,
                iteration=iteration,
                tools=[t.name for t in tool_uses],
            )

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool_use in tool_uses:
                result = execute_tool(tool_use.name, tool_use.input, self.repo_dir)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result,
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        raise AgentError(
            self.name,
            f"Exceeded {MAX_TOOL_ITERATIONS} tool-use iterations without a final response.",
        )
