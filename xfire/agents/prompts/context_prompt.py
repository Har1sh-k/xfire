"""Context-aware prompt generation using the fast model.

Provides two functions:
  check_intent_changed()       — fast model check for baseline rebuild
  build_context_system_prompt() — repo-specific system prompt adaptation
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from xfire.agents.fast_model import FastModel
    from xfire.core.baseline import Baseline

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Intent-change detection
# ---------------------------------------------------------------------------

INTENT_CHANGE_SYSTEM = """You are a security model analyst checking whether a code diff materially changes a repository's security posture.

You will be given:
1. The repository's current baseline context (purpose, capabilities, trust boundaries, controls)
2. A code diff

Your job is ONLY to check whether the diff materially changes:
1. The repo's fundamental purpose or threat model?
2. Core trust boundaries (new external inputs, new auth layers, new network-facing surfaces)?
3. Major security controls added or removed (new sandbox, removed auth check, new encryption layer)?
4. Significant new capabilities not captured in the baseline (new code execution path, new external API access)?

Respond with ONLY valid JSON. No preamble, no explanation outside the JSON.

{"material_change": true/false, "reason": "one sentence explanation"}"""


async def check_intent_changed(
    diff_text: str,
    baseline: Baseline,
    fast_model: FastModel,
) -> bool:
    """Check if diff materially changes the repo's security model.

    Returns True if baseline needs rebuild (material change detected).
    Returns False on model errors (safe default — skip rebuild).
    """
    from xfire.agents.fast_model import FastModelUnavailableError

    # Only send first 3000 chars of diff to keep it cheap
    diff_excerpt = diff_text[:3000]
    if len(diff_text) > 3000:
        diff_excerpt += f"\n... (truncated, {len(diff_text)} total chars)"

    prompt = f"""## Repository Baseline Context

{baseline.context_md}

## Code Diff to Evaluate

```diff
{diff_excerpt}
```

Does this diff materially change the repository's security model?
Respond with JSON only: {{"material_change": true/false, "reason": "..."}}"""

    try:
        response = await fast_model.call(prompt, system=INTENT_CHANGE_SYSTEM)
        data = _extract_json(response)
        material_change = bool(data.get("material_change", False))
        reason = data.get("reason", "")
        logger.info(
            "intent_change_check",
            material_change=material_change,
            reason=reason[:100],
        )
        return material_change
    except FastModelUnavailableError as e:
        logger.warning(
            "intent_change_check.unavailable",
            error=str(e),
            msg="Fast model unavailable — skipping intent check (no rebuild)",
        )
        return False
    except Exception as e:
        logger.warning(
            "intent_change_check.error",
            error=str(e),
            msg="Error during intent check — skipping rebuild",
        )
        return False


# ---------------------------------------------------------------------------
# Context-aware system prompt generation
# ---------------------------------------------------------------------------

# The threat-model audit template that gets adapted per-repo
AUDIT_TEMPLATE = """You are an elite security engineer performing a thorough code review of a pull request.

You are NOT a static analysis tool. You do NOT pattern-match. You READ code, UNDERSTAND architecture, TRACE data flows, and REASON about security implications. You think like an attacker who has read the entire codebase.

## Repository-Specific Context

{repo_context}

## Your Review Methodology

1. UNDERSTAND THE CONTEXT
   - This repo's purpose: {repo_purpose}
   - Core capabilities: {capabilities}
   - Trust boundaries: {trust_boundaries}
   - Security controls already in place: {controls}

2. READ THE DIFF CAREFULLY
   - What code was added, removed, and modified?
   - What security-relevant behavior changed?
   - Were any security controls added, removed, or weakened?

3. TRACE DATA FLOWS
   - Can untrusted input reach any dangerous operation?
   - Follow data through function calls, across files, through transformations
   - Check if validation/sanitization exists

4. CHECK FOR MISSING CONTROLS
   - Is there auth where there should be?
   - Is there input validation where there should be?
   - Are there audit logs for sensitive operations?

