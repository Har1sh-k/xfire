"""GitHub PR loader — fetch PR data via GitHub REST API."""

from __future__ import annotations

import asyncio

import structlog

from xfire.config.settings import AnalysisConfig
from xfire.core.models import PRContext

logger = structlog.get_logger()

# Key manifest / config files to fetch from the repo for intent inference
_MANIFEST_FILENAMES = [
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "go.mod",
    "Gemfile",
    "Cargo.toml",
    "setup.py",
    "setup.cfg",
    "pom.xml",
    "composer.json",
]

_CI_PATH_PREFIXES = (".github/workflows/", ".gitlab-ci", "Jenkinsfile")


class GitHubAPIError(Exception):
    """Error from the GitHub API."""


def _handle_github_error(resp: object, context: str) -> None:
    """Raise a descriptive GitHubAPIError for failed responses."""
    import httpx

    if not isinstance(resp, httpx.Response):
        return
    if resp.is_success:
        return
    status = resp.status_code
    if status == 404:
        raise GitHubAPIError(f"{context}: not found (404). Check the repo/PR exists.")
    elif status == 403:
        raise GitHubAPIError(
            f"{context}: permission denied or rate limited (403). Check your token."
        )
    elif status >= 500:
        raise GitHubAPIError(f"{context}: GitHub server error ({status}). Try again later.")
    else:
        raise GitHubAPIError(f"{context}: HTTP {status} — {resp.text[:200]}")


def _build_directory_structure(file_paths: list[str]) -> str:
    """Build a pseudo directory tree from a list of file paths."""
    dirs: set[str] = set()
    for path in file_paths:
        parts = path.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]) + "/")
    # Sort for deterministic output
    all_entries = sorted(dirs | set(file_paths))
    return "\n".join(all_entries[:200])


async def _fetch_all_pr_files(client: object, repo: str, pr_number: int) -> list[dict]:
    """Fetch all PR files with pagination."""
    import httpx

    if not isinstance(client, httpx.AsyncClient):
        return []

    all_files: list[dict] = []
    page = 1
    while True:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files",
            params={"per_page": 100, "page": page},
        )
        if not resp.is_success:
            _handle_github_error(resp, f"Fetching files for {repo}#{pr_number}")
        batch = resp.json()
        if not batch:
            break
        all_files.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return all_files


async def fetch_pr_shas(
    repo: str,
    pr_number: int,
    token: str,
) -> tuple[str, str]:
    """Lightweight fetch of just the head and base SHAs for a PR.

    Returns (head_sha, base_sha).  Costs a single API call.
    """
    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}",
        )
        _handle_github_error(resp, f"Fetching SHAs for {repo}#{pr_number}")
        data = resp.json()
        head_sha = data.get("head", {}).get("sha", "")
        base_sha = data.get("base", {}).get("sha", "")
        return head_sha, base_sha


