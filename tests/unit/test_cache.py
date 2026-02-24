"""Tests for crossfire.core.cache — context and intent persistence."""

from __future__ import annotations

import json

import pytest

from crossfire.core.cache import (
    context_cache_path,
    intent_cache_path,
    load_cached_context,
    load_cached_intent,
    save_context_cache,
    save_intent_cache,
)
from crossfire.core.models import IntentProfile, PRContext


@pytest.fixture
def cache_dir(tmp_path):
    return str(tmp_path / "cache")


@pytest.fixture
def sample_context():
    return PRContext(
        repo_name="owner/repo",
        pr_number=42,
        pr_title="Fix auth bypass",
        head_sha="abc123def456",
        base_sha="000111222333",
    )


@pytest.fixture
def sample_intent():
    return IntentProfile(
        repo_purpose="Web application",
        intended_capabilities=["http_input", "database_access"],
        pr_intent="Bug fix",
    )


# ── Path generation ──────────────────────────────────────────────────


class TestCachePaths:
    def test_context_cache_path_format(self, cache_dir):
        path = context_cache_path(cache_dir, 42, "abc123")
        assert path.name == "context-42-abc123.json"

    def test_intent_cache_path_format(self, cache_dir):
        path = intent_cache_path(cache_dir, "def456")
        assert path.name == "intent-def456.json"

    def test_creates_directory(self, cache_dir):
        path = context_cache_path(cache_dir, 1, "sha")
        assert path.parent.exists()


# ── Context round-trip ───────────────────────────────────────────────


class TestContextCache:
    def test_miss_returns_none(self, cache_dir):
        result = load_cached_context(cache_dir, 42, "nonexistent")
        assert result is None

    def test_save_and_load(self, cache_dir, sample_context):
        save_context_cache(cache_dir, 42, "abc123def456", sample_context)
        loaded = load_cached_context(cache_dir, 42, "abc123def456")

        assert loaded is not None
        assert loaded.repo_name == "owner/repo"
        assert loaded.pr_number == 42
        assert loaded.pr_title == "Fix auth bypass"
        assert loaded.head_sha == "abc123def456"
        assert loaded.base_sha == "000111222333"

    def test_different_sha_misses(self, cache_dir, sample_context):
        save_context_cache(cache_dir, 42, "abc123def456", sample_context)
        result = load_cached_context(cache_dir, 42, "different_sha")
        assert result is None

    def test_different_pr_misses(self, cache_dir, sample_context):
        save_context_cache(cache_dir, 42, "abc123def456", sample_context)
        result = load_cached_context(cache_dir, 99, "abc123def456")
        assert result is None

    def test_corrupt_json_returns_none(self, cache_dir):
        path = context_cache_path(cache_dir, 42, "corrupt")
        path.write_text("not valid json {{{", encoding="utf-8")
        result = load_cached_context(cache_dir, 42, "corrupt")
        assert result is None

    def test_wrong_schema_returns_none(self, cache_dir):
        path = context_cache_path(cache_dir, 42, "badschema")
        path.write_text(json.dumps({"not": "a PRContext"}), encoding="utf-8")
        result = load_cached_context(cache_dir, 42, "badschema")
        assert result is None


# ── Intent round-trip ────────────────────────────────────────────────


class TestIntentCache:
    def test_miss_returns_none(self, cache_dir):
        result = load_cached_intent(cache_dir, "nonexistent")
        assert result is None

    def test_save_and_load(self, cache_dir, sample_intent):
        save_intent_cache(cache_dir, "base_sha_123", sample_intent)
        loaded = load_cached_intent(cache_dir, "base_sha_123")

        assert loaded is not None
        assert loaded.repo_purpose == "Web application"
        assert loaded.intended_capabilities == ["http_input", "database_access"]
        assert loaded.pr_intent == "Bug fix"

    def test_different_sha_misses(self, cache_dir, sample_intent):
        save_intent_cache(cache_dir, "base_sha_123", sample_intent)
        result = load_cached_intent(cache_dir, "different_base")
        assert result is None

    def test_corrupt_json_returns_none(self, cache_dir):
        path = intent_cache_path(cache_dir, "corrupt")
        path.write_text("<<<garbage>>>", encoding="utf-8")
        result = load_cached_intent(cache_dir, "corrupt")
        assert result is None

    def test_overwrite_existing(self, cache_dir, sample_intent):
        save_intent_cache(cache_dir, "sha1", sample_intent)

        updated = IntentProfile(
            repo_purpose="CLI tool",
            intended_capabilities=["file_access"],
        )
        save_intent_cache(cache_dir, "sha1", updated)

        loaded = load_cached_intent(cache_dir, "sha1")
        assert loaded is not None
        assert loaded.repo_purpose == "CLI tool"
