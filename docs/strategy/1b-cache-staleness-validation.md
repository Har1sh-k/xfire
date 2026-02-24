# Strategy 1.b — Cache Staleness Validation (Hybrid Heuristic + LLM)

> Depends on: [Strategy 1 — Caching](./caching-strategy.md)

---

## Problem

When a cache hit occurs, the cached context or intent may be stale. Blindly trusting stale data feeds bad input into expensive review agents. Blindly rebuilding wastes the cache. We need a fast, cheap way to decide.

---

## Approach: Deterministic Check First, LLM Only When Ambiguous

Before burning an LLM call, do a fast deterministic check:

- **Context**: does the cached `head_sha` match the current `head_sha`? If yes, skip validation entirely. If no, the cache is stale — rebuild (no LLM needed).
- **Intent**: has the base SHA moved? If no, the cache is valid. If yes, use a small model (Haiku / Flash / Codex-mini) to check whether the structural changes actually affect intent.

This way the LLM validation only fires in the ambiguous case (base moved but maybe nothing material changed), and the common cases (exact match / obvious miss) are handled for free.

---

## Validation Flow

```
Cache hit?
│
├─ Context: compare cached head_sha vs current head_sha
│   ├─ Match    → use cached context (zero cost)
│   └─ Mismatch → full rebuild (no LLM needed, staleness is binary)
│
└─ Intent: compare cached base_sha vs current base_sha
    ├─ Match    → use cached intent (zero cost)
    └─ Mismatch → small model evaluates if changes affect intent
        ├─ Still valid → use cached intent
        └─ Stale       → full rebuild
```

---

## Why This Split

| Data | Staleness signal | Decision method | Cost |
|------|-----------------|----------------|------|
| **Context** | `head_sha` changed | String comparison (binary: same commit or not) | Free |
| **Intent** | `base_sha` changed | Ambiguous — base moved but repo purpose/deps/structure may be unchanged | LLM call (~$0.001-0.005) |

Context staleness is binary — same commit or not — so a string comparison is sufficient.

Intent staleness is ambiguous — the base branch may have moved but the repo's purpose, dependencies, and structure may be unchanged. This is where a cheap LLM call adds real value: it can read the diff between the old and new base and determine whether anything material changed (new deps, restructured directories, updated README) before discarding a perfectly good cached intent.

---

## Small Model Intent Validation

When the base SHA has moved, the validator sends a prompt to a small model with:

1. The cached `IntentProfile` (JSON)
2. The diff between old base and new base (focused on: README, dependency manifests, directory structure, config files)

The model answers one question: **"Did anything change that would affect the repo's purpose, capabilities, trust boundaries, or security controls?"**

- If no → use cached intent
- If yes → full rebuild

### Model Selection

| Provider | Model | Cost per validation | Latency |
|----------|-------|-------------------|---------|
| Anthropic | Haiku | ~$0.001 | ~1s |
| Google | Flash | ~$0.001 | ~1s |
| OpenAI | Codex-mini | ~$0.002 | ~1s |

Any of these is orders of magnitude cheaper than a full review agent call.

---

## Implementation Notes

- Store `head_sha` and `base_sha` as metadata fields inside the cache JSON files (alongside the serialized model data).
- The current `head_sha` and `base_sha` are available from the GitHub Actions event context (`github.event.pull_request.head.sha`, `github.event.pull_request.base.sha`).
- The small model validation should be a standalone function in `crossfire/core/cache.py`, not wired into the agent adapters.
- The LLM validation only targets **intent**, not context. This avoids paying for LLM calls in cases where a string comparison gives you the answer.
