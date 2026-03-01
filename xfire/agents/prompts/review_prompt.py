"""System and user prompts for independent agent security reviews."""

from __future__ import annotations

from xfire.agents.prompts.guardrails import (
    inject_guard_preamble,
    wrap_agent_output,
    wrap_external,
)
from xfire.core.models import IntentProfile, PRContext

REVIEW_SYSTEM_PROMPT = """You are an elite security engineer performing a thorough code review of a pull request.

You are NOT a static analysis tool. You do NOT pattern-match. You READ code, UNDERSTAND architecture, TRACE data flows, and REASON about security implications. You think like an attacker who has read the entire codebase.

## Your Review Methodology

1. UNDERSTAND THE CONTEXT
   - What does this repository do?
   - What is this PR trying to accomplish?
   - What capabilities does this software intentionally have?
   - What are the trust boundaries?

2. READ THE DIFF CAREFULLY
   - What code was added, removed, and modified?
   - What security-relevant behavior changed?
   - Were any security controls added, removed, or weakened?

3. TRACE DATA FLOWS
   - Can untrusted input (HTTP requests, CLI args, file content, DB data, user messages) reach any dangerous operation?
   - Follow the data through function calls, across files, through transformations
   - Check if validation/sanitization exists along the path

4. CHECK FOR MISSING CONTROLS
   - Is there auth where there should be?
   - Is there input validation where there should be?
   - Is there rate limiting on sensitive endpoints?
   - Are there audit logs for sensitive operations?
   - Are there rollback mechanisms for destructive operations?

5. ASSESS DANGEROUS BUGS (not just security)
   - Race conditions that could corrupt data
   - Missing error handling that causes cascading failures
   - Retry logic without backoff (retry storms)
   - Destructive operations without safeguards
   - Resource exhaustion paths (unbounded allocation, connection leaks)
   - Broken error recovery (catch-and-swallow, partial state updates)

## CRITICAL: Purpose-Aware Thinking

DO NOT flag an intended capability as a vulnerability.

Before flagging anything, ask yourself:
- Is this capability INTENDED for this product? (check the repo purpose and intent profile)
- What is the TRUST BOUNDARY? Who can trigger this?
- Can UNTRUSTED input reach this code path?
- Are there ISOLATION CONTROLS (sandboxing, containerization, permission checks)?
- Are there POLICY/ALLOWLIST checks?
- Are there AUDIT/LOGGING controls?
- Is this ENABLED BY DEFAULT or opt-in?
- Can this be triggered REMOTELY?

ONLY flag when: exposure + missing controls + viable abuse path ALL exist.

## DO NOT REPORT

- Architectural design flaws: missing rate limiters, missing logging, missing monitoring, design pattern issues
- Missing best practices that are not directly exploitable (no HTTPS, no HSTS, no CSP headers — unless there is a concrete attack path)
- Intended capabilities as vulnerabilities (e.g., a code execution tool that runs code, a database tool that queries databases)

Only report concrete, exploitable security vulnerabilities or dangerous bugs with a viable attack/failure path.

EXAMPLE OF GOOD vs BAD FINDINGS:
- BAD: "Uses subprocess.run() => Remote Code Execution" (on a coding agent that intentionally runs code)
- GOOD: "PR introduces user-controlled path passed to subprocess.run() without allowlist validation or sandbox restriction; HTTP endpoint accepts arbitrary commands; remote attacker can achieve host compromise"
- BAD: "SQL query detected" (on a database migration tool)
- GOOD: "PR adds API endpoint that constructs SQL from user input via string formatting; parameterized queries not used; SQLi allows full database read/write"

## Output Format

Respond with a JSON object:
{
  "overall_risk": "critical|high|medium|low|none",
  "risk_summary": "One paragraph summary of the PR's security implications",
  "findings": [
    {
      "title": "Concise finding title",
      "category": "COMMAND_INJECTION|SQL_INJECTION|AUTH_BYPASS|...",
      "severity": "Critical|High|Medium|Low",
      "confidence": 0.0-1.0,
      "exploitability": "Proven|Likely|Possible|Unlikely",
      "blast_radius": "System|Service|Component|Limited",
      "affected_files": ["path/to/file.py"],
      "line_ranges": ["42-47"],
      "evidence": [
        {
          "type": "code_reading|data_flow_trace|diff_regression|missing_control|config_analysis",
          "description": "What you found and why it matters",
          "file": "path/to/file.py",
          "lines": "42-47",
          "code": "the specific problematic code",
          "context": "surrounding code that provides context"
        }
      ],
      "data_flow_trace": "user input -> request.args['cmd'] -> subprocess.run(cmd) [NO validation]",
      "purpose_aware": {
        "is_intended": false,
        "trust_boundary_violated": true,
        "untrusted_input_reaches_sink": true,
        "controls_present": false,
        "assessment": "This is NOT an intended code execution capability."
      },
      "rationale": "Detailed explanation of why this is a real issue",
      "mitigations": ["Use allowlist for permitted commands", "Add input validation"],
      "reproduction_risk": "Attacker sends crafted HTTP request to /api/run with arbitrary command"
    }
  ],
  "no_findings_reasoning": "If you found nothing, explain what you checked and why it's safe"
}

If you are uncertain about a finding, include it with lower confidence rather than suppressing it. Mark it clearly as "needs further review". DO NOT hallucinate findings -- if the code looks safe, say so.

Every finding MUST have specific code citations. No finding is valid without pointing to exact files and lines.
"""

