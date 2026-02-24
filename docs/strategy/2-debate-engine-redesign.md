# Strategy 2 — Debate Engine Redesign

> Replace rotation-based role assignment with evidence-driven roles, structured debate rounds, budget caps, and degraded-mode fallbacks.

---

## Pre-Debate: Finding Filter (Two Layers)

Code review agents should not report architectural design flaws or intended capabilities as vulnerabilities.

**Layer 1 — Prompt-level:** The review system prompt explicitly instructs agents: *"Do NOT report architectural design flaws (missing rate limiters, missing logging, design patterns), and do NOT flag intended capabilities as vulnerabilities. Only report concrete, exploitable security vulnerabilities or dangerous bugs."*

**Layer 2 — Synthesizer post-filter:** After reviews, before debate routing, the synthesizer drops findings that match:
- Categories like missing rate limiters, design pattern issues, or logging gaps
- Findings where `purpose_aware_assessment.is_intended_capability == True` and `isolation_controls_present == True`

Both layers together: the prompt reduces noise at the source, and the synthesizer catches anything that slips through.

---

## Debate Budget

Not all PRs deserve the same debate investment. A PR that changes 3 lines in a README should not trigger 6 rounds of LLM debate on a low-confidence finding.

### Budget Formula

```
total_debate_rounds_allowed = min(findings_needing_debate * 2, budget_cap)
```

### Budget Cap by PR Size

| Changed lines | Budget cap (rounds) |
|--------------|-------------------|
| 1-20 | 2 |
| 21-100 | 6 |
| 101-500 | 12 |
| 500+ | 20 |

### Budget Enforcement

1. Sort findings needing debate by `(severity_order, confidence)` descending.
2. Debate from the top of the list.
3. Each debate consumes 1 round (if defense agrees) or 2 rounds (if round 2 is needed).
4. When the budget reaches zero, stop debating.
5. Remaining undebated findings are auto-marked as `Unclear` with `debate_summary: "Skipped: debate budget exhausted"`.

This keeps costs predictable and forces the system to spend tokens where they matter most.

---

## Severity Resolution (No Debate)

When multiple agents find the same vulnerability but disagree on severity, this does not warrant a full prosecution/defense/judge debate. The developer's action is the same regardless of whether a SQL injection is rated Critical or High: fix it.

### Merge Rule

- If any agent rated it **Critical** → use Critical (someone saw a concrete exploit path — preserve that signal).
- Otherwise → take the **median** severity across agents.

This eliminates the "severity debate" row entirely and replaces it with a deterministic merge rule. Zero LLM cost.

---

## Role Assignment (Evidence-Driven)

Roles are determined by who found or missed the finding, not by rotation or static config.

### Prosecutor

Always the agent that **proposed/found** the vulnerability. If multiple agents found it and a debate is needed (e.g., silent dissent case), pick the one that rated it highest severity.

### Defense

The agent that **missed** the vulnerability (did not report it). When multiple agents missed it, pick by preference order:

```
Defense preference: codex > claude > gemini
```

### Judge

The remaining agent after prosecutor and defense are assigned.

```
Judge preference: codex > gemini > claude
```

### Preference Ordering — v1 vs v2

The static preference ordering (codex > claude > gemini) is configurable in `settings.debate.role_preferences` so users can reorder without code changes.

**v2 evolution:** Replace static preferences with historical precision per finding category. Pick the defense/judge role based on which agent has the highest accuracy on this type of finding. This requires instrumented feedback loops (was this finding actually a true positive?) that do not exist yet. Noted as a future extension.

---

## Silent Dissent Check

**Problem:** When 2 agents find a vuln and 1 misses it, auto-confirming without hearing the dissent is risky. The missing agent may have explicitly analyzed the same code and concluded it was safe (intended behavior, mitigated, etc.).

**Check:** Before auto-confirming, scan the missing agent's `AgentReview` for:
1. Any `Finding` with status `REJECTED` that overlaps on `affected_files` and `line_ranges` with the confirmed finding.
2. Mentions of the same file paths in its `overall_risk_assessment` text.

If either matches → the agent saw the code and disagreed. Route to a **quick 1-round validity debate** (finder prosecutes, dissenting agent defends, third agent judges).

If neither matches → the agent did not analyze that code area. Auto-confirm is safe.

This is a deterministic check in the synthesizer (~10 lines), not an LLM call.

---

## Debate Routing Table

### 3-Agent Mode

