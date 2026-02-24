"""Judge prompts for the adversarial debate."""

JUDGE_SYSTEM_PROMPT = """You are the judge in a security review debate. You have seen the prosecution \
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


def build_judge_prompt(
    finding_summary: str,
    prosecutor_argument: str,
    defense_argument: str,
    rebuttal_argument: str | None,
    intent_summary: str,
) -> str:
    """Build the judge prompt for a specific finding."""
    parts = [
        f"## Finding Under Review\n\n{finding_summary}",
        f"## Prosecutor's Argument\n\n{prosecutor_argument}",
        f"## Defense's Argument\n\n{defense_argument}",
    ]

    if rebuttal_argument:
        parts.append(f"## Prosecutor's Rebuttal\n\n{rebuttal_argument}")

    parts.append(f"## Repository Intent Profile\n\n{intent_summary}")
    parts.append(
        "Now make your ruling. Evaluate evidence quality from both sides. "
        "Reference specific arguments and code citations. "
        "Be fair, thorough, and evidence-driven."
    )

    return "\n\n".join(parts)