5. ASSESS DANGEROUS BUGS
   - Race conditions, missing error handling, retry storms
   - Destructive operations without safeguards
   - Resource exhaustion paths

## CRITICAL: Purpose-Aware Thinking

DO NOT flag intended capabilities as vulnerabilities. The capabilities listed above are INTENDED.

ONLY flag when: exposure + missing controls + viable abuse path ALL exist.

Only report concrete, exploitable security vulnerabilities or dangerous bugs with a viable attack/failure path."""

CONTEXT_PROMPT_SYSTEM = """You are generating a highly specific security review prompt tailored to a specific repository.

You will be given:
1. The repository's baseline context (purpose, capabilities, trust boundaries, controls)
2. A summary of what the diff changes

Your job is to produce a CONCISE security review system prompt (300-500 words) that:
- Names the specific repo and its purpose
- Lists the specific capabilities and trust boundaries relevant to THIS repo
- Identifies which security areas are MOST relevant given the diff
- Notes which capabilities are INTENDED (so reviewers don't false-positive on them)
- Is a complete, standalone system prompt for a security reviewer

Output ONLY the system prompt text. No preamble, no JSON, no explanation."""


async def build_context_system_prompt(
    baseline: Baseline,
    diff_summary: str,
    fast_model: FastModel,
) -> str:
    """Build a repo-specific system prompt for security review.

    Adapts the AUDIT_TEMPLATE to this repo using the fast model.
    Falls back to REVIEW_SYSTEM_PROMPT from review_prompt.py if fast model fails.
    """
    from xfire.agents.fast_model import FastModelUnavailableError
    from xfire.agents.prompts.review_prompt import REVIEW_SYSTEM_PROMPT

    intent = baseline.intent

    # Build quick summary from baseline
    capabilities_str = ", ".join(intent.intended_capabilities[:8]) or "general-purpose"
    trust_boundaries_str = "; ".join(
        f"{tb.name}: {tb.description[:60]}" for tb in intent.trust_boundaries[:3]
    ) or "standard"
    controls_str = ", ".join(
        sc.control_type for sc in intent.security_controls_detected[:6]
    ) or "none detected"

    prompt = f"""## Repository Baseline Context

{baseline.context_md}

## Diff Summary

{diff_summary[:1000]}

## Task

Generate a focused security review system prompt (300-500 words) tailored to this specific repository.

Include:
- The repo's exact purpose: {intent.repo_purpose[:200]}
- Key capabilities (flag these as INTENDED, not vulnerabilities): {capabilities_str}
- Trust boundaries to watch: {trust_boundaries_str}
- Existing controls: {controls_str}
- Specific threat categories most relevant to this diff

Output ONLY the system prompt text. No preamble."""

    try:
        response = await fast_model.call(prompt, system=CONTEXT_PROMPT_SYSTEM)
        response = response.strip()
        if len(response) < 100:
            logger.warning(
                "context_prompt.too_short",
                length=len(response),
                msg="Fast model returned very short prompt, using fallback",
            )
            return REVIEW_SYSTEM_PROMPT
        logger.info(
            "context_prompt.generated",
            length=len(response),
        )
        return response
    except FastModelUnavailableError as e:
        logger.warning(
            "context_prompt.unavailable",
            error=str(e),
            msg="Fast model unavailable — using default REVIEW_SYSTEM_PROMPT",
        )
        return REVIEW_SYSTEM_PROMPT
    except Exception as e:
        logger.warning(
            "context_prompt.error",
            error=str(e),
            msg="Error generating context prompt — using default",
        )
        return REVIEW_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict:
    """Extract JSON object from text, handling code fences and preamble."""
    text = text.strip()

    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Code block
    block = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if block:
        try:
            return json.loads(block.group(1))
        except json.JSONDecodeError:
            pass

    # First { to last }
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from response: {text[:200]}")
