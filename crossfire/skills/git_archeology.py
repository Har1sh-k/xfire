"""Git archeology skill — understand code history and ownership."""

from __future__ import annotations

import subprocess
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from crossfire.skills.base import BaseSkill, SkillResult


class CommitInfo(BaseModel):
    """Information about a git commit."""

    sha: str
    author: str
    date: str
    message: str


class BlameInfo(BaseModel):
    """Git blame summary for a file."""

    file_path: str
    authors: dict[str, int] = Field(default_factory=dict)  # author -> line count
    total_lines: int = 0


class CodeAge(BaseModel):
    """Age information for a code region."""

    file_path: str
    start_line: int
    end_line: int
    oldest_commit_date: str = ""
    newest_commit_date: str = ""
    is_recently_changed: bool = False


class ContributorInfo(BaseModel):
    """Contributor information for a file."""

    name: str
    lines: int
    percentage: float


def _run_git(args: list[str], repo_dir: str) -> str | None:
    """Run a git command and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=repo_dir,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=15,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


class GitArcheologySkill(BaseSkill):
    """Understand code history and ownership patterns."""

    name = "git_archeology"

    def execute(self, repo_dir: str, changed_files: list[str], **kwargs: Any) -> SkillResult:
        """Analyze git history for changed files."""
        blame_infos: list[dict] = []
        histories: list[dict] = []
        security_commits: list[dict] = []

        for file_path in changed_files:
            blame = self.get_blame(file_path, repo_dir)
            if blame:
                blame_infos.append(blame.model_dump())

            history = self.get_file_history(file_path, repo_dir, limit=10)
            histories.extend([h.model_dump() for h in history])

        sec_commits = self.get_recent_security_commits(repo_dir, days=90)
        security_commits = [c.model_dump() for c in sec_commits]

        parts: list[str] = []
        parts.append(f"Analyzed git history for {len(changed_files)} files")
        if security_commits:
            parts.append(f"Found {len(security_commits)} recent security-related commits")

        return SkillResult(
            skill_name=self.name,
            summary="; ".join(parts),
            details={
                "blame": blame_infos,
                "history": histories,
                "security_commits": security_commits,
            },
        )

    def get_blame(self, file_path: str, repo_dir: str) -> BlameInfo | None:
        """Get git blame summary for a file."""
        output = _run_git(["blame", "--porcelain", file_path], repo_dir)
        if not output:
            return None

        authors: dict[str, int] = {}
        for line in output.split("\n"):
            if line.startswith("author "):
                author = line[7:]
                authors[author] = authors.get(author, 0) + 1

        return BlameInfo(
            file_path=file_path,
            authors=authors,
            total_lines=sum(authors.values()),
        )

    def get_file_history(self, file_path: str, repo_dir: str, limit: int = 20) -> list[CommitInfo]:
        """Get commit history for a file."""
        output = _run_git(
            ["log", f"--max-count={limit}", "--format=%H|%an|%ai|%s", "--follow", "--", file_path],
            repo_dir,
        )
        if not output:
            return []

        commits: list[CommitInfo] = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append(CommitInfo(
                    sha=parts[0],
                    author=parts[1],
                    date=parts[2],
                    message=parts[3],
                ))

        return commits

    def get_recent_security_commits(self, repo_dir: str, days: int = 90) -> list[CommitInfo]:
        """Find commits with security-related keywords in messages."""
        output = _run_git(
            ["log", f"--since={days} days ago", "--format=%H|%an|%ai|%s",
             "--grep=security\\|vuln\\|CVE\\|auth\\|fix.*inject\\|XSS\\|CSRF",
             "--regexp-ignore-case", "-i"],
            repo_dir,
        )
        if not output:
            return []

        commits: list[CommitInfo] = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append(CommitInfo(
                    sha=parts[0],
                    author=parts[1],
                    date=parts[2],
                    message=parts[3],
                ))

        return commits

    def get_code_age(self, file_path: str, line_range: tuple[int, int], repo_dir: str) -> CodeAge:
        """Determine how old code in a specific line range is."""
        output = _run_git(
            ["blame", "-L", f"{line_range[0]},{line_range[1]}", "--porcelain", file_path],
            repo_dir,
        )

        dates: list[str] = []
        if output:
            for line in output.split("\n"):
                if line.startswith("committer-time "):
                    timestamp = int(line.split(" ")[1])
                    dates.append(datetime.fromtimestamp(timestamp).isoformat())

        oldest = min(dates) if dates else ""
        newest = max(dates) if dates else ""

        # Recently changed = within last 30 days
        is_recent = False
        if newest:
            try:
                newest_dt = datetime.fromisoformat(newest)
                is_recent = (datetime.now() - newest_dt).days < 30
            except ValueError:
                pass

        return CodeAge(
            file_path=file_path,
            start_line=line_range[0],
            end_line=line_range[1],
            oldest_commit_date=oldest,
            newest_commit_date=newest,
            is_recently_changed=is_recent,
        )

    def get_contributors(self, file_path: str, repo_dir: str) -> list[ContributorInfo]:
        """Get contributor breakdown for a file."""
        blame = self.get_blame(file_path, repo_dir)
        if not blame or blame.total_lines == 0:
            return []

        return [
            ContributorInfo(
                name=author,
                lines=count,
                percentage=round(count / blame.total_lines * 100, 1),
            )
            for author, count in sorted(blame.authors.items(), key=lambda x: -x[1])
        ]
