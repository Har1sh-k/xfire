"""Abstract base agent interface for CrossFire."""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from typing import Any

import structlog

from crossfire.config.settings import AgentConfig

logger = structlog.get_logger()


class AgentError(Exception):
    """Error from an agent execution."""

    def __init__(self, agent_name: str, message: str) -> None:
        self.agent_name = agent_name
        super().__init__(f"Agent '{agent_name}' error: {message}")


class BaseAgent(ABC):
    """Abstract base class for all CrossFire agent adapters.

    Each agent supports both CLI and API execution modes.
    """

    name: str = "base"

    def __init__(self, config: AgentConfig, repo_dir: str | None = None) -> None:
        self.config = config
        self.repo_dir = repo_dir

    async def execute(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None = None,
    ) -> str:
        """Execute agent in configured mode, return raw response."""
        if self.config.mode == "cli":
            return await self._run_cli(prompt, system_prompt, context_files)
        else:
            return await self._run_api(prompt, system_prompt, context_files)

    @abstractmethod
    async def _run_cli(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Execute via CLI subprocess."""
        ...

    @abstractmethod
    async def _run_api(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Execute via API call."""
        ...

    def parse_json_response(self, raw: str) -> dict[str, Any]:
        """Extract JSON from agent response.

        Handles: raw JSON, markdown code blocks, preamble text, etc.
        """
        # Try direct JSON parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` blocks
        json_block = re.search(r"```(?:json)?\s*\n(.*?)\n```", raw, re.DOTALL)
        if json_block:
            try:
                return json.loads(json_block.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding first { and last }
        first_brace = raw.find("{")
        last_brace = raw.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            try:
                return json.loads(raw[first_brace : last_brace + 1])
            except json.JSONDecodeError:
                pass

        raise AgentError(self.name, f"Could not parse JSON from response (length={len(raw)})")

    async def _run_subprocess(
        self, cmd: list[str], timeout: int | None = None
    ) -> str:
        """Run a subprocess asynchronously with timeout."""
        import os
        import sys

        timeout = timeout or self.config.timeout

        # On Windows, .cmd/.bat files must be run via cmd.exe /c, and paths
        # need backslash separators for CreateProcess to find them.
        if sys.platform == "win32":
            resolved = [os.path.normpath(cmd[0])] + cmd[1:]
            if resolved[0].lower().endswith((".cmd", ".bat")):
                cmd_exe = os.path.join(
                    os.environ.get("SystemRoot", "C:\\Windows"),
                    "System32", "cmd.exe",
                )
                resolved = [cmd_exe, "/c"] + resolved
            cmd = resolved

        logger.info("agent.subprocess.start", agent=self.name, cmd=cmd[0])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ,
                cwd=self.repo_dir or None,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            if proc.returncode != 0:
                err_text = stderr.decode(errors="replace") if stderr else ""
                logger.warning(
                    "agent.subprocess.error",
                    agent=self.name,
                    returncode=proc.returncode,
                    stderr=err_text[:500],
                )
                raise AgentError(self.name, f"CLI exited with code {proc.returncode}: {err_text[:500]}")

            return stdout.decode(errors="replace")

        except FileNotFoundError:
            raise AgentError(
                self.name,
                f"CLI command not found: {cmd[0]}. Is it installed and in PATH?",
            )
        except asyncio.TimeoutError:
            logger.error("agent.subprocess.timeout", agent=self.name, timeout=timeout)
            raise AgentError(self.name, f"CLI timed out after {timeout}s")
