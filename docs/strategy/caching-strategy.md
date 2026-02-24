# Caching Strategy — Context & Intent Persistence in GitHub Actions

> Avoid redundant computation across CI runs by caching `PRContext` and `IntentProfile` between GitHub Actions invocations.

---

## Motivation

Every `crossfire analyze-pr` run currently rebuilds context and intent from scratch. Context building alone makes 10+ GitHub API calls (PR metadata, paginated file lists, file contents at both refs, README, repo info, commits, manifest files). On large PRs this takes 10-30 seconds and burns API rate limit. Since most re-runs target the same commit, this work is wasted.

---

## What to Cache

| Stage | Cost per run | Changes when... | Cache value |
|-------|-------------|----------------|------------|
| **Context** (`PRContext`) | ~10+ GitHub API calls, fetches all file contents at both base/head | New commits pushed to PR | **HIGH** — biggest time/API savings |
| **Intent** (`IntentProfile`) | Pure regex/heuristics, no API calls | Base branch changes (repo structure, README, deps) | **LOW** — fast to compute, but free to cache |
| **Skills** | subprocess (git blame/grep, os.walk) | File contents change | **MEDIUM** — but skipped in `analyze-pr` mode (no local checkout) |

---

## Cache Layout

```
.crossfire/cache/
  context-{pr_number}-{head_sha}.json    # invalidated by new push
  intent-{base_sha}.json                 # invalidated when main moves
```

### Key Strategy

- **Context**: `context-{pr_number}-{head_sha}` — new commits to the PR generate a new head SHA, so stale cache is never used.
- **Intent**: `intent-{base_sha}` — when main moves forward (new merges), the repo structure/README/deps may change, invalidating the cache.

---

## Serialization

Both `PRContext` and `IntentProfile` are Pydantic v2 `BaseModel` subclasses. Serialization is trivial:

```python
# Write
Path(cache_path).write_text(context.model_dump_json(indent=2))

# Read
context = PRContext.model_validate_json(Path(cache_path).read_text())
```

No extra serialization code needed.

---

## GitHub Actions Wiring

Update `.github/workflows/crossfire.yml` to use `actions/cache`:

```yaml
- uses: actions/cache@v4
  with:
    path: .crossfire/cache
    key: crossfire-${{ github.event.pull_request.number }}-${{ github.event.pull_request.head.sha }}
    restore-keys: |
      crossfire-${{ github.event.pull_request.number }}-
      crossfire-
```

### Cache Hit Behavior

1. **Exact hit** — re-runs of the same commit (retries, re-triggered workflows): both context and intent cached.
2. **PR prefix hit** — same PR with new commits: intent cache is still valid if base hasn't moved; context is rebuilt.
3. **Broad prefix hit** — intent cache from any recent PR on the same base branch.

---

## Code Changes Required

### 1. New module: `crossfire/core/cache.py`

Responsibilities:
- Cache key generation from PR number, head SHA, base SHA.
- Write: serialize `PRContext` / `IntentProfile` to JSON files.
- Read: deserialize and validate from JSON files.
- Cache miss returns `None`.

### 2. New CLI option: `--cache-dir`

- Default: `.crossfire/cache`
- Passed through to the orchestrator.
- Also settable via `CROSSFIRE_CACHE_DIR` environment variable.

### 3. Orchestrator changes (`core/orchestrator.py`)

In `analyze_pr()`:
- Before building context, check `cache_dir/context-{pr}-{head_sha}.json`.
- If hit, skip all GitHub API calls.
- After building context (on miss), write to cache.

In `_run_pipeline()`:
- Before inferring intent, check `cache_dir/intent-{base_sha}.json`.
- If hit, skip heuristic inference.
- After inference (on miss), write to cache.

### 4. Workflow update (`.github/workflows/crossfire.yml`)

- Add `actions/cache@v4` step before the `crossfire analyze-pr` step.
- Pass `--cache-dir .crossfire/cache` to the CLI.

---

## Cache Staleness Validation

See [Strategy 1.b — Cache Staleness Validation](./1b-cache-staleness-validation.md) for the hybrid heuristic + LLM approach to validating cached data before use.

---

## Future Extensions

- **Skills caching** for `analyze-diff` mode (keyed on `{head_sha}` + changed file list hash).
- **Cache TTL / max size** — prune old entries to avoid unbounded growth.
- **Cache warming** — pre-compute intent on merge to main so PR runs always get a hit.
