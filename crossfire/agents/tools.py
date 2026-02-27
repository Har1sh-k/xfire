"""Shared read-only filesystem tools for agentic code review.

These tools are passed to LLM APIs (Claude, OpenAI, Gemini) during API-mode
reviews so agents can autonomously navigate and read the codebase.

Security: all tools are read-only.  Callers must never expose write operations.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

# Max tool-use iterations to prevent runaway loops
MAX_TOOL_ITERATIONS = 20


# ---------------------------------------------------------------------------
# Tool definitions for each API format
# ---------------------------------------------------------------------------

# Anthropic (Claude) tool schema
CLAUDE_TOOLS: list[dict] = [
    {
        "name": "read_file",
        "description": (
            "Read the full contents of a file. "
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
            "Search for a regex pattern across files. "
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

# OpenAI (Codex) function schema
OPENAI_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of a file for code review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or repo-relative file path.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a regex pattern across files in the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern."},
                    "directory": {"type": "string", "description": "Directory to search (optional)."},
                    "file_glob": {"type": "string", "description": 'File glob, e.g. "*.py" (optional).'},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List contents of a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (optional)."},
                    "recursive": {"type": "boolean", "description": "List recursively."},
                },
                "required": [],
            },
        },
    },
]

# Gemini function declarations schema
GEMINI_TOOLS: list[dict] = [
    {
        "name": "read_file",
        "description": "Read the full contents of a source file for code review.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {
                    "type": "STRING",
                    "description": "Absolute or repo-relative file path.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": "Search files for a regex pattern, returning matching lines.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "pattern": {"type": "STRING", "description": "Regex pattern to search for."},
                "directory": {"type": "STRING", "description": "Directory to search (optional)."},
                "file_glob": {"type": "STRING", "description": "File glob filter (optional)."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a path.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Directory path (optional)."},
                "recursive": {"type": "BOOLEAN", "description": "List recursively."},
            },
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool executors (read-only, shared across all adapters)
# ---------------------------------------------------------------------------

def execute_tool(tool_name: str, tool_input: dict, repo_dir: str | None) -> str:
    """Dispatch a tool call and return the result as a string."""
    if tool_name == "read_file":
        return _read_file(tool_input.get("path", ""), repo_dir)
    if tool_name == "search_files":
        return _search_files(
            tool_input.get("pattern", ""),
            tool_input.get("directory"),
            tool_input.get("file_glob"),
            repo_dir,
        )
    if tool_name == "list_directory":
        return _list_directory(
            tool_input.get("path"),
            bool(tool_input.get("recursive", False)),
            repo_dir,
        )
    return f"Unknown tool: {tool_name}"


def _read_file(path: str, repo_dir: str | None) -> str:
    resolved = Path(path)
    if not resolved.is_absolute() and repo_dir:
        resolved = Path(repo_dir) / resolved
    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        numbered = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
        return f"=== {resolved} ({len(lines)} lines) ===\n{numbered}"
    except FileNotFoundError:
        return f"Error: file not found: {resolved}"
    except OSError as e:
        return f"Error reading {resolved}: {e}"


def _search_files(
    pattern: str,
    directory: str | None,
    file_glob: str | None,
    repo_dir: str | None,
) -> str:
    search_dir = directory or repo_dir or "."
    glob_arg = file_glob or "*"

    try:
        result = subprocess.run(
            ["grep", "-rn", "--include", glob_arg, "-E", pattern, search_dir],
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = result.stdout.strip()
        if not out:
            return f"No matches for pattern '{pattern}' in {search_dir}"
        lines = out.splitlines()
        if len(lines) > 200:
            lines = lines[:200] + [f"... ({len(lines) - 200} more lines truncated)"]
        return "\n".join(lines)
    except FileNotFoundError:
        return _search_files_python(pattern, search_dir, file_glob)
    except subprocess.TimeoutExpired:
        return "Error: search timed out"
    except Exception as e:
        return f"Error searching: {e}"


def _search_files_python(pattern: str, directory: str, file_glob: str | None) -> str:
    glob_pat = file_glob or "*"
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return f"Invalid regex: {e}"

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
        return f"Error: {e}"

    if not matches:
        return f"No matches for '{pattern}' in {directory}"
    return "\n".join(matches)


def _list_directory(path: str | None, recursive: bool, repo_dir: str | None) -> str:
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
        return f"Error: not found: {target}"
    except OSError as e:
        return f"Error: {e}"
