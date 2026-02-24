"""GitHub comment poster — post review comments to PRs."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


async def post_review_comment(
    repo: str,
    pr_number: int,
    token: str,
    body: str,
) -> bool:
    """Post a review comment on a GitHub PR.

    Returns True if successful.
    """
    import httpx

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        # Check for existing CrossFire comments to update instead of creating new
        comments_resp = await client.get(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
            params={"per_page": 100},
        )

        existing_comment_id = None
        if comments_resp.status_code == 200:
            for comment in comments_resp.json():
                if "CrossFire Security Review" in comment.get("body", ""):
                    existing_comment_id = comment["id"]
                    break

        if existing_comment_id:
            # Update existing comment
            resp = await client.patch(
                f"https://api.github.com/repos/{repo}/issues/comments/{existing_comment_id}",
                json={"body": body},
            )
        else:
            # Create new comment
            resp = await client.post(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                json={"body": body},
            )

        if resp.status_code in (200, 201):
            logger.info("github.comment_posted", repo=repo, pr=pr_number)
            return True
        else:
            logger.error(
                "github.comment_failed",
                repo=repo,
                pr=pr_number,
                status=resp.status_code,
                body=resp.text[:500],
            )
            return False