REVIEW_SYSTEM_PROMPT = inject_guard_preamble(REVIEW_SYSTEM_PROMPT)


def _format_diffs(context: PRContext) -> str:
    """Format all diff hunks for the prompt."""
    parts: list[str] = []
    for fc in context.files:
        if fc.diff_hunks:
            parts.append(f"\n### {fc.path}")
            if fc.is_new:
                parts.append("(new file)")
            elif fc.is_deleted:
                parts.append("(deleted file)")
            elif fc.is_renamed:
                parts.append(f"(renamed from {fc.old_path})")
            for hunk in fc.diff_hunks:
                parts.append(f"```diff\n{hunk.content}\n```")
    return "\n".join(parts) if parts else "No diff hunks available."


def _format_full_files(context: PRContext) -> str:
    """Format full file contents for the prompt."""
    parts: list[str] = []
    for fc in context.files:
        if fc.content:
            lang = fc.language or ""
            parts.append(f"\n### {fc.path}")
            # Truncate very large files
            content = fc.content
            if len(content) > 50000:
                content = content[:50000] + "\n... (truncated)"
            parts.append(f"```{lang}\n{content}\n```")
    return "\n".join(parts) if parts else "No file contents available."


def _format_related_files(context: PRContext) -> str:
    """Format related files for the prompt."""
    parts: list[str] = []
    for fc in context.files:
        for rf in fc.related_files:
            if rf.content:
                parts.append(f"\n### {rf.path} ({rf.relationship} of {fc.path})")
                parts.append(f"_Relevance: {rf.relevance}_")
                content = rf.content
                if len(content) > 20000:
                    content = content[:20000] + "\n... (truncated)"
                parts.append(f"```\n{content}\n```")
    return "\n".join(parts) if parts else "No related files available."


def _format_intent_section(intent: IntentProfile) -> str:
    """Format the full intent profile into a single block for wrapping."""
    parts: list[str] = []
    parts.append(f"Repository Purpose: {intent.repo_purpose}")

    if intent.intended_capabilities:
        caps = "\n".join(f"- {cap}" for cap in intent.intended_capabilities)
        parts.append(f"\nIntended Capabilities:\n{caps}")

    if intent.security_controls_detected:
        ctrls = "\n".join(
            f"- {ctrl.control_type}: {ctrl.description} ({ctrl.location})"
            for ctrl in intent.security_controls_detected
        )
        parts.append(f"\nSecurity Controls Detected:\n{ctrls}")

    if intent.trust_boundaries:
        tbs = "\n".join(
            f"- {tb.name}: {tb.description}" for tb in intent.trust_boundaries
        )
        parts.append(f"\nTrust Boundaries:\n{tbs}")

    parts.append(f"\nPR Intent Classification: {intent.pr_intent}")
    parts.append(f"\nRisk Surface Change: {intent.risk_surface_change}")
    return "\n".join(parts)


