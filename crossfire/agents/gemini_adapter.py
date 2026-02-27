"""Gemini agent adapter - Gemini API key mode or OAuth token fallback."""

from __future__ import annotations

import asyncio
import os

import httpx
import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.auth import get_gemini_access_token

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
        joined = "\n".join([text for text in texts if text.strip()]).strip()
        return joined or None

    async def _run_api(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Gemini API key mode, with OAuth fallback when no API key is set."""
        api_key = os.environ.get(self.config.api_key_env)

        if api_key:
            try:
                import google.generativeai as genai
            except ImportError:
                raise AgentError(self.name, "google-generativeai package not installed")

            genai.configure(api_key=api_key)
            logger.info("agent.api.start", agent=self.name, model=self.config.model)

            try:
                model = genai.GenerativeModel(
                    self.config.model,
                    system_instruction=system_prompt if system_prompt else None,
                )
                response = await asyncio.wait_for(
                    model.generate_content_async(prompt),
                    timeout=self.config.timeout,
                )
                return response.text
            except asyncio.TimeoutError:
                raise AgentError(self.name, f"API timed out after {self.config.timeout}s")
            except Exception as e:
                raise AgentError(self.name, f"API call failed: {e}")

        access_token = get_gemini_access_token(refresh_if_needed=True)
        if not access_token:
            raise AgentError(
                self.name,
                (
                    f"API key not found in env var {self.config.api_key_env}. "
                    "Run `crossfire auth login --provider gemini` for OAuth subscription auth."
                ),
            )

        logger.info("agent.api.start", agent=self.name, model=self.config.model, auth="oauth")

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.config.model}:generateContent"
        )
        payload: dict[str, object] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ]
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException:
            raise AgentError(self.name, f"API timed out after {self.config.timeout}s")
        except Exception as e:
            raise AgentError(self.name, f"OAuth API call failed: {e}")

        if response.status_code in (401, 403):
            raise AgentError(
                self.name,
                "Gemini OAuth token unauthorized. Re-run `crossfire auth login --provider gemini`.",
            )

        try:
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise AgentError(self.name, f"OAuth API call failed: {e}")

        if not isinstance(data, dict):
            raise AgentError(self.name, "OAuth API returned malformed response")

        text = self._extract_text(data)
        if not text:
            raise AgentError(self.name, "OAuth API returned empty response")

        return text
