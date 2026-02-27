"""Claude agent adapter — agentic file-access review with OAuth / API key support."""

from __future__ import annotations

import os

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.agents.tools import CLAUDE_TOOLS, MAX_TOOL_ITERATIONS, execute_tool
from crossfire.auth import get_claude_setup_token, read_claude_cli_credentials

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

        Claude Code CLI is natively agentic — it reads, searches, and navigates
        the repository using its built-in tools.  --add-dir grants access and
        --disallowedTools blocks write operations.
        """
        cmd = [self.config.cli_command]
        cmd.extend(["-p", prompt, "--output-format", "json"])

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        if self.repo_dir:
            cmd.extend(["--add-dir", self.repo_dir])

        cmd.extend(["--disallowedTools", ",".join(_WRITE_TOOLS)])
        cmd.extend(self.config.cli_args)

        return await self._run_subprocess(cmd)

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
                oauth_token = read_claude_cli_credentials()
                if oauth_token:
                    client = anthropic.AsyncAnthropic(
                        auth_token=oauth_token, timeout=self.config.timeout
                    )
                    auth_label = "cli_oauth"
                else:
                    raise AgentError(
                        self.name,
                        (
                            f"No credentials found. Options:\n"
                            f"  1. Set {self.config.api_key_env} env var.\n"
                            f"  2. Run `crossfire auth login --provider claude`.\n"
                            f"  3. Log in with the Claude CLI (auto-detected)."
                        ),
                    )

        logger.info("agent.api.start", agent=self.name, model=self.config.model, auth=auth_label)

        messages: list[dict] = [{"role": "user", "content": prompt}]

        for iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response = await client.messages.create(
                    model=self.config.model,
                    system=system_prompt,
                    messages=messages,
                    max_tokens=8192,
                    tools=CLAUDE_TOOLS,
                )
            except Exception as e:
                raise AgentError(self.name, f"API call failed: {e}")

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]

            if not tool_uses:
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
