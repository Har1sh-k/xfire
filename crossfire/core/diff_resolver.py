"""DiffResolver — resolves all crossfire scan input modes into (diff, head, base).

Supported modes:
    from_refs(repo_dir, base, head)        git diff base..head
    from_range(repo_dir, range_str)        git diff abc~1..abc
    from_patch(patch_path, repo_dir)       read file, HEAD as head_commit
    from_since_last_scan(repo_dir, state)  git diff last..HEAD
    from_since_date(repo_dir, date_str)    git log --since=DATE, oldest→HEAD
    from_last_n(repo_dir, n)               git diff HEAD~N..HEAD
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# DiffResult
# ---------------------------------------------------------------------------


@dataclass
class DiffResult:
    """The resolved output of any diff resolution mode."""

    diff_text: str
    head_commit: str
    base_commit: str
    commit_range_desc: str


# ---------------------------------------------------------------------------
# Internal git helper
# ---------------------------------------------------------------------------


def _run_git(repo_dir: str, args: list[str], timeout: int = 60) -> str | None:
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=repo_dir,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout
        logger.debug(
            "diff_resolver.git_error",
            args=args,
            stderr=result.stderr[:200],
        )
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("diff_resolver.git_unavailable", error=str(e))
        return None


def _resolve_sha(repo_dir: str, ref: str) -> str:
    """Resolve a git ref to a full SHA."""
    out = _run_git(repo_dir, ["rev-parse", ref])
    return out.strip() if out else ref


def _get_head(repo_dir: str) -> str:
    """Return the current HEAD SHA."""
    out = _run_git(repo_dir, ["rev-parse", "HEAD"])
    return out.strip() if out else ""


# ---------------------------------------------------------------------------
# DiffResolver
# ---------------------------------------------------------------------------


class DiffResolverError(Exception):
    """Raised when a diff cannot be resolved."""


class DiffResolver:
    """Resolves various scan input modes into a DiffResult."""

    @staticmethod
    def from_refs(repo_dir: str, base: str, head: str) -> DiffResult:
        """Resolve using explicit base and head refs.

        Uses git diff base..head (two-dot diff — all changes between the two commits).
        """
        repo_dir = str(Path(repo_dir).resolve())
        diff_text = _run_git(repo_dir, ["diff", f"{base}..{head}"]) or ""
        head_commit = _resolve_sha(repo_dir, head)
        base_commit = _resolve_sha(repo_dir, base)

        logger.info(
            "diff_resolver.from_refs",
            base=base,
            head=head,
            diff_lines=diff_text.count("\n"),
        )
        return DiffResult(
            diff_text=diff_text,
            head_commit=head_commit,
            base_commit=base_commit,
            commit_range_desc=f"{base}..{head}",
        )

    @staticmethod
    def from_range(repo_dir: str, range_str: str) -> DiffResult:
        """Resolve from a commit range string like 'abc123~1..abc123'.

        Accepts any syntax git diff understands: SHA~N..SHA, branch..branch, etc.
        """
        repo_dir = str(Path(repo_dir).resolve())

        # Split on '..' to extract head and base
        if ".." in range_str:
            parts = range_str.split("..", 1)
            base_ref = parts[0].strip()
            head_ref = parts[1].strip()
        else:
            # Single commit — treat as HEAD~1..commit
            base_ref = f"{range_str}~1"
            head_ref = range_str

        diff_text = _run_git(repo_dir, ["diff", range_str]) or ""
        head_commit = _resolve_sha(repo_dir, head_ref)
        base_commit = _resolve_sha(repo_dir, base_ref)

        logger.info(
            "diff_resolver.from_range",
            range=range_str,
            diff_lines=diff_text.count("\n"),
        )
        return DiffResult(
            diff_text=diff_text,
            head_commit=head_commit,
            base_commit=base_commit,
            commit_range_desc=range_str,
        )

    @staticmethod
    def from_patch(patch_path: str, repo_dir: str) -> DiffResult:
        """Resolve from a .patch file on disk.

        head_commit is current HEAD; base_commit is unknown (empty string).
        """
        repo_dir = str(Path(repo_dir).resolve())
        p = Path(patch_path)
        if not p.exists():
            raise DiffResolverError(f"Patch file not found: {patch_path}")

        diff_text = p.read_text(errors="replace")
        head_commit = _get_head(repo_dir)

        logger.info(
            "diff_resolver.from_patch",
            patch=patch_path,
            diff_lines=diff_text.count("\n"),
        )
        return DiffResult(
            diff_text=diff_text,
            head_commit=head_commit,
            base_commit="",
            commit_range_desc=f"patch:{p.name}",
        )

    @staticmethod
    def from_since_last_scan(repo_dir: str, scan_state: object) -> DiffResult:
        """Resolve all commits since the last scan.

        scan_state must have a .last_scanned_commit attribute.
        If no previous scan exists, falls back to HEAD~10..HEAD.
        """
        repo_dir = str(Path(repo_dir).resolve())
        last_commit = getattr(scan_state, "last_scanned_commit", "") or ""

        head_commit = _get_head(repo_dir)

        if not last_commit:
            logger.warning(
                "diff_resolver.no_last_scan",
                msg="No last scanned commit found, using HEAD~10..HEAD",
            )
            diff_text = _run_git(repo_dir, ["diff", "HEAD~10..HEAD"]) or ""
            base_commit = _resolve_sha(repo_dir, "HEAD~10")
            return DiffResult(
                diff_text=diff_text,
                head_commit=head_commit,
                base_commit=base_commit,
                commit_range_desc="HEAD~10..HEAD (no previous scan)",
            )

        diff_text = _run_git(repo_dir, ["diff", f"{last_commit}..HEAD"]) or ""
        logger.info(
            "diff_resolver.from_since_last_scan",
            last_commit=last_commit[:12],
            diff_lines=diff_text.count("\n"),
        )
        return DiffResult(
            diff_text=diff_text,
            head_commit=head_commit,
            base_commit=last_commit,
            commit_range_desc=f"{last_commit[:12]}..HEAD",
        )

    @staticmethod
    def from_since_date(repo_dir: str, date_str: str) -> DiffResult:
        """Resolve all commits since a date string (e.g. '2026-02-01').

        Finds the oldest commit since that date and diffs oldest→HEAD.
        """
        repo_dir = str(Path(repo_dir).resolve())

        # Get all commits since date (oldest first)
        log_out = _run_git(
            repo_dir, ["log", f"--since={date_str}", "--format=%H", "--reverse"]
        )
        if not log_out or not log_out.strip():
            raise DiffResolverError(
                f"No commits found since {date_str}. "
                "Check the date format (YYYY-MM-DD)."
            )

        commits = [c.strip() for c in log_out.strip().split("\n") if c.strip()]
        oldest_commit = commits[0]
        head_commit = _get_head(repo_dir)

        # Diff from just before oldest commit to HEAD
        base_ref = f"{oldest_commit}~1"
        diff_text = _run_git(repo_dir, ["diff", f"{base_ref}..HEAD"]) or ""
        base_commit = _resolve_sha(repo_dir, base_ref)

        logger.info(
            "diff_resolver.from_since_date",
            date=date_str,
            commits=len(commits),
            diff_lines=diff_text.count("\n"),
        )
        return DiffResult(
            diff_text=diff_text,
            head_commit=head_commit,
            base_commit=base_commit,
            commit_range_desc=f"since:{date_str}",
        )

    @staticmethod
    def from_last_n(repo_dir: str, n: int) -> DiffResult:
        """Resolve the last N commits (HEAD~N..HEAD)."""
        repo_dir = str(Path(repo_dir).resolve())

        if n < 1:
            raise DiffResolverError(f"n must be >= 1, got {n}")

        diff_text = _run_git(repo_dir, ["diff", f"HEAD~{n}..HEAD"]) or ""
        head_commit = _get_head(repo_dir)
        base_commit = _resolve_sha(repo_dir, f"HEAD~{n}")

        logger.info(
            "diff_resolver.from_last_n",
            n=n,
            diff_lines=diff_text.count("\n"),
        )
        return DiffResult(
            diff_text=diff_text,
            head_commit=head_commit,
            base_commit=base_commit,
            commit_range_desc=f"HEAD~{n}..HEAD",
        )
