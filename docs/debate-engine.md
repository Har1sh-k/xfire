# xFire Debate Engine

## Overview

The xFire debate engine is a structured adversarial process that resolves disagreements between AI agents reviewing the same pull request. When agents produce conflicting findings, the engine assigns courtroom-style roles (prosecutor, defense, judge), orchestrates a multi-round debate with cited evidence, and applies a consensus algorithm to produce a final verdict. The system is designed to reduce both false positives and false negatives by forcing agents to defend their positions against active opposition, with every claim grounded in specific code references.

---

## Debate Routing

Before any debate begins, the finding synthesizer determines which findings actually need a debate and which can be resolved automatically. Routing decisions are based on how many of the active agents independently reported the same finding.

| Scenario | Agents Found | Routing | Result |
|---|---|---|---|
| Unanimous agreement | 3/3 (or all) | `auto_confirmed` | Confirmed |
| Supermajority, no dissent | 2/3, silent dissent = false | `auto_confirmed` | Likely |
| Supermajority, with dissent | 2/3, silent dissent = true | `needs_debate` | Debated |
| Single finder | 1/3 | `needs_debate` | Debated |
| Single agent mode | 1/1 | `informational` | Unclear |

Findings tagged `auto_confirmed` skip the debate entirely. Only findings tagged `needs_debate` are sent to the debate engine.

### Cross-Validation Confidence Multipliers

When multiple agents independently discover the same finding, the synthesizer applies a confidence boost before debate routing:

| Agents Confirming | Multiplier | Cap |
|---|---|---|
| 2 | x1.2 | 0.95 |
| 3+ | x1.4 | 0.99 |

These multipliers are applied to the finding's existing confidence score after cluster merging and before debate tagging.

---

## Silent Dissent Detection

A key challenge in multi-agent review is distinguishing between an agent that *missed* a finding (ignorance) and an agent that *saw the same code and decided it was not a problem* (informed disagreement). xFire calls the latter **silent dissent**.

The detection mechanism works as follows:

1. For each finding reported by a subset of agents, identify the agents that did *not* report it (the "missers").
2. For each misser, examine its review output for **rejected findings** (findings the agent explicitly considered and dismissed).
3. If any rejected finding from a misser overlaps with the current finding on both `affected_files` and `line_ranges`, that constitutes silent dissent.
4. As a secondary check, if the misser's `overall_risk_assessment` text mentions any of the finding's affected filenames (matched by basename), that also constitutes silent dissent.

When silent dissent is detected, a 2-of-3 supermajority finding is escalated from `auto_confirmed` to `needs_debate`, ensuring that the dissenting perspective is heard in a structured adversarial process rather than silently overruled.

---

## Debate Budget

The debate engine operates under a budget measured in debate rounds. The budget is determined by the size of the pull request (total changed lines):

| Changed Lines | Max Debate Rounds |
|---|---|
| 1 -- 20 | 2 |
| 21 -- 100 | 6 |
| 101 -- 500 | 12 |
| 500+ | 20 |

```python
_BUDGET_CAPS = [
    (20, 2),
    (100, 6),
    (500, 12),
]
_BUDGET_CAP_DEFAULT = 20
```

### Severity-First Ordering

Findings are sorted by `(severity, confidence)` descending before debate begins, ensuring that the budget is spent on the most important findings first. Severity uses the ordering: Critical (4) > High (3) > Medium (2) > Low (1).

### Budget Exhaustion

When the remaining budget reaches zero, all remaining findings that have not yet been debated are assigned the status **UNCLEAR** with the summary `"Skipped: debate budget exhausted"`. Each debate consumes 1 round (if defense concedes) or 2 rounds (if the debate proceeds to Round 2).

---

## Role Assignment

The debate engine supports three role assignment strategies, controlled by the `debate.role_assignment` configuration field.

### Evidence-Driven Assignment (default: `"evidence"`)

Roles are assigned based on which agents found or missed the finding:

| Role | Assignment Rule |
|---|---|
| **Prosecutor** | The first agent in the finding's `reviewing_agents` list (the finder) |
| **Defense** | The highest-preference agent among those who *missed* the finding, selected from `debate.defense_preference` |
| **Judge** | The remaining agent, selected from `debate.judge_preference` |

If all agents found the finding (no missers), the defense is selected from `defense_preference` excluding the prosecutor. If no remaining agent is available for judge duty, the engine falls back to **2-agent mode**.

### 2-Agent Mode Fallback

