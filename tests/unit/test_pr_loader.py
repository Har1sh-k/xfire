"""Tests for the GitHub PR loader — error handling and helpers."""

import pytest

from crossfire.integrations.github.pr_loader import (
    GitHubAPIError,
    _build_directory_structure,
    _handle_github_error,
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