async def load_pr_context(
    repo: str,
    pr_number: int,
    token: str,
    config: AnalysisConfig,
) -> PRContext:
    """Fetch complete PR context from GitHub REST API via httpx."""
    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # Fetch PR metadata
        pr_resp = await client.get(f"https://api.github.com/repos/{repo}/pulls/{pr_number}")
        if not pr_resp.is_success:
            _handle_github_error(pr_resp, f"Fetching PR {repo}#{pr_number}")
        pr_data = pr_resp.json()

        # Fetch PR files with pagination
        files_data = await _fetch_all_pr_files(client, repo, pr_number)

        # Fetch the diff
        diff_resp = await client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}",
            headers={**headers, "Accept": "application/vnd.github.v3.diff"},
        )
        if not diff_resp.is_success:
            _handle_github_error(diff_resp, f"Fetching diff for {repo}#{pr_number}")
        diff_text = diff_resp.text

        # Parse diff into file contexts
        from xfire.core.context_builder import parse_diff
        files = parse_diff(diff_text)

        # Enrich with full file content from GitHub (parallel)
        base_ref = pr_data.get("base", {}).get("sha", "")
        head_ref = pr_data.get("head", {}).get("sha", "")
        raw_headers = {**headers, "Accept": "application/vnd.github.v3.raw"}

        async def _fetch_file_content(fc):
            """Fetch head and base content for a single file."""
            if not fc.is_deleted:
                resp = await client.get(
                    f"https://api.github.com/repos/{repo}/contents/{fc.path}",
                    params={"ref": head_ref},
                    headers=raw_headers,
                )
                if resp.status_code == 200:
                    fc.content = resp.text

            if not fc.is_new and config.context_depth != "shallow":
                base_fetch_path = fc.old_path if fc.is_renamed and fc.old_path else fc.path
                resp = await client.get(
                    f"https://api.github.com/repos/{repo}/contents/{base_fetch_path}",
                    params={"ref": base_ref},
                    headers=raw_headers,
                )
                if resp.status_code == 200:
                    fc.base_content = resp.text

        await asyncio.gather(*[_fetch_file_content(fc) for fc in files])

        # Fetch README, repo info, and commits in parallel
        readme_task = client.get(
            f"https://api.github.com/repos/{repo}/readme",
            headers=raw_headers,
        )
        repo_task = client.get(f"https://api.github.com/repos/{repo}")
        commits_task = client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}/commits",
            params={"per_page": 50},
        )
        readme_resp, repo_resp, commits_resp = await asyncio.gather(
            readme_task, repo_task, commits_task,
        )

        readme_content = readme_resp.text if readme_resp.status_code == 200 else None
        repo_description = (
            repo_resp.json().get("description", "") if repo_resp.status_code == 200 else ""
        )
        commit_messages = []
        if commits_resp.status_code == 200:
            commit_messages = [
                c.get("commit", {}).get("message", "").split("\n")[0]
                for c in commits_resp.json()
            ]

        # Fetch key manifest files for intent inference (parallel)
        config_files: dict[str, str] = {}

        async def _fetch_manifest(filename: str):
            resp = await client.get(
                f"https://api.github.com/repos/{repo}/contents/{filename}",
                params={"ref": head_ref},
                headers=raw_headers,
            )
            if resp.status_code == 200:
                config_files[filename] = resp.text

        await asyncio.gather(*[_fetch_manifest(f) for f in _MANIFEST_FILENAMES])

        # Also include any config files that are part of the PR changes
        for fc in files:
            if fc.content and fc.path not in config_files:
                lower = fc.path.lower()
                if any(lower.endswith(ext) for ext in (".yml", ".yaml", ".toml", ".cfg", ".ini", ".json")):
                    config_files[fc.path] = fc.content

        # Separate CI config files
        ci_config_files: dict[str, str] = {}
        for path, content in config_files.items():
            if any(path.startswith(prefix) for prefix in _CI_PATH_PREFIXES):
                ci_config_files[path] = content

        # Build directory structure from file list
        all_paths = [fd.get("filename", "") for fd in files_data if fd.get("filename")]
        directory_structure = _build_directory_structure(all_paths)

        # Build context
        labels = [label.get("name", "") for label in pr_data.get("labels", [])]

        return PRContext(
            repo_name=repo,
            pr_number=pr_number,
            pr_title=pr_data.get("title", ""),
            pr_description=pr_data.get("body", "") or "",
            author=pr_data.get("user", {}).get("login", ""),
            base_branch=pr_data.get("base", {}).get("ref", "main"),
            head_branch=pr_data.get("head", {}).get("ref", ""),
            head_sha=head_ref,
            base_sha=base_ref,
            files=files,
            commit_messages=commit_messages,
            labels=labels,
            readme_content=readme_content,
            repo_description=repo_description,
            config_files=config_files,
            ci_config_files=ci_config_files,
            directory_structure=directory_structure,
        )
