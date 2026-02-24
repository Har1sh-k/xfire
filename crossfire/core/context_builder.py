"""Deep context extraction engine for CrossFire.

Builds complete PRContext from GitHub PRs or local diffs, including:
- Diff parsing with hunk extraction
- Full file content (head + base versions)
- Related files (imports, callers, callees)
- Test file discovery
- Git blame / history
- Config and CI file collection
- Directory structure
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import structlog

from crossfire.config.settings import AnalysisConfig
from crossfire.core.models import DiffHunk, FileContext, PRContext, RelatedFile

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".php": "php",
    ".swift": "swift",
    ".sh": "shell",
    ".bash": "shell",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".tf": "terraform",
    ".dockerfile": "dockerfile",
    ".md": "markdown",
}

# File patterns for test files
TEST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"test_.*\.py$"),
    re.compile(r".*_test\.py$"),
    re.compile(r".*\.test\.[jt]sx?$"),
    re.compile(r".*_test\.go$"),
    re.compile(r".*Test\.java$"),
    re.compile(r".*_spec\.rb$"),
    re.compile(r".*\.spec\.[jt]sx?$"),
]

# Security-relevant config patterns
SECURITY_CONFIG_PATTERNS: list[str] = [
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "Dockerfile*",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "*.toml",
    "*.cfg",
    "*.ini",
    ".env.example",
    "Makefile",
    "nginx.conf",
    "*.tf",
]

# Manifest files
MANIFEST_FILES: list[str] = [
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "go.mod",
    "go.sum",
    "Gemfile",
    "Gemfile.lock",
    "Cargo.toml",
    "Cargo.lock",
    "pom.xml",
    "build.gradle",
    "composer.json",
]

# Import patterns by language
PYTHON_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
)
JS_IMPORT_RE = re.compile(
    r"""(?:import\s+.*?\s+from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)
GO_IMPORT_RE = re.compile(r'"([^"]+)"', re.MULTILINE)


def detect_language(file_path: str) -> str | None:
    """Detect programming language from file extension."""
    ext = Path(file_path).suffix.lower()
    if not ext and Path(file_path).name.lower().startswith("dockerfile"):
        return "dockerfile"
    return EXTENSION_LANGUAGE_MAP.get(ext)


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
DIFF_FILE_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$", re.MULTILINE)
RENAME_FROM_RE = re.compile(r"^rename from (.+)$", re.MULTILINE)
RENAME_TO_RE = re.compile(r"^rename to (.+)$", re.MULTILINE)
NEW_FILE_RE = re.compile(r"^new file mode", re.MULTILINE)
DELETED_FILE_RE = re.compile(r"^deleted file mode", re.MULTILINE)


