"""FastModel — lightweight API-first, CLI-fallback model for cheap inference.

Used for:
  - Intent-change detection (cheap check before heavy pipeline)
  - Repo-specific context-aware system prompt generation

Tries Anthropic API first (direct SDK call); falls back to claude CLI subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from xfire.config.settings import FastModelConfig

logger = structlog.get_logger()


class FastModelUnavailable(Exception):
    """Raised when the fast model cannot be reached via API or CLI."""


class FastModel:
    """Lightweight model wrapper for cheap, fast inference calls."""

    def __init__(self, config: FastModelConfig) -> None:
        self.config = config

    async def call(self, prompt: str, system: str = "") -> str:
        """Call the fast model, returning the text response.

        Tries API first; falls back to CLI if API key is unavailable.
        Raises FastModelUnavailable if both fail.
        """
        # Try API first
        api_key = os.environ.get(self.config.api_key_env, "")
        if api_key:
            try:
                return await self._call_api(prompt, system)
            except FastModelUnavailable:
                logger.warning(
                    "fast_model.api_failed",
                    msg="API call failed, falling back to CLI",
                )
            except Exception as e:
                logger.warning(
                    "fast_model.api_error",
                    error=str(e),
                    msg="API error, falling back to CLI",
                )

        # Fallback to CLI
        try:
            return await self._call_cli(prompt, system)
        except Exception as e:
            raise FastModelUnavailable(
                f"Fast model unavailable via both API and CLI: {e}"
            ) from e

    async def _call_api(self, prompt: str, system: str) -> str:
        """Call the Anthropic API directly using the SDK."""
        try:
            import anthropic
        except ImportError as e:
            raise FastModelUnavailable(
                "anthropic SDK not installed. Run: pip install anthropic"
            ) from e

        api_key = os.environ.get(self.config.api_key_env, "")
        if not api_key:
            raise FastModelUnavailable(
                f"API key env var '{self.config.api_key_env}' not set"
            )

        client = anthropic.AsyncAnthropic(api_key=api_key)

        messages = [{"role": "user", "content": prompt}]

        kwargs: dict = {
            "model": self.config.model,
            "max_tokens": 1024,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        try:
            response = await asyncio.wait_for(
                client.messages.create(**kwargs),
                timeout=self.config.timeout,
            )
            # Extract text from content blocks
            text_parts = [
                block.text
                for block in response.content
                if hasattr(block, "text")
            ]
            return "\n".join(text_parts)
        except asyncio.TimeoutError as e:
            raise FastModelUnavailable(
                f"API call timed out after {self.config.timeout}s"
            ) from e
        except Exception as e:
            raise FastModelUnavailable(f"API call failed: {e}") from e

    async def _call_cli(self, prompt: str, system: str) -> str:
        """Call the claude CLI subprocess as a fallback.

        Writes prompt to a temp file and passes it via stdin/argument.
        """
        cli_cmd = self.config.cli_command
        cli_args = list(self.config.cli_args)

        # Build the full prompt (combine system + user if system provided)
        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n{prompt}"

        # Write prompt to a temp file so we can pass it safely
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(full_prompt)
            tmp_path = tmp.name

        try:
            cmd = [cli_cmd] + cli_args + ["-p", full_prompt]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.config.timeout,
                )
            except asyncio.TimeoutError as e:
                proc.kill()
                raise FastModelUnavailable(
                    f"CLI timed out after {self.config.timeout}s"
                ) from e

            if proc.returncode != 0:
                err = stderr.decode(errors="replace") if stderr else ""
                raise FastModelUnavailable(
                    f"CLI exited with code {proc.returncode}: {err[:300]}"
                )

            raw = stdout.decode(errors="replace")

            # If CLI returns JSON (--output-format json), extract the text
            if "--output-format" in cli_args and "json" in cli_args:
                try:
                    data = json.loads(raw)
                    # claude CLI JSON format: {"result": "...", ...}
                    if isinstance(data, dict):
                        return data.get("result", data.get("content", raw))
                except json.JSONDecodeError:
                    pass

            return raw

        except FileNotFoundError as e:
            raise FastModelUnavailable(
                f"CLI command not found: {cli_cmd}. Is it installed?"
            ) from e
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
