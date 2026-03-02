# Prompting Strategy

## Philosophy

The review prompt is the product. Most of xFire's value comes from how we prompt the agents. The prompts in `xfire/agents/prompts/` are the most important code in the repo.

## Review Prompt (`review_prompt.py`)

The independent review prompt does several things:

1. **Sets the role**: "You are an elite security engineer" — not a scanner, not a tool
2. **Defines methodology**: A 5-step review process mirroring how human reviewers work
3. **Enforces purpose-awareness**: Explicit instructions to check intent before flagging
4. **Provides examples**: Good vs bad findings to calibrate quality
5. **Requires evidence**: Every finding must cite specific code
6. **Structures output**: JSON format ensures parseable results

## Debate Prompts

The debate uses a 2-round judge-led structure. There are 4 prompt files for the 3 roles.

### Round 1

**Prosecutor** (`prosecutor_prompt.py` → `build_prosecutor_prompt()`):
- Must cite specific code as evidence
- Must explain concrete attack/failure path
- Must address the intent question (is this capability intended?)
- Honest about weak evidence

**Defense** (`defense_prompt.py` → `build_defense_prompt()`):
- Must cite code showing controls/context
- Must address prosecutor's claims point by point
- Honest about real issues (don't defend the indefensible)
- Signals whether it concedes or disagrees

### Round 2 (triggered only if defense disagrees)

**Judge Clarification** (`judge_prompt.py` → `build_judge_clarification_prompt()`):
- Reviews Round 1 arguments
- Asks targeted clarifying questions to both sides
- Both prosecution and defense respond to the questions

**Judge Final Ruling** (`judge_prompt.py` → `build_judge_final_prompt()`):
- Evaluates all evidence from both rounds
- References specific arguments and evidence citations
- Issues a clear ruling with reasoning
- Sets final severity and confidence

### Skipping Round 2

`build_judge_prompt()` handles the single-round path (defense concedes or `max_rounds: 1` in config). The judge issues a final ruling directly from Round 1 arguments.

## Debate Flow

```
Round 1:
  Prosecutor argues → Defense responds
       |
       v
  Defense concedes? ──YES──> Judge rules directly (1 round)
       |
       NO
       v
Round 2:
  Judge asks clarifying questions
  Both sides respond
       |
       v
  Judge issues final ruling (2 rounds)
```

## Key Principles

1. **Code citations required**: Abstract reasoning without code refs is weak
2. **Purpose-awareness embedded**: Every prompt references the intent profile
3. **Honesty incentivized**: Agents are told their credibility matters
4. **Structured output**: JSON format ensures machine-parseable results
5. **Balanced debate**: Both sides get equal context and opportunity
6. **Evidence quality graded**: Judge explicitly assesses the quality of evidence, not just the argument