When only two agents are available (or no third agent remains for the judge role), the engine runs without a judge:

- If defense concedes, the finding is marked **Confirmed**.
- If defense disagrees, the finding is marked **Unclear** (deferred to human review).

### Alternative Strategies

- **`"fixed"`**: Uses the `debate.fixed_roles` mapping directly (e.g., `prosecutor: claude, defense: codex, judge: gemini`). Falls back to rotation if any assigned agent is unavailable.
- **`"rotate"`**: Round-robin rotation across enabled agents. Legacy fallback; does not consider evidence provenance.

---

## Debate Flow

Each debate follows a structured two-round protocol. Round 2 only occurs if the defense does not concede after Round 1.

### Round 1: Opening Arguments

1. **Prosecution presents**: The prosecutor agent receives the finding summary, collected evidence, PR context, and intent profile. It argues that the finding represents a real security issue, citing specific code locations.
2. **Defense responds**: The defense agent receives everything the prosecutor received plus the prosecution's argument. It argues that the finding is a false positive, citing mitigating controls, intended behavior, or insufficient evidence.

### Defense Concession Check

After Round 1, the engine checks whether the defense conceded. A concession is detected when the defense's `position` field (lowercased, stripped) matches any of: `real_issue`, `confirmed`, `agree`, `concede`.

- **If defense concedes (3-agent mode)**: The judge issues a verdict immediately based on Round 1 arguments. The debate uses 1 round.
- **If defense concedes (2-agent mode)**: The finding is confirmed without a judge. The debate uses 1 round.

### Round 2: Judge-Led Clarification

If the defense does not concede:

1. **Judge asks targeted questions**: The judge reviews both sides' arguments and identifies the key points of disagreement. It formulates specific clarifying questions.
2. **Both sides respond in parallel**: The prosecutor and defense each receive the judge's questions and respond concurrently (via `asyncio.gather`).
3. **Judge issues final ruling**: The judge reviews all arguments (Round 1 + Round 2 responses) along with the full PR context and intent profile, then issues a final ruling with a position, confidence score, and optional severity adjustment.

### Flow Diagram

```
                    +---------------------+
                    |  Prosecution argues  |
                    +---------------------+
                              |
                              v
                    +---------------------+
                    |   Defense responds   |
                    +---------------------+
                              |
                              v
                    +---------------------+
                    | Defense concedes?    |
                    +----------+----------+
                       Yes     |     No
                       |       |       |
              +--------+       |       +--------+
              v                |                v
    +-----------------+        |    +------------------------+
    | Judge verdicts  |        |    | Judge asks questions   |
    | (1 round)       |        |    +------------------------+
    +-----------------+        |                |
              |                |                v
              |                |    +------------------------+
              |                |    | Both sides respond     |
              |                |    | (parallel)             |
              |                |    +------------------------+
              |                |                |
              |                |                v
              |                |    +------------------------+
              |                |    | Judge final ruling     |
              |                |    | (2 rounds)             |
              |                |    +------------------------+
              |                |                |
              v                v                v
                    +---------------------+
                    | Consensus algorithm |
                    +---------------------+
                              |
                              v
                    +---------------------+
                    |    Final verdict    |
                    +---------------------+
```

---

## Consensus Algorithm

After the debate completes, the consensus algorithm in `compute_consensus()` determines the final verdict. The judge's ruling is the primary signal, refined by four cross-checks.

### Primary Signal: Judge Ruling

The judge's `position` field is mapped to an initial outcome:

| Judge Position Contains | Initial Outcome |
|---|---|
| `"confirmed"` | CONFIRMED |
| `"likely"` | LIKELY |
| `"rejected"` | REJECTED |
| anything else | UNCLEAR |

### Cross-Check 1: Unanimity Boost

If the outcome is CONFIRMED and the defense conceded (position in `real_issue`, `confirmed`, `agree`, `concede`), the final confidence is boosted by **+0.15**, capped at **0.99**. Unanimous agreement is the strongest possible signal.

### Cross-Check 2: Weak Evidence Downgrade

If the outcome is CONFIRMED but the prosecution's evidence quality score is below **0.4**, the outcome is downgraded to **LIKELY**. This check is **waived when unanimous** -- a defense concession is treated as stronger than citation count.

### Cross-Check 3: Minimum Evidence Threshold

If the outcome is CONFIRMED but the prosecutor cited fewer than **2** evidence items, the outcome is capped at **LIKELY**. This check is also **waived when unanimous**.

