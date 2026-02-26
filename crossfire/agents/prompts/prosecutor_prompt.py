"""Prosecution prompts for the adversarial debate."""

from crossfire.agents.prompts.guardrails import (
    inject_guard_preamble,
    wrap_agent_output,
    wrap_external,
)

_PROSECUTOR_SYSTEM_PROMPT = """You are the prosecutor in a security review debate. Your job is to argue \
why a suspected finding represents a REAL security risk or dangerous bug.

Rules:
- You MUST cite specific code (file, line numbers, code snippets) as evidence
- You MUST explain the concrete attack or failure path
- You MUST address the purpose/intent question: is this an UNINTENDED exposure, not just an intended capability?
- You MUST NOT make unsupported claims or speculate without evidence
- If the evidence is genuinely weak, acknowledge it — your credibility matters for the consensus
- Be thorough but concise. Focus on the strongest arguments.

Output JSON:
{
  "position": "real_issue",
  "argument": "Your prosecution argument",
  "cited_evidence": [
    {"file": "path/to/file.py", "lines": "42-47", "code": "problematic code", "explanation": "why this matters"}
  ],
  "attack_path": "Step by step how this could be exploited/triggered",
  "confidence": 0.0-1.0,
  "severity_argument": "Why this deserves Critical/High/Medium/Low"
}
"""

PROSECUTOR_SYSTEM_PROMPT = inject_guard_preamble(_PROSECUTOR_SYSTEM_PROMPT)


def build_prosecutor_prompt(
    finding_summary: str,
    evidence_text: str,
    context_summary: str,
    intent_summary: str,
) -> str:
    """Build the prosecution prompt for a specific finding."""
    return (
        f"## Finding Under Review\n\n"
        f"{wrap_agent_output(finding_summary, 'review-agent')}\n\n"
        f"## Evidence Collected\n\n"
        f"{wrap_external(evidence_text, 'code-evidence')}\n\n"
        f"## PR Context\n\n"
        f"{wrap_external(context_summary, 'pr-context')}\n\n"
        f"## Repository Intent Profile\n\n"
        f"{wrap_agent_output(intent_summary, 'intent-inference')}\n\n"
        f"Now argue your case. Cite specific code as evidence. Explain the attack/failure path. "
        f"Address whether this is truly unintended or just a flagged intended capability."
    )
