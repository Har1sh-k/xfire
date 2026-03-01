"""Tests for the GitHub PR loader — error handling and helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xfire.integrations.github.pr_loader import (
    GitHubAPIError,
    _build_directory_structure,
    _fetch_all_pr_files,
    _handle_github_error,
    fetch_pr_shas,
)


class TestBuildDirectoryStructure:
    def test_basic_structure(self):
        paths = ["src/main.py", "src/utils/helper.py", "tests/test_main.py"]
        tree = _build_directory_structure(paths)
        assert "src/" in tree
        assert "tests/" in tree
        assert "src/utils/" in tree

    def test_empty_paths(self):
        tree = _build_directory_structure([])
        assert tree == ""

    def test_single_file(self):
        tree = _build_directory_structure(["README.md"])
        assert "README.md" in tree

    def test_deep_nesting(self):
        paths = ["a/b/c/d/e.py"]
        tree = _build_directory_structure(paths)
        assert "a/" in tree
        assert "a/b/" in tree
        assert "a/b/c/" in tree
        assert "a/b/c/d/" in tree

    def test_caps_at_200_lines(self):
        paths = [f"dir{i}/file{j}.py" for i in range(50) for j in range(10)]
        tree = _build_directory_structure(paths)
        lines = tree.split("\n")
        assert len(lines) <= 200


class TestHandleGithubError:
    def test_non_response_does_nothing(self):
        # Should not raise for non-httpx objects
        _handle_github_error("not a response", "test")

    def test_success_does_nothing(self):
        # Create a mock-like object
        class MockResp:
            is_success = True
        _handle_github_error(MockResp(), "test")


class TestGitHubAPIError:
    def test_error_message(self):
        err = GitHubAPIError("Fetching PR: not found (404)")
        assert "not found" in str(err)


# ─── Detailed Error Handling Tests ───────────────────────────────────────────


class TestHandleGithubErrorDetailed:
    def test_404_raises(self):
        import httpx

        resp = httpx.Response(status_code=404, request=httpx.Request("GET", "https://api.github.com/test"))
        with pytest.raises(GitHubAPIError, match="not found"):
            _handle_github_error(resp, "test")

    def test_403_raises(self):
        import httpx

        resp = httpx.Response(status_code=403, request=httpx.Request("GET", "https://api.github.com/test"))
        with pytest.raises(GitHubAPIError, match="permission denied"):
            _handle_github_error(resp, "test")

    def test_500_raises(self):
        import httpx

        resp = httpx.Response(status_code=500, request=httpx.Request("GET", "https://api.github.com/test"))
        with pytest.raises(GitHubAPIError, match="server error"):
            _handle_github_error(resp, "test")


# ─── fetch_pr_shas Tests ────────────────────────────────────────────────────


class TestFetchPrShas:
    @pytest.mark.asyncio
    async def test_returns_shas(self, respx_mock):
        """fetch_pr_shas returns (head_sha, base_sha)."""
        import httpx

        respx_mock.get("https://api.github.com/repos/test/repo/pulls/1").mock(
            return_value=httpx.Response(200, json={
                "head": {"sha": "abc123"},
                "base": {"sha": "def456"},
            })
        )
        head, base = await fetch_pr_shas("test/repo", 1, "token")
        assert head == "abc123"
        assert base == "def456"

    @pytest.mark.asyncio
    async def test_404_raises(self, respx_mock):
        """fetch_pr_shas raises on 404."""
        import httpx

        respx_mock.get("https://api.github.com/repos/test/repo/pulls/999").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with pytest.raises(GitHubAPIError, match="not found"):
            await fetch_pr_shas("test/repo", 999, "token")


# ─── _fetch_all_pr_files Tests ──────────────────────────────────────────────


class TestFetchAllPrFiles:
    @pytest.mark.asyncio
    async def test_single_page(self, respx_mock):
        """Single page of files."""
        import httpx

        respx_mock.get("https://api.github.com/repos/test/repo/pulls/1/files").mock(
            return_value=httpx.Response(200, json=[
                {"filename": "app.py", "status": "modified"},
            ])
        )
        async with httpx.AsyncClient() as client:
            files = await _fetch_all_pr_files(client, "test/repo", 1)
        assert len(files) == 1
        assert files[0]["filename"] == "app.py"