### Cross-Check 4: Purpose-Aware Override

If the intent profile includes intended capabilities, and:
- The prosecution's cited evidence mentions one of those intended capabilities, **and**
- The defense's cited evidence mentions controls (keywords: `control`, `sandbox`, `validation`)

Then the outcome is downgraded by one level:
- CONFIRMED becomes LIKELY
- LIKELY becomes UNCLEAR

This prevents flagging behavior that is both intended by the repository's purpose and protected by security controls.

### Severity Handling

The judge may adjust severity in its structured response (via the `final_severity` field). If the judge provides a valid severity value, it overrides the original finding severity in the debate record.

---

## Evidence Quality Scoring

Each agent's argument is scored for evidence quality using a formula that rewards specificity:

| Component | Score |
|---|---|
| Baseline (having an argument) | +0.20 |
| File + line range citation | +0.25 per citation |
| Code snippet included | +0.15 per citation |
| Explanation provided | +0.10 per citation |

The total is capped at **1.0**.

### Quality Categories

| Category | Score Range |
|---|---|
| **Strong** | >= 0.70 |
| **Moderate** | >= 0.40 |
| **Weak** | < 0.40 |

```python
def _evidence_quality_score(argument: AgentArgument) -> float:
    score = 0.2  # baseline for having an argument at all
    for citation in argument.cited_evidence:
        if citation.file_path and citation.line_range:
            score += 0.25  # specific file:line citation
        if citation.code_snippet:
            score += 0.15  # includes code
        if citation.explanation:
            score += 0.1   # explains why
    return min(score, 1.0)
```

Evidence quality is reported in the debate record as a string (e.g., `"Prosecution: strong, Defense: moderate"`) and is used by the consensus algorithm to gate verdict confidence.

---

## Final Verdicts

Every debated finding receives one of four final verdicts:

| Verdict | Meaning |
|---|---|
| **CONFIRMED** | The finding is a real security issue. All cross-checks passed, evidence is strong, and the judge ruled in favor of the prosecution. |
| **LIKELY** | The finding is probably real but lacks sufficient evidence certainty for full confirmation. This can result from weak prosecution evidence, insufficient citations, or a purpose-aware downgrade. |
| **UNCLEAR** | The debate did not produce a definitive answer. This occurs when the judge rules unclear, the budget is exhausted, only two agents are available and the defense disagrees, or the debate could not be completed. |
| **REJECTED** | The finding is a false positive. The defense successfully demonstrated that the flagged behavior is not a security issue. Requires the defense to provide evidence scoring at least 0.3. |

Verdicts map directly to `FindingStatus` values and are applied to the finding after consensus computation:

```python
consensus_map = {
    ConsensusOutcome.CONFIRMED: FindingStatus.CONFIRMED,
    ConsensusOutcome.LIKELY:    FindingStatus.LIKELY,
    ConsensusOutcome.UNCLEAR:   FindingStatus.UNCLEAR,
    ConsensusOutcome.REJECTED:  FindingStatus.REJECTED,
}
```

---

## Configuration

The debate engine is configured through the `debate` section of `.xfire/config.yaml` (or equivalent settings). All fields have sensible defaults.

| Field | Type | Default | Description |
|---|---|---|---|
| `role_assignment` | string | `"evidence"` | Strategy for assigning debate roles. Options: `"evidence"`, `"rotate"`, `"fixed"`. |
| `fixed_roles` | mapping | `{prosecutor: claude, defense: codex, judge: gemini}` | Explicit role assignments when `role_assignment` is `"fixed"`. |
| `defense_preference` | list | `[codex, claude, gemini]` | Agent preference order for the defense role in evidence-driven assignment. |
| `judge_preference` | list | `[codex, gemini, claude]` | Agent preference order for the judge role in evidence-driven assignment. |
| `max_rounds` | int | `2` | Maximum debate rounds per finding. Minimum 1 (defense concedes), maximum 2 (judge-led Round 2). |
| `require_evidence_citations` | bool | `true` | Whether agents must cite specific code evidence in their arguments. |
| `min_agents_for_debate` | int | `2` | Minimum number of available agents required to run a debate. If fewer are available, the debate is skipped and the finding is marked UNCLEAR. |

### Example Configuration

```yaml
agents:
  debate:
    role_assignment: evidence
    defense_preference: [codex, claude, gemini]
    judge_preference: [codex, gemini, claude]
    max_rounds: 2
    require_evidence_citations: true
    min_agents_for_debate: 2
```
