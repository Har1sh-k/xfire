"""Tests for the GitHub comment poster — mock httpx for posting reviews."""

import pytest

from xfire.integrations.github.comment_poster import post_review_comment


class TestPostReviewComment:
    @pytest.mark.asyncio
    async def test_new_comment(self, respx_mock):
        """Posts a new comment when no existing CrossFire comment found."""
        import httpx

        # No existing comments
        respx_mock.get("https://api.github.com/repos/test/repo/issues/1/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        # Create new comment
        respx_mock.post("https://api.github.com/repos/test/repo/issues/1/comments").mock(
            return_value=httpx.Response(201, json={"id": 42})
        )

        result = await post_review_comment("test/repo", 1, "token", "review body")
        assert result is True

    @pytest.mark.asyncio
    async def test_update_existing(self, respx_mock):
        """Updates an existing CrossFire comment."""
        import httpx

        # Existing CrossFire comment
        respx_mock.get("https://api.github.com/repos/test/repo/issues/1/comments").mock(
            return_value=httpx.Response(200, json=[
                {"id": 99, "body": "# CrossFire Security Review\nOld results"},
            ])
        )
        # Update existing comment
        respx_mock.patch("https://api.github.com/repos/test/repo/issues/comments/99").mock(
            return_value=httpx.Response(200, json={"id": 99})
        )

        result = await post_review_comment("test/repo", 1, "token", "updated body")
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self, respx_mock):
        """Returns False on failed post."""
        import httpx

        respx_mock.get("https://api.github.com/repos/test/repo/issues/1/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx_mock.post("https://api.github.com/repos/test/repo/issues/1/comments").mock(
            return_value=httpx.Response(403, json={"message": "forbidden"})
        )

        result = await post_review_comment("test/repo", 1, "token", "body")
        assert result is False