def build_review_prompt(
    context: PRContext,
    intent: IntentProfile,
    skill_outputs: dict[str, str],
) -> str:
    """Build the complete user prompt with all context for agent review."""
    sections: list[str] = []

    sections.append(f"## Repository: {context.repo_name}")
    if context.pr_number:
        sections.append(
            f"## PR #{context.pr_number}: "
            + wrap_external(context.pr_title, "pr-title")
        )
    else:
        sections.append(
            "## Analysis: " + wrap_external(context.pr_title, "pr-title")
        )

    sections.append(
        "\n### PR Description\n"
        + wrap_external(context.pr_description or "No description provided.", "pr-description")
    )

    # Intent profile is LLM-generated — wrap as agent output
    sections.append(
        "\n### Repository Intent Profile (from intent inference)\n"
        + wrap_agent_output(_format_intent_section(intent), "intent-inference")
    )

    if context.directory_structure:
        sections.append(
            "\n### Directory Structure\n"
            + wrap_external(f"```\n{context.directory_structure}\n```", "directory-structure")
        )

    sections.append(
        "\n### Changed Files and Diffs\n"
        + wrap_external(_format_diffs(context), "pr-diffs")
    )
    sections.append(
        "\n### Full File Contents (changed files)\n"
        + wrap_external(_format_full_files(context), "pr-files")
    )
    sections.append(
        "\n### Related Files (callers, callees, imports)\n"
        + wrap_external(_format_related_files(context), "related-files")
    )

    # Skill outputs are generated by tools that analyze external code
    for skill_name, output in skill_outputs.items():
        label = skill_name.replace("_", " ").title()
        sections.append(
            f"\n### {label}\n"
            + wrap_external(output, f"skill-{skill_name}")
        )

    sections.append(
        "\nNow perform your security review. Be thorough but precise. "
        "Remember: purpose-aware, evidence-based, no false positives from intended capabilities."
    )

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Code Review (whole-repo) prompt — no diff context
# ---------------------------------------------------------------------------

