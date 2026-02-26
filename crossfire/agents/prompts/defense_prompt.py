"""Defense prompts for the adversarial debate."""

from crossfire.agents.prompts.guardrails import (
    inject_guard_preamble,
    wrap_agent_output,
    wrap_external,
)

_DEFENSE_SYSTEM_PROMPT = """You are the defense attorney in a security review debate. Your job is to argue \
why a suspected finding is a FALSE POSITIVE, intended behavior, or adequately mitigated.

Rules:
- You MUST cite specific code showing controls, validation, intended behavior, or context
- You MUST address the prosecutor's specific claims point by point
- If controls exist that mitigate the risk, cite them with file and line numbers
- If this is an intended capability per the repo purpose, explain why it's safe
- If the finding IS genuinely real, say so honestly — don't defend the indefensible
- Be thorough but concise. Focus on the strongest counter-arguments.

Output JSON:
{
  "position": "false_positive|mitigated|intended_behavior|real_issue",
  "argument": "Your defense argument",
  "cited_evidence": [
    {"file": "path/to/file.py", "lines": "23-30", "code": "control code", "explanation": "how this mitigates"}
  ],
  "controls_present": ["List of specific controls that mitigate this"],
  "confidence": 0.0-1.0,
  "counter_to_prosecution": "Specific response to prosecutor's claims"
}
"""

DEFENSE_SYSTEM_PROMPT = inject_guard_preamble(_DEFENSE_SYSTEM_PROMPT)


def build_defense_prompt(
    finding_summary: str,
    evidence_text: str,
    prosecutor_argument: str,
    context_summary: str,
    intent_summary: str,
) -> str:
    """Build the defense prompt for a specific finding."""
    return (
        f"## Finding Under Review\n\n"
        f"{wrap_agent_output(finding_summary, 'review-agent')}\n\n"
        f"## Evidence Collected\n\n"
        f"{wrap_external(evidence_text, 'code-evidence')}\n\n"
        f"## Prosecutor's Argument\n\n"
        f"{wrap_agent_output(prosecutor_argument, 'prosecutor')}\n\n"
        f"## PR Context\n\n"
        f"{wrap_external(context_summary, 'pr-context')}\n\n"
        f"## Repository Intent Profile\n\n"
        f"{wrap_agent_output(intent_summary, 'intent-inference')}\n\n"
        f"Now present your defense. Address the prosecutor's specific claims. "
        f"Cite code showing controls, intended behavior, or context they missed. "
        f"If the finding is genuinely real, acknowledge it honestly."
    )
