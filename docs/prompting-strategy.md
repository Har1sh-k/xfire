# Prompting Strategy

## Philosophy

The review prompt is the product. Most of CrossFire's value comes from how we prompt the agents. The prompts in `crossfire/agents/prompts/` are the most important code in the repo.

## Review Prompt

The independent review prompt (`review_prompt.py`) does several things:

1. **Sets the role**: "You are an elite security engineer" — not a scanner, not a tool
2. **Defines methodology**: A 5-step review process mirroring how human reviewers work
3. **Enforces purpose-awareness**: Explicit instructions to check intent before flagging
4. **Provides examples**: Good vs bad findings to calibrate quality
5. **Requires evidence**: Every finding must cite specific code
6. **Structures output**: JSON format ensures parseable results

## Debate Prompts

### Prosecutor
- Must cite specific code as evidence
- Must explain concrete attack/failure path
- Must address intent question
- Honest about weak evidence

### Defense
- Must cite code showing controls/context
- Must address prosecutor's claims point by point
- Honest about real issues (don't defend the indefensible)

### Judge
- Evaluates evidence quality from both sides
- References specific arguments
- Clear ruling with reasoning
- Sets final severity and confidence

## Key Principles

1. **Code citations required**: Abstract reasoning without code refs is weak
2. **Purpose-awareness embedded**: Every prompt references intent profile
3. **Honesty incentivized**: Agents are told their credibility matters
4. **Structured output**: JSON format ensures machine-parseable results
5. **Balanced debate**: Both sides get equal context and opportunity
