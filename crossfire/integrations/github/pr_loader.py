"""GitHub PR loader — fetch PR data via GitHub API."""

from __future__ import annotations

import structlog

from crossfire.config.settings import AnalysisConfig
from crossfire.core.models import PRContext

logger = structlog.get_logger()


async def load_pr_context(
    repo: str,
    pr_number: int,
    token: str,
    config: AnalysisConfig,
) -> PRContext:
    """Fetch complete PR context from GitHub API.

    Uses httpx for async HTTP and PyGithub for convenience methods.
    """
    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # Fetch PR metadata
        pr_resp = await client.get(f"https://api.github.com/repos/{repo}/pulls/{pr_number}")
        pr_resp.raise_for_status()
        pr_data = pr_resp.json()

        # Fetch PR files (diff info)
        files_resp = await client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files",
            params={"per_page": 100},
        )
        files_resp.raise_for_status()
        files_data = files_resp.json()

        # Fetch the diff
        diff_resp = await client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}",
            headers={**headers, "Accept": "application/vnd.github.v3.diff"},
        )
        diff_resp.raise_for_status()
        diff_text = diff_resp.text

        # Parse diff into file contexts
        from crossfire.core.context_builder import parse_diff
        files = parse_diff(diff_text)

        # Enrich with full file content from GitHub
        base_ref = pr_data.get("base", {}).get("sha", "")
        head_ref = pr_data.get("head", {}).get("sha", "")

        for fc in files:
            if not fc.is_deleted:
                # Fetch head version
                content_resp = await client.get(
                    f"https://api.github.com/repos/{repo}/contents/{fc.path}",
                    params={"ref": head_ref},
                    headers={**headers, "Accept": "application/vnd.github.v3.raw"},
                )
                if content_resp.status_code == 200:
                    fc.content = content_resp.text

            if not fc.is_new and config.context_depth != "shallow":
                # Fetch base version (use old_path for renames since base lives at the old path)
                base_fetch_path = fc.old_path if fc.is_renamed and fc.old_path else fc.path
                base_resp = await client.get(
                    f"https://api.github.com/repos/{repo}/contents/{base_fetch_path}",
                    params={"ref": base_ref},
                    headers={**headers, "Accept": "application/vnd.github.v3.raw"},
                )
                if base_resp.status_code == 200:
                    fc.base_content = base_resp.text

        # Fetch README
        readme_content = None
        readme_resp = await client.get(
            f"https://api.github.com/repos/{repo}/readme",
            headers={**headers, "Accept": "application/vnd.github.v3.raw"},
        )
        if readme_resp.status_code == 200:
            readme_content = readme_resp.text

        # Fetch repo info
        repo_resp = await client.get(f"https://api.github.com/repos/{repo}")
        repo_description = repo_resp.json().get("description", "") if repo_resp.status_code == 200 else ""

        # Fetch commits on the PR
        commits_resp = await client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_number}/commits",
            params={"per_page": 50},
        )
        commit_messages = []
        if commits_resp.status_code == 200:
            commit_messages = [
                c.get("commit", {}).get("message", "").split("\n")[0]
                for c in commits_resp.json()
            ]

        # Build context
        labels = [l.get("name", "") for l in pr_data.get("labels", [])]

        return PRContext(
            repo_name=repo,
            pr_number=pr_number,
            pr_title=pr_data.get("title", ""),
            pr_description=pr_data.get("body", "") or "",
            author=pr_data.get("user", {}).get("login", ""),
            base_branch=pr_data.get("base", {}).get("ref", "main"),
            head_branch=pr_data.get("head", {}).get("ref", ""),
            files=files,
            commit_messages=commit_messages,
            labels=labels,
            readme_content=readme_content,
            repo_description=repo_description,
        )