def parse_diff(diff_text: str) -> list[FileContext]:
    """Parse a unified diff into FileContext objects with DiffHunks."""
    files: list[FileContext] = []

    # Split into per-file diffs
    file_diffs = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)

    for file_diff in file_diffs:
        if not file_diff.strip():
            continue

        file_match = DIFF_FILE_RE.search(file_diff)
        if not file_match:
            continue

        a_path = file_match.group(1)
        b_path = file_match.group(2)

        is_new = bool(NEW_FILE_RE.search(file_diff))
        is_deleted = bool(DELETED_FILE_RE.search(file_diff))

        rename_from = RENAME_FROM_RE.search(file_diff)
        rename_to = RENAME_TO_RE.search(file_diff)
        is_renamed = bool(rename_from and rename_to)
        old_path = rename_from.group(1) if rename_from else None

        file_path = b_path if not is_deleted else a_path

        # Parse hunks
        hunks: list[DiffHunk] = []
        for hunk_match in HUNK_HEADER_RE.finditer(file_diff):
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2) or "1")
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4) or "1")

            # Extract hunk content until next hunk header or end
            hunk_start = hunk_match.start()
            next_hunk = HUNK_HEADER_RE.search(file_diff, hunk_match.end())
            next_file = re.search(r"^diff --git ", file_diff[hunk_match.end():], re.MULTILINE)

            if next_hunk:
                hunk_end = next_hunk.start()
            elif next_file:
                hunk_end = hunk_match.end() + next_file.start()
            else:
                hunk_end = len(file_diff)

            hunk_content = file_diff[hunk_start:hunk_end].rstrip()
            lines = hunk_content.split("\n")

            added_lines = [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")]
            removed_lines = [line[1:] for line in lines if line.startswith("-") and not line.startswith("---")]

            hunks.append(DiffHunk(
                file_path=file_path,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                content=hunk_content,
                added_lines=added_lines,
                removed_lines=removed_lines,
            ))

        files.append(FileContext(
            path=file_path,
            language=detect_language(file_path),
            diff_hunks=hunks,
            is_new=is_new,
            is_deleted=is_deleted,
            is_renamed=is_renamed,
            old_path=old_path,
        ))

    return files


# ---------------------------------------------------------------------------
# Local repo context building
# ---------------------------------------------------------------------------


def _run_git(args: list[str], repo_dir: str) -> str | None:
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _read_file_safe(path: str | Path, max_size: int = 1_000_000) -> str | None:
    """Read a file, returning None if too large or unreadable."""
    try:
        p = Path(path)
        if p.stat().st_size > max_size:
            return None
        return p.read_text(errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def _get_file_at_ref(file_path: str, ref: str, repo_dir: str) -> str | None:
    """Get file content at a specific git ref."""
    return _run_git(["show", f"{ref}:{file_path}"], repo_dir)


def _find_imports_python(content: str, file_path: str, repo_dir: str) -> list[RelatedFile]:
    """Find Python import targets and resolve to file paths."""
    related: list[RelatedFile] = []
    seen: set[str] = set()

    for match in PYTHON_IMPORT_RE.finditer(content):
        module = match.group(1) or match.group(2)
        if not module:
            continue

        # Convert module path to file path candidates
        parts = module.split(".")
        candidates = [
            os.path.join(*parts) + ".py",
            os.path.join(*parts, "__init__.py"),
        ]

        for candidate in candidates:
            full_path = os.path.join(repo_dir, candidate)
            if os.path.isfile(full_path) and candidate not in seen:
                seen.add(candidate)
                related.append(RelatedFile(
                    path=candidate,
                    relationship="imports",
                    relevance=f"Imported by {file_path}",
                ))

    return related


def _find_imports_js(content: str, file_path: str, repo_dir: str) -> list[RelatedFile]:
    """Find JS/TS import targets and resolve to file paths."""
    related: list[RelatedFile] = []
    seen: set[str] = set()
    file_dir = os.path.dirname(os.path.join(repo_dir, file_path))

    for match in JS_IMPORT_RE.finditer(content):
        module = match.group(1) or match.group(2)
        if not module or not module.startswith("."):
            continue  # skip node_modules imports

        # Resolve relative path
        base = os.path.normpath(os.path.join(file_dir, module))
        rel_base = os.path.relpath(base, repo_dir).replace("\\", "/")

        extensions = [".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"]
        candidates = [rel_base + ext for ext in extensions]
        if not os.path.splitext(rel_base)[1]:
            candidates.append(rel_base)

        for candidate in candidates:
            full_path = os.path.join(repo_dir, candidate)
            if os.path.isfile(full_path) and candidate not in seen:
                seen.add(candidate)
                related.append(RelatedFile(
                    path=candidate,
                    relationship="imports",
                    relevance=f"Imported by {file_path}",
                ))
                break

    return related


def _find_imports(content: str, file_path: str, language: str | None, repo_dir: str) -> list[RelatedFile]:
    """Find imports for a file based on its language."""
    if language == "python":
        return _find_imports_python(content, file_path, repo_dir)
    elif language in ("javascript", "typescript"):
        return _find_imports_js(content, file_path, repo_dir)
    return []


def _find_reverse_imports(file_path: str, repo_dir: str, language: str | None) -> list[RelatedFile]:
    """Find files that import the given file (imported_by relationship)."""
    related: list[RelatedFile] = []

    if language == "python":
        # Convert file path to module name
        module_name = file_path.replace("/", ".").replace("\\", ".")
        if module_name.endswith(".py"):
            module_name = module_name[:-3]
        if module_name.endswith(".__init__"):
            module_name = module_name[:-9]

        # Search for imports of this module
        try:
            result = subprocess.run(
                ["git", "grep", "-l", module_name, "--", "*.py"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    line = line.strip()
                    if line and line != file_path:
                        related.append(RelatedFile(
                            path=line,
                            relationship="imported_by",
                            relevance=f"Imports {file_path}",
                        ))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    return related[:10]  # cap reverse imports


def _find_test_files(file_path: str, repo_dir: str) -> list[str]:
    """Find test files that likely cover the given file."""
    test_files: list[str] = []
    basename = Path(file_path).stem

    # Walk the repo looking for test files matching this source file
    for root, _dirs, files in os.walk(repo_dir):
        # Skip hidden dirs, node_modules, venvs
        rel_root = os.path.relpath(root, repo_dir)
        if any(part.startswith(".") or part in ("node_modules", "venv", ".venv", "__pycache__")
               for part in rel_root.split(os.sep)):
            continue

        for f in files:
            if any(pat.match(f) for pat in TEST_PATTERNS):
                # Check if test file name relates to source file
                test_stem = f.replace("test_", "").replace("_test", "")
                test_stem = re.sub(r"\.(test|spec)\.", ".", test_stem)
                test_stem = Path(test_stem).stem

                if test_stem == basename:
                    rel_path = os.path.relpath(os.path.join(root, f), repo_dir).replace("\\", "/")
                    test_files.append(rel_path)

    return test_files


def _get_git_blame_summary(file_path: str, repo_dir: str) -> dict[str, Any] | None:
    """Get a summary of git blame for a file."""
    output = _run_git(["blame", "--porcelain", file_path], repo_dir)
    if not output:
        return None

    authors: dict[str, int] = {}
    for line in output.split("\n"):
        if line.startswith("author "):
            author = line[7:]
            authors[author] = authors.get(author, 0) + 1

    return {
        "authors": authors,
        "total_lines": sum(authors.values()),
    }


def _collect_config_files(repo_dir: str) -> dict[str, str]:
    """Collect security-relevant config files."""
    configs: dict[str, str] = {}

    for pattern in SECURITY_CONFIG_PATTERNS:
        if "*" in pattern:
            # Glob pattern
            base_dir = os.path.dirname(pattern) or "."
            full_base = os.path.join(repo_dir, base_dir)
            if not os.path.isdir(full_base):
                continue
            file_pattern = os.path.basename(pattern)
            import fnmatch
            for f in os.listdir(full_base):
                if fnmatch.fnmatch(f, file_pattern):
                    rel_path = os.path.join(base_dir, f).replace("\\", "/")
                    content = _read_file_safe(os.path.join(full_base, f))
                    if content:
                        configs[rel_path] = content
        else:
            full_path = os.path.join(repo_dir, pattern)
            if os.path.isfile(full_path):
                content = _read_file_safe(full_path)
                if content:
                    configs[pattern] = content

    return configs


def _collect_manifest_files(repo_dir: str) -> dict[str, str]:
    """Collect dependency manifest files."""
    manifests: dict[str, str] = {}
    for filename in MANIFEST_FILES:
        full_path = os.path.join(repo_dir, filename)
        if os.path.isfile(full_path):
            content = _read_file_safe(full_path)
            if content:
                manifests[filename] = content
    return manifests


def _get_directory_structure(repo_dir: str, max_depth: int = 4) -> str:
    """Get a tree-like directory structure."""
    lines: list[str] = []

    def _walk(dir_path: str, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return

        try:
            entries = sorted(os.listdir(dir_path))
        except OSError:
            return

        # Filter out hidden, caches, etc.
        entries = [
            e for e in entries
            if not e.startswith(".")
            and e not in ("node_modules", "__pycache__", "venv", ".venv", ".git", ".tox")
        ]

        for i, entry in enumerate(entries):
            full = os.path.join(dir_path, entry)
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry}")

            if os.path.isdir(full):
                extension = "    " if is_last else "│   "
                _walk(full, prefix + extension, depth + 1)

    lines.append(os.path.basename(repo_dir) + "/")
    _walk(repo_dir, "", 1)
    return "\n".join(lines[:200])  # cap output size


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ContextBuilder:
    """Builds complete PRContext for analysis."""

    def __init__(self, analysis_config: AnalysisConfig) -> None:
        self.config = analysis_config

    def build_from_diff(
        self,
        diff_text: str,
        repo_dir: str,
        base_ref: str = "HEAD~1",
        pr_title: str = "Local diff analysis",
        pr_description: str = "",
    ) -> PRContext:
        """Build PRContext from a diff string and local repo."""
        repo_dir = os.path.abspath(repo_dir)
        files = parse_diff(diff_text)

        # Enrich each file with content and context
        for fc in files:
            self._enrich_file_context(fc, repo_dir, base_ref)

        # Gather repo-level context
        readme = _read_file_safe(os.path.join(repo_dir, "README.md"))
        config_files = self._collect_configs(repo_dir)
        ci_configs = self._collect_ci_configs(config_files)
        directory_structure = _get_directory_structure(repo_dir)
        commit_messages = self._get_commit_messages(repo_dir, base_ref)
        repo_name = self._detect_repo_name(repo_dir)

        return PRContext(
            repo_name=repo_name,
            pr_title=pr_title,
            pr_description=pr_description,
            base_branch=base_ref,
            files=files,
            commit_messages=commit_messages,
            readme_content=readme,
            ci_config_files=ci_configs,
            config_files=config_files,
            directory_structure=directory_structure,
        )

    def build_from_staged(self, repo_dir: str) -> PRContext:
        """Build PRContext from staged changes in a git repo."""
        diff_text = _run_git(["diff", "--cached"], repo_dir) or ""
        if not diff_text.strip():
            logger.warning(
                "context.no_staged_changes",
                msg="No staged changes found, falling back to unstaged diff",
            )
            diff_text = _run_git(["diff"], repo_dir) or ""

        return self.build_from_diff(
            diff_text=diff_text,
            repo_dir=repo_dir,
            base_ref="HEAD",
            pr_title="Staged changes analysis",
        )

    def build_from_refs(self, repo_dir: str, base_ref: str, head_ref: str) -> PRContext:
        """Build PRContext from a commit range."""
        diff_text = _run_git(["diff", f"{base_ref}...{head_ref}"], repo_dir) or ""
        return self.build_from_diff(
            diff_text=diff_text,
            repo_dir=repo_dir,
            base_ref=base_ref,
            pr_title=f"Changes {base_ref}...{head_ref}",
        )

    def build_from_patch_file(self, patch_path: str, repo_dir: str) -> PRContext:
        """Build PRContext from a .patch file."""
        p = Path(patch_path)
        if not p.exists():
            raise FileNotFoundError(f"Patch file not found: {patch_path}")
        diff_text = p.read_text(errors="replace")
        return self.build_from_diff(
            diff_text=diff_text,
            repo_dir=repo_dir,
            pr_title=f"Patch analysis: {p.name}",
        )

    async def build_from_github_pr(
        self,
        repo: str,
        pr_number: int,
        github_token: str,
    ) -> PRContext:
        """Build PRContext from a GitHub PR via API."""
        from crossfire.integrations.github.pr_loader import load_pr_context

        return await load_pr_context(
            repo=repo,
            pr_number=pr_number,
            token=github_token,
            config=self.config,
        )

    # --- Private helpers ---

    def _enrich_file_context(
        self, fc: FileContext, repo_dir: str, base_ref: str
    ) -> None:
        """Add full content, related files, blame, and test info to a FileContext."""
        full_path = os.path.join(repo_dir, fc.path)

        # Head content
        if not fc.is_deleted:
            fc.content = _read_file_safe(full_path)

        # Base content (use old_path for renames, since the base version lives at the old path)
        if not fc.is_new:
            base_path = fc.old_path if fc.is_renamed and fc.old_path else fc.path
            fc.base_content = _get_file_at_ref(base_path, base_ref, repo_dir)

        depth = self.config.context_depth

        if depth in ("medium", "deep") and fc.content:
            # Find imports
            imports = _find_imports(fc.content, fc.path, fc.language, repo_dir)
            fc.related_files.extend(imports)

        if depth == "deep":
            # Find reverse imports (who imports this file)
            reverse = _find_reverse_imports(fc.path, repo_dir, fc.language)
            fc.related_files.extend(reverse)

            # Git blame
            fc.git_blame_summary = _get_git_blame_summary(fc.path, repo_dir)

            # Test files
            if self.config.include_test_files:
                fc.test_files = _find_test_files(fc.path, repo_dir)

        # Load related file contents (capped)
        loaded = 0
        for rf in fc.related_files:
            if loaded >= self.config.max_related_files:
                break
            rf_path = os.path.join(repo_dir, rf.path)
            rf.content = _read_file_safe(rf_path, max_size=500_000)
            if rf.content:
                loaded += 1

    def _collect_configs(self, repo_dir: str) -> dict[str, str]:
        """Collect config files based on depth setting."""
        if self.config.context_depth == "shallow":
            return {}
        configs = _collect_config_files(repo_dir)
        manifests = _collect_manifest_files(repo_dir)
        configs.update(manifests)
        return configs

    def _collect_ci_configs(self, all_configs: dict[str, str]) -> dict[str, str]:
        """Extract CI config files from the collected configs."""
        ci_configs: dict[str, str] = {}
        for path, content in all_configs.items():
            if ".github/workflows" in path or "Jenkinsfile" in path or ".gitlab-ci" in path:
                ci_configs[path] = content
        return ci_configs

    def _get_commit_messages(self, repo_dir: str, base_ref: str) -> list[str]:
        """Get commit messages from base ref to HEAD."""
        output = _run_git(
            ["log", "--format=%s", f"{base_ref}..HEAD", "--max-count=50"],
            repo_dir,
        )
        if not output:
            return []
        return [line.strip() for line in output.strip().split("\n") if line.strip()]

    def _detect_repo_name(self, repo_dir: str) -> str:
        """Detect repo name from git remote or directory name."""
        output = _run_git(["remote", "get-url", "origin"], repo_dir)
        if output:
            url = output.strip()
            # Handle https://github.com/owner/repo.git or git@github.com:owner/repo.git
            match = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", url)
            if match:
                return match.group(1)
        return Path(repo_dir).name
