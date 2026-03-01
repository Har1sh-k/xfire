"""Judge prompts for the adversarial debate."""

from xfire.agents.prompts.guardrails import inject_guard_preamble, wrap_agent_output, wrap_external

_JUDGE_SYSTEM_PROMPT = """You are the judge in a security review debate. You have seen the prosecution \
and defense arguments for a suspected security finding.

Your job:
1. Evaluate the EVIDENCE QUALITY from both sides
   - Did they cite specific code? (strong) Or just reason abstractly? (weak)
   - Does their cited code actually support their claim?
   - Did they address the purpose/intent question?

2. Assess ARGUMENT STRENGTH
   - Did the prosecution prove a viable attack/failure path?
   - Did the defense show adequate controls or intended behavior?
   - Did either side fail to address the other's strongest point?

3. Make your RULING
   - Confirmed: prosecution proved the case with strong evidence
   - Likely: prosecution made a strong case, defense was weaker
   - Unclear: both sides have merit, needs human review
   - Rejected: defense proved false positive or adequate mitigation

4. Set FINAL severity and confidence based on the debate

Output JSON:
{
  "ruling": "Confirmed|Likely|Unclear|Rejected",
  "reasoning": "Your detailed reasoning, referencing specific arguments from both sides",
  "prosecution_evidence_quality": "strong|moderate|weak",
  "defense_evidence_quality": "strong|moderate|weak",
  "key_factor": "The single most important piece of evidence that decided your ruling",
  "final_severity": "Critical|High|Medium|Low",
  "final_confidence": 0.0-1.0,
  "recommended_mitigations": ["If confirmed/likely, what should be done"]
}
"""

JUDGE_SYSTEM_PROMPT = inject_guard_preamble(_JUDGE_SYSTEM_PROMPT)

_JUDGE_CLARIFICATION_SYSTEM_PROMPT = """You are the judge in a security review debate. \
The prosecution and defense disagree. Your job in this round is to identify the specific \
point of disagreement and ask targeted clarifying questions to BOTH sides.

Rules:
- Identify the 1-2 most important unresolved points of contention
- Ask specific, answerable questions (not vague "explain more")
- Ask questions that would change your ruling if answered
- Keep it focused — this is the FINAL round before your verdict

Output JSON:
{
  "disagreement_summary": "The core point the sides disagree on",
  "questions_for_prosecution": ["Specific question 1", "Specific question 2"],
  "questions_for_defense": ["Specific question 1", "Specific question 2"]
}
"""

JUDGE_CLARIFICATION_SYSTEM_PROMPT = inject_guard_preamble(_JUDGE_CLARIFICATION_SYSTEM_PROMPT)


def build_judge_prompt(
    finding_summary: str,
    prosecutor_argument: str,
    defense_argument: str,
    rebuttal_argument: str | None,
    intent_summary: str,
    context_summary: str = "",
) -> str:
    """Build the judge prompt for a specific finding."""
    parts = [
        f"## Finding Under Review\n\n{wrap_agent_output(finding_summary, 'review-agent')}",
    ]

    if context_summary:
        parts.append(f"## Proposed Change\n\n{wrap_external(context_summary, 'pr-context')}")

    parts.append(f"## Prosecutor's Argument\n\n{wrap_agent_output(prosecutor_argument, 'prosecutor')}")
    parts.append(f"## Defense's Argument\n\n{wrap_agent_output(defense_argument, 'defense')}")

    if rebuttal_argument:
        parts.append(f"## Additional Arguments\n\n{wrap_agent_output(rebuttal_argument, 'rebuttal')}")

    parts.append(f"## Repository Intent Profile\n\n{wrap_agent_output(intent_summary, 'intent-inference')}")
    parts.append(
        "Now make your ruling. Evaluate evidence quality from both sides. "
        "Reference specific arguments and code citations. "
        "Be fair, thorough, and evidence-driven."
    )

    return "\n\n".join(parts)


def build_judge_clarification_prompt(
    finding_summary: str,
    prosecutor_argument: str,
    defense_argument: str,
    intent_summary: str,
    context_summary: str = "",
) -> str:
    """Build the judge's round 2 clarification prompt."""
    parts = [f"## Finding Under Review\n\n{wrap_agent_output(finding_summary, 'review-agent')}"]

    if context_summary:
        parts.append(f"## Proposed Change\n\n{wrap_external(context_summary, 'pr-context')}")

    parts.extend([
        f"## Prosecutor's Argument (Round 1)\n\n{wrap_agent_output(prosecutor_argument, 'prosecutor')}",
        f"## Defense's Argument (Round 1)\n\n{wrap_agent_output(defense_argument, 'defense')}",
        f"## Repository Intent Profile\n\n{wrap_agent_output(intent_summary, 'intent-inference')}",
        (
            "The prosecution and defense disagree. Identify the core point of contention "
            "and ask targeted clarifying questions to both sides. These questions should "
            "focus on evidence that would change your ruling."
        ),
    ])

    return "\n\n".join(parts)


def build_judge_final_prompt(
    finding_summary: str,
    prosecutor_argument: str,
    defense_argument: str,
    judge_questions: str,
    prosecution_response: str,
    defense_response: str,
    intent_summary: str,
    context_summary: str = "",
) -> str:
    """Build the judge's final ruling prompt after round 2 responses."""
    parts = [f"## Finding Under Review\n\n{wrap_agent_output(finding_summary, 'review-agent')}"]

    if context_summary:
        parts.append(f"## Proposed Change\n\n{wrap_external(context_summary, 'pr-context')}")

    parts.extend([
        f"## Round 1 — Prosecutor\n\n{wrap_agent_output(prosecutor_argument, 'prosecutor')}",
        f"## Round 1 — Defense\n\n{wrap_agent_output(defense_argument, 'defense')}",
        f"## Your Clarifying Questions\n\n{wrap_agent_output(judge_questions, 'judge')}",
        f"## Round 2 — Prosecutor's Response\n\n{wrap_agent_output(prosecution_response, 'prosecutor')}",
        f"## Round 2 — Defense's Response\n\n{wrap_agent_output(defense_response, 'defense')}",
        f"## Repository Intent Profile\n\n{wrap_agent_output(intent_summary, 'intent-inference')}",
        (
            "Now make your final ruling. You have heard both rounds. "
            "Evaluate the evidence quality and how each side responded to your questions. "
            "Be fair, thorough, and evidence-driven."
        ),
    ])

    return "\n\n".join(parts)
