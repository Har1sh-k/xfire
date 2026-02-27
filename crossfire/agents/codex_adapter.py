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
        """Run via Codex CLI non-interactively.

        Uses the same flags as OpenClaw's DEFAULT_CODEX_BACKEND:
          codex exec --json --color never --sandbox read-only --skip-git-repo-check <prompt>

        --sandbox read-only ensures the agent can read files but cannot write,
        which is the correct posture for a security review agent.
        The Codex CLI is natively agentic and runs in the repo directory.
        """
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        cmd = [
            self.config.cli_command,
            "exec",
            "--json",
            "--color", "never",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
        ]
        cmd.extend(self.config.cli_args)
        # Pass prompt via stdin to avoid Windows CreateProcess 32K command-line limit.
        # codex exec reads from stdin when no positional prompt arg is given.
        raw = await self._run_subprocess(cmd, stdin_data=full_prompt)
        # Codex outputs JSONL with --json; extract response text and reasoning
        response, reasoning = self._parse_jsonl_output(raw)
        if reasoning:
            self.thinking_trace = reasoning
        return response

    @staticmethod
    def _parse_jsonl_output(raw: str) -> tuple[str, str]:
        """Extract assistant text and reasoning from Codex JSONL stream output.

        Handles the current Codex CLI JSONL format:
          {type:"item.completed", item:{type:"agent_message", text:"..."}}  — response
          {type:"item.completed", item:{type:"reasoning",      text:"..."}}  — thinking

        Also handles the legacy OpenAI format:
          {role:"assistant", content:[{type:"output_text", text:"..."}]}
          {type:"output_text", text:"..."}

        Returns (response_text, reasoning_text).
        """
        import json as _json

        texts: list[str] = []
        reasoning_parts: list[str] = []

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue

            # Current Codex CLI format: item.completed events
            if obj.get("type") == "item.completed" and isinstance(obj.get("item"), dict):
                item = obj["item"]
                item_type = item.get("type", "")
                text = item.get("text", "")
                if item_type == "agent_message" and text:
                    texts.append(text)
                elif item_type == "reasoning" and text:
                    reasoning_parts.append(text)

            # Legacy: {role: "assistant", content: [{type: "output_text", text: "..."}]}
            elif obj.get("role") == "assistant":
                content = obj.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "output_text":
                            texts.append(block.get("text", ""))
                elif isinstance(content, str):
                    texts.append(content)

            # Flat: {type: "output_text", text: "..."}
            elif obj.get("type") == "output_text":
                texts.append(obj.get("text", ""))

        response = "\n".join(t for t in texts if t.strip()).strip()
        reasoning = "\n\n---\n\n".join(r for r in reasoning_parts if r.strip()).strip()
        # Fall back to raw output if we couldn't parse any response text
        return (response if response else raw, reasoning)

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
