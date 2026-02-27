"""Gemini agent adapter — agentic file-access review via Google Gemini API."""

from __future__ import annotations

import asyncio
import os

import httpx
import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.agents.tools import GEMINI_TOOLS, MAX_TOOL_ITERATIONS, execute_tool
from crossfire.auth import get_gemini_access_token

logger = structlog.get_logger()

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


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

        The Gemini CLI is natively agentic and runs in the repo directory
        (cwd=self.repo_dir set by BaseAgent).
        """
        cmd = [self.config.cli_command]
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        cmd.append(full_prompt)
        cmd.extend(self.config.cli_args)
        return await self._run_subprocess(cmd)

    @staticmethod
    def _extract_text(payload: dict) -> str | None:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return None
        first = candidates[0]
        if not isinstance(first, dict):
            return None
        content = first.get("content")
        if not isinstance(content, dict):
            return None
        parts = content.get("parts")
        if not isinstance(parts, list):
            return None
        texts = [
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        joined = "\n".join(t for t in texts if t.strip()).strip()
        return joined or None

    @staticmethod
    def _extract_function_calls(payload: dict) -> list[dict]:
        """Extract function_call parts from a Gemini response."""
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return []
        first = candidates[0]
        if not isinstance(first, dict):
            return []
        content = first.get("content", {})
        parts = content.get("parts", []) if isinstance(content, dict) else []
        return [p["functionCall"] for p in parts if isinstance(p, dict) and "functionCall" in p]

    async def _run_api(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Gemini API with agentic function-calling loop.

        Auth priority:
          1. GOOGLE_API_KEY env var (key-based)
          2. OAuth access token (~/.gemini/oauth_creds.json or CrossFire auth store)

        The agent loops calling read_file / search_files / list_directory until
        it produces its final response (max MAX_TOOL_ITERATIONS).
        """
        api_key = os.environ.get(self.config.api_key_env, "").strip() or None
        access_token: str | None = None

        if api_key:
            auth_label = "api_key"
        else:
            access_token = get_gemini_access_token(refresh_if_needed=True)
            if not access_token:
                raise AgentError(
                    self.name,
                    (
                        f"No credentials found. Options:\n"
                        f"  1. Set {self.config.api_key_env} env var.\n"
                        f"  2. Log in via the Gemini CLI (credentials auto-detected)."
                    ),
                )
            auth_label = "cli_oauth"

        logger.info("agent.api.start", agent=self.name, model=self.config.model, auth=auth_label)

        # Build the initial request body
        contents: list[dict] = [{"role": "user", "parts": [{"text": prompt}]}]
        tool_config = {"function_declarations": GEMINI_TOOLS}

        for iteration in range(MAX_TOOL_ITERATIONS):
            body: dict = {"contents": contents, "tools": [tool_config]}
            if system_prompt:
                body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

            # Choose auth style
            if api_key:
                url = f"{_GEMINI_BASE}/{self.config.model}:generateContent?key={api_key}"
                headers = {"Content-Type": "application/json"}
            else:
                url = f"{_GEMINI_BASE}/{self.config.model}:generateContent"
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                }

            try:
                async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                    response = await client.post(url, headers=headers, json=body)
            except httpx.TimeoutException:
                raise AgentError(self.name, f"API timed out after {self.config.timeout}s")
            except Exception as e:
                raise AgentError(self.name, f"API call failed: {e}")

            if response.status_code in (401, 403):
                raise AgentError(
                    self.name,
                    "Gemini token unauthorized — re-run `crossfire auth login --provider gemini`.",
                )

            try:
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                raise AgentError(self.name, f"API response error: {e}")

            if not isinstance(data, dict):
                raise AgentError(self.name, "Malformed API response")

            # Check for function calls
            function_calls = self._extract_function_calls(data)

            if function_calls:
                logger.info(
                    "agent.tool_use",
                    agent=self.name,
                    iteration=iteration,
                    tools=[fc.get("name") for fc in function_calls],
                )

                # Append model response to conversation
                candidates = data.get("candidates", [{}])
                model_content = candidates[0].get("content", {}) if candidates else {}
                contents.append(model_content)

                # Execute all function calls and return results
                function_responses = []
                for fc in function_calls:
                    fn_name = fc.get("name", "")
                    fn_args = fc.get("args", {})
                    result = execute_tool(fn_name, fn_args, self.repo_dir)
                    function_responses.append(
                        {
                            "functionResponse": {
                                "name": fn_name,
                                "response": {"result": result},
                            }
                        }
                    )

                contents.append({"role": "user", "parts": function_responses})

            else:
                # No function calls — extract final text
                text = self._extract_text(data)
                if text:
                    return text
                raise AgentError(self.name, "API returned empty response")

        raise AgentError(
            self.name,
            f"Exceeded {MAX_TOOL_ITERATIONS} iterations without a final response.",
        )