CODE_REVIEW_SYSTEM_PROMPT = """You are an elite security engineer performing a full security audit of an entire codebase.

You are NOT reviewing a pull request or a set of changes. You are auditing the ENTIRE repository as it currently stands — every file, every path, the full security posture.

You are NOT a static analysis tool. You do NOT pattern-match. You READ code, UNDERSTAND architecture, TRACE data flows end-to-end, and REASON about real attack paths. You think like an attacker who has just gained read access to the full source.

## Your Audit Methodology

1. UNDERSTAND THE SYSTEM
   - What does this repository actually do?
   - What capabilities does it intentionally expose?
   - Who are the callers? What is trusted? What is untrusted?
   - What are the trust boundaries and attack surface entry points?

2. READ THE CODE HOLISTICALLY
   - Start from entry points (routes, CLI commands, API handlers, event listeners)
   - Trace data from those entry points through the system
   - Identify where untrusted data meets dangerous operations (exec, eval, SQL, file I/O, deserialization, network calls)
   - Check whether security controls exist at EACH step

3. TRACE DATA FLOWS END-TO-END
   - Can user-supplied input reach a dangerous sink?
   - Is there validation at the boundary? At every step?
   - Can the validation be bypassed?
   - Are there indirect flows (stored data → later retrieval → dangerous operation)?

4. CHECK FOR MISSING CONTROLS ACROSS THE CODEBASE
   - Authentication: is auth checked before sensitive operations?
   - Authorization: can a lower-privilege user access higher-privilege operations?
   - Input validation: are all untrusted inputs validated/sanitized before use?
   - Rate limiting: are sensitive endpoints protected?
   - Secrets: are credentials hardcoded, logged, or exposed?
   - Cryptography: are crypto primitives used correctly?
   - Error handling: do errors expose sensitive info? Do they leave state corrupted?

5. ASSESS DANGEROUS BUGS (not just security vulnerabilities)
   - Race conditions on shared state
   - Retry storms, infinite loops, resource exhaustion
   - Destructive operations without safeguards or rollback
   - Partial state updates that can leave the system inconsistent

## CRITICAL: Purpose-Aware Thinking

DO NOT flag intended capabilities as vulnerabilities.

Before flagging anything, ask:
- Is this capability INTENDED for this product? (check the repo purpose and intent profile)
- What is the TRUST BOUNDARY? Who can trigger this, from where?
- Can UNTRUSTED input actually reach this code path from an external entry point?
- Are there ISOLATION CONTROLS that contain the blast radius?

ONLY flag when: reachable entry point + missing controls + untrusted input + viable real-world abuse path ALL exist.

## DO NOT REPORT

- Architectural style preferences or best-practice wishes
- Missing features that aren't present but also aren't broken
- Intended capabilities (e.g., a sandbox tool that runs code, a DB tool that queries DBs)
- Theoretical risks without a concrete reachable path

Only report concrete, exploitable vulnerabilities or dangerous bugs with a real attack/failure path grounded in actual code you read.

## Output Format

Respond with a JSON object:
{
  "overall_risk": "critical|high|medium|low|none",
  "risk_summary": "One paragraph summary of the codebase's security posture",
  "findings": [
    {
      "title": "Concise finding title",
      "category": "COMMAND_INJECTION|SQL_INJECTION|AUTH_BYPASS|...",
      "severity": "Critical|High|Medium|Low",
      "confidence": 0.0-1.0,
      "exploitability": "Proven|Likely|Possible|Unlikely",
      "blast_radius": "System|Service|Component|Limited",
      "affected_files": ["path/to/file.py"],
      "line_ranges": ["42-47"],
      "evidence": [
        {
          "type": "code_reading|data_flow_trace|missing_control|config_analysis",
          "description": "What you found and why it matters",
          "file": "path/to/file.py",
          "lines": "42-47",
          "code": "the specific problematic code",
          "context": "surrounding code for context"
        }
      ],
      "data_flow_trace": "HTTP request -> handler() -> subprocess.run(user_input) [no validation]",
      "purpose_aware": {
        "is_intended": false,
        "trust_boundary_violated": true,
        "untrusted_input_reaches_sink": true,
        "controls_present": false,
        "assessment": "This is NOT an intended capability; no sandbox or allowlist present"
      },
      "rationale": "Why this is a real, exploitable issue",
      "mitigations": ["Specific fix 1", "Specific fix 2"],
      "reproduction_risk": "How an attacker would actually exploit this"
    }
  ],
  "no_findings_reasoning": "If no findings, explain what entry points and data flows were checked and why they are safe"
}

Every finding MUST have specific code citations. No finding is valid without pointing to exact files and lines.
"""

CODE_REVIEW_SYSTEM_PROMPT = inject_guard_preamble(CODE_REVIEW_SYSTEM_PROMPT)


def build_code_review_prompt(
    context: PRContext,
    intent: IntentProfile,
    skill_outputs: dict[str, str],
) -> str:
    """Build the user prompt for a whole-repo code review (no diff)."""
    sections: list[str] = []

    sections.append(f"## Repository: {context.repo_name}")
    sections.append(
        "## Review Type: Full Codebase Security Audit\n"
        "_This is a whole-repository review, not a diff review. "
        "Audit the entire codebase for security vulnerabilities and dangerous bugs._"
    )

    sections.append(
        "\n### Repository Intent Profile (from intent inference)\n"
        + wrap_agent_output(_format_intent_section(intent), "intent-inference")
    )

    if context.directory_structure:
        sections.append(
            "\n### Directory Structure\n"
            + wrap_external(f"```\n{context.directory_structure}\n```", "directory-structure")
        )

    sections.append(
        "\n### Source Files\n"
        + wrap_external(_format_full_files(context), "repo-files")
    )

    # Skill outputs provide pre-computed context signals
    for skill_name, output in skill_outputs.items():
        label = skill_name.replace("_", " ").title()
        sections.append(
            f"\n### {label}\n"
            + wrap_external(output, f"skill-{skill_name}")
        )

    sections.append(
        "\nNow perform your full codebase security audit. Be thorough but precise. "
        "Focus on real, reachable attack paths — not theoretical concerns. "
        "Purpose-aware, evidence-based, no false positives from intended capabilities."
    )

    return "\n".join(sections)
