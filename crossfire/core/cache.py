"""Cache layer for context and intent persistence across CI runs.

Stores PRContext and IntentProfile as JSON files keyed by SHA so
that subsequent GitHub Actions runs can skip expensive API calls
and heuristic computation when nothing has changed.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from crossfire.core.models import IntentProfile, PRContext

logger = structlog.get_logger()


def _ensure_dir(cache_dir: str) -> Path:
    """Ensure the cache directory exists and return it as a Path."""
    p = Path(cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Context cache (keyed on PR number + head SHA) ────────────────────


def context_cache_path(cache_dir: str, pr_number: int, head_sha: str) -> Path:
    """Return the path for a cached PRContext."""
    return _ensure_dir(cache_dir) / f"context-{pr_number}-{head_sha}.json"


def load_cached_context(
    cache_dir: str, pr_number: int, head_sha: str,
) -> PRContext | None:
    """Load a cached PRContext, or None on miss / corrupt data."""
    path = context_cache_path(cache_dir, pr_number, head_sha)
    if not path.exists():
        logger.debug("cache.context_miss", pr=pr_number, head_sha=head_sha[:8])
        return None
    try:
        context = PRContext.model_validate_json(path.read_text(encoding="utf-8"))
        logger.info("cache.context_hit", pr=pr_number, head_sha=head_sha[:8])
        return context
    except Exception as e:
        logger.warning("cache.context_corrupt", path=str(path), error=str(e))
        return None


def save_context_cache(
    cache_dir: str, pr_number: int, head_sha: str, context: PRContext,
) -> None:
    """Write a PRContext to the cache."""
    path = context_cache_path(cache_dir, pr_number, head_sha)
    try:
        path.write_text(context.model_dump_json(indent=2), encoding="utf-8")
        logger.info("cache.context_saved", pr=pr_number, head_sha=head_sha[:8])
    except Exception as e:
        logger.warning("cache.context_write_failed", error=str(e))


# ── Intent cache (keyed on base SHA) ─────────────────────────────────


def intent_cache_path(cache_dir: str, base_sha: str) -> Path:
    """Return the path for a cached IntentProfile."""
    return _ensure_dir(cache_dir) / f"intent-{base_sha}.json"


def load_cached_intent(cache_dir: str, base_sha: str) -> IntentProfile | None:
    """Load a cached IntentProfile, or None on miss / corrupt data."""
    path = intent_cache_path(cache_dir, base_sha)
    if not path.exists():
        logger.debug("cache.intent_miss", base_sha=base_sha[:8])
        return None
    try:
        intent = IntentProfile.model_validate_json(path.read_text(encoding="utf-8"))
        logger.info("cache.intent_hit", base_sha=base_sha[:8])
        return intent
    except Exception as e:
        logger.warning("cache.intent_corrupt", path=str(path), error=str(e))
        return None


def save_intent_cache(
    cache_dir: str, base_sha: str, intent: IntentProfile,
) -> None:
    """Write an IntentProfile to the cache."""
    path = intent_cache_path(cache_dir, base_sha)
    try:
        path.write_text(intent.model_dump_json(indent=2), encoding="utf-8")
        logger.info("cache.intent_saved", base_sha=base_sha[:8])
    except Exception as e:
        logger.warning("cache.intent_write_failed", error=str(e))