| Found by | Severity agreement | Silent dissent? | Action |
|----------|-------------------|----------------|--------|
| All 3 | Agree | — | Auto-confirm |
| All 3 | Disagree | — | Auto-confirm at merged severity (highest-if-Critical, else median) |
| 2 of 3 | Agree | No | Auto-confirm |
| 2 of 3 | Agree | Yes | Quick 1-round validity debate |
| 2 of 3 | Disagree | No | Auto-confirm at merged severity |
| 2 of 3 | Disagree | Yes | Quick 1-round validity debate, severity by merge rule |
| 1 of 3 | — | — | Full validity debate (1-2 rounds) |

### 2-Agent Mode (One Agent Failed/Timed Out)

| Found by | Severity agreement | Action |
|----------|-------------------|--------|
| Both | Agree | Auto-confirm |
| Both | Disagree | Auto-confirm at merged severity (highest-if-Critical, else median) |
| 1 of 2 | — | 1-round debate: finder prosecutes, misser defends, **no judge**. If defense concedes → Confirmed. If defense disagrees → Unclear (no tiebreaker) |

### 1-Agent Mode (Two Agents Failed)

| Found by | Action |
|----------|--------|
| 1 | Mark as Unclear (insufficient corroboration) |

All rows are subject to the debate budget. If the budget is exhausted, any row that says "debate" becomes `Unclear (budget exhausted)` instead.

---

## Debate Flow (Min 1 Round, Max 2 Rounds)

### Round 1

```
Prosecutor argues case (validity of the finding)
    │
    ▼
Defense responds (point-by-point counter-argument)
    │
    ▼
Does defense agree with prosecution?
    │
    ├─ YES → Judge issues verdict immediately
    │         (1 round consumed from budget)
    │
    └─ NO  → proceed to Round 2
```

### Round 2 (Judge-Led)

When defense disagrees, the judge drives the second round instead of letting prosecutor and defense go back and forth. This prevents circular arguments and keeps the round focused.

```
Judge identifies the specific point of disagreement
    │
    ▼
Judge asks targeted clarifying questions to BOTH sides
    │
    ▼
Prosecutor responds to judge's questions  ─┐
Defense responds to judge's questions      ─┤ (can run in parallel)
    │                                       │
    ▼                                       │
Judge makes final ruling  ◄────────────────┘
    (2 rounds consumed from budget)
```

### 2-Agent Debate (No Judge Available)

```
Prosecutor argues case
    │
    ▼
Defense responds
    │
    ├─ Defense concedes → Confirmed
    └─ Defense disagrees → Unclear (no tiebreaker, flag for human review)
```

No round 2 in 2-agent mode. There is no judge to drive it.

---

## Consensus After Debate

After the judge (or 2-agent resolution) produces a verdict:

1. **Evidence quality scoring** remains unchanged (file:line citations, code snippets, explanations).
2. **Purpose-aware override** remains: if prosecutor mentions an intended capability and defense cites controls, downgrade one level.
3. **Minimum evidence threshold** remains: Confirmed requires 2+ strong evidence items from prosecution.
4. **Final severity** comes from the judge's ruling (in debated cases) or the merge rule (in auto-confirmed cases).

---

## Summary of Changes from Current Architecture

| Aspect | Current | New |
|--------|---------|-----|
| Role assignment | Rotation or fixed config | Evidence-driven (who found/missed) |
| Debate trigger | Severity + confidence thresholds | Agent agreement + silent dissent check |
| Severity disputes | Full debate | Deterministic merge rule (no LLM cost) |
| Rebuttal | Prosecutor-only, asymmetric | Replaced by judge-led Round 2 |
| Round 2 trigger | Config flag (`enable_rebuttal`) | Defense disagrees with prosecution |
| Round 2 format | Prosecutor responds to defense | Judge asks both sides clarifying questions |
| Max rounds | 1 + optional rebuttal | Min 1, max 2 (structured, budget-capped) |
| Arch/design flaws | Reported and debated | Filtered before debate (prompt + synthesizer) |
| All-agents-agree | Still debated | Auto-confirmed, debate skipped |
| Budget | Unlimited (debate all tagged findings) | Capped by PR size, highest severity first |
| 2-agent fallback | Not handled | 1-round debate, no judge, disagree → Unclear |
| 1-agent fallback | Not handled | Unclear (insufficient corroboration) |
| Agent preference | N/A | Static v1 (configurable), data-driven v2 (future) |
| Silent dissent | Not checked | Synthesizer checks for explicit disagreement before auto-confirm |
