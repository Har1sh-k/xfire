"""Policy engine — suppressions, waivers, and repo-specific rules."""

from __future__ import annotations

import re
from typing import Any

import structlog

from xfire.core.models import Finding, FindingStatus

logger = structlog.get_logger()


class PolicyEngine:
    """Applies suppressions, waivers, and custom rules to findings."""

    def __init__(self, suppressions: list[dict[str, Any]] | None = None) -> None:
        self.suppressions = suppressions or []

    def apply(self, findings: list[Finding]) -> list[Finding]:
        """Apply all policy rules to findings.

        Returns the filtered list (suppressed findings are removed or
        marked as rejected).
        """
        result: list[Finding] = []

        for finding in findings:
            suppressed = self._check_suppressions(finding)
            if suppressed:
                logger.info(
                    "policy.suppressed",
                    finding=finding.title,
                    reason=suppressed,
                )
                finding.status = FindingStatus.REJECTED
                finding.debate_summary = f"Suppressed by policy: {suppressed}"
            result.append(finding)

        return result

    def _check_suppressions(self, finding: Finding) -> str | None:
        """Check if a finding matches any suppression rule.

        Returns the suppression reason if matched, None otherwise.

        Suppression format:
        {
            "category": "COMMAND_INJECTION",  # optional: match by category
            "file_pattern": "tests/.*",        # optional: match by file path regex
            "title_pattern": ".*test.*",        # optional: match by title regex
            "reason": "Accepted risk for testing infrastructure"
        }
        """
        for rule in self.suppressions:
            if self._matches_rule(finding, rule):
                return rule.get("reason", "Matched suppression rule")
        return None

    def _matches_rule(self, finding: Finding, rule: dict[str, Any]) -> bool:
        """Check if a finding matches a suppression rule."""
        # Category match
        if "category" in rule:
            if finding.category.value != rule["category"]:
                return False

        # File pattern match
        if "file_pattern" in rule:
            pattern = rule["file_pattern"]
            if not any(re.match(pattern, f) for f in finding.affected_files):
                return False

        # Title pattern match
        if "title_pattern" in rule:
            if not re.match(rule["title_pattern"], finding.title, re.IGNORECASE):
                return False

        return True
