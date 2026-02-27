"""Claude agent adapter — agentic file-access review with OAuth / API key support."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import structlog

from crossfire.agents.base import AgentError, BaseAgent
from crossfire.auth import get_claude_setup_token, read_claude_cli_credentials

logger = structlog.get_logger()

# Tools blocked in Claude CLI mode (read-only enforcement)
_WRITE_TOOLS = ["Write", "Edit", "MultiEdit", "Bash"]

# Max tool-use iterations to prevent runaway loops
_MAX_TOOL_ITERATIONS = 20

# Tools exposed to Claude during API-mode reviews
_REVIEW_TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read the full contents of a file on the local filesystem. "
            "Use this to inspect source files during code review."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path, or path relative to the repository root.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search for a regex pattern in files. "
            "Returns matching lines with file paths and line numbers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for."},
                "directory": {
                    "type": "string",
                    "description": "Directory to search in (defaults to repository root).",
                },
                "file_glob": {
                    "type": "string",
                    "description": 'File glob filter, e.g. "*.py" or "**/*.ts". Optional.',
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and subdirectories at a given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path (defaults to repository root).",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list recursively. Default false.",
                },
            },
            "required": [],
        },
    },
]


def _tool_read_file(path: str, repo_dir: str | None) -> str:
    resolved = Path(path)
    if not resolved.is_absolute() and repo_dir:
        resolved = Path(repo_dir) / resolved
    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        # Return with line numbers for easier reference
        numbered = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
        return f"=== {resolved} ({len(lines)} lines) ===\n{numbered}"
    except FileNotFoundError:
        return f"Error: file not found: {resolved}"
    except OSError as e:
        return f"Error reading {resolved}: {e}"


def _tool_search_files(
    pattern: str,
    directory: str | None,
    file_glob: str | None,
    repo_dir: str | None,
) -> str:
    search_dir = directory or repo_dir or "."
    cmd = ["grep", "-rn", "--include", file_glob or "*", "-E", pattern, search_dir]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout.strip()
        if not out:
            return f"No matches for pattern '{pattern}' in {search_dir}"
        # Limit output to avoid huge context
        lines = out.splitlines()
        if len(lines) > 200:
            lines = lines[:200] + [f"... ({len(lines) - 200} more lines truncated)"]
        return "\n".join(lines)
    except FileNotFoundError:
        # grep not available — fall back to Python
        return _tool_search_files_python(pattern, search_dir, file_glob)
    except subprocess.TimeoutExpired:
        return "Error: search timed out"
    except Exception as e:
        return f"Error searching: {e}"


def _tool_search_files_python(pattern: str, directory: str, file_glob: str | None) -> str:
    import fnmatch
    import re

    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return f"Invalid regex pattern: {e}"

    glob_pat = file_glob or "*"
    matches: list[str] = []
    base = Path(directory)

    try:
        for fpath in base.rglob("*"):
            if not fpath.is_file():
                continue
            if not fnmatch.fnmatch(fpath.name, glob_pat):
                continue
            try:
                for i, line in enumerate(
                    fpath.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    if compiled.search(line):
                        matches.append(f"{fpath}:{i}: {line}")
                        if len(matches) >= 200:
                            break
            except OSError:
                continue
            if len(matches) >= 200:
                break
    except Exception as e:
        return f"Error during search: {e}"

    if not matches:
        return f"No matches for '{pattern}' in {directory}"
    return "\n".join(matches)


def _tool_list_directory(path: str | None, recursive: bool, repo_dir: str | None) -> str:
    target = Path(path or repo_dir or ".")
    try:
        if recursive:
            entries = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file())
            if len(entries) > 500:
                entries = entries[:500] + [f"... ({len(entries) - 500} more)"]
        else:
            entries = sorted(
                f"{'[DIR] ' if p.is_dir() else '      '}{p.name}" for p in target.iterdir()
            )
        return f"=== {target} ===\n" + "\n".join(entries)
    except FileNotFoundError:
        return f"Error: directory not found: {target}"
    except OSError as e:
        return f"Error listing {target}: {e}"


def _execute_tool(
    tool_name: str,
    tool_input: dict,
    repo_dir: str | None,
) -> str:
    if tool_name == "read_file":
        return _tool_read_file(tool_input.get("path", ""), repo_dir)
    if tool_name == "search_files":
        return _tool_search_files(
            tool_input.get("pattern", ""),
            tool_input.get("directory"),
            tool_input.get("file_glob"),
            repo_dir,
        )
    if tool_name == "list_directory":
        return _tool_list_directory(
            tool_input.get("path"),
            bool(tool_input.get("recursive", False)),
            repo_dir,
        )
    return f"Unknown tool: {tool_name}"


class ClaudeAgent(BaseAgent):
    """Agent adapter for Claude (Claude Code CLI or Anthropic API)."""

    name = "claude"

    async def _run_cli(
        self,
        prompt: str,
        system_prompt: str,
        context_files: list[str] | None,
    ) -> str:
        """Run via Claude Code CLI with read-only agentic file access to the repo.

        Claude Code CLI is natively agentic — it can read, search, and navigate
        the repository using its built-in tools.  We grant access via --add-dir
        and block write tools so it never modifies files.
        """
        cmd = [self.config.cli_command]
        cmd.extend(["-p", prompt, "--output-format", "json"])

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        # Grant read-only filesystem access to the repo being reviewed
        if self.repo_dir:
            cmd.extend(["--add-dir", self.repo_dir])

        # Explicitly block all write/edit tools
        cmd.extend(["--disallowedTools", ",".join(_WRITE_TOOLS)])

        # Any extra CLI args from config
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

        The agent runs in a tool-use loop: it may call read_file, search_files,
        and list_directory any number of times (up to _MAX_TOOL_ITERATIONS)
        before producing its final JSON review response.
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

        # Agentic tool-use loop
        messages: list[dict] = [{"role": "user", "content": prompt}]

        for iteration in range(_MAX_TOOL_ITERATIONS):
            try:
                response = await client.messages.create(
                    model=self.config.model,
                    system=system_prompt,
                    messages=messages,
                    max_tokens=8192,
                    tools=_REVIEW_TOOLS,
                )
            except Exception as e:
                raise AgentError(self.name, f"API call failed: {e}")

            # Check for tool-use blocks
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            text_blocks = [b for b in response.content if b.type == "text"]

            if not tool_uses:
                # No more tool calls — return the final text response
                if text_blocks:
                    return text_blocks[-1].text
                raise AgentError(self.name, "API returned no text content")

            logger.info(
                "agent.tool_use",
                agent=self.name,
                iteration=iteration,
                tools=[t.name for t in tool_uses],
            )

            # Append assistant message, then tool results
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool_use in tool_uses:
                result = _execute_tool(tool_use.name, tool_use.input, self.repo_dir)
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
            f"Exceeded {_MAX_TOOL_ITERATIONS} tool-use iterations without a final response.",
        )
