"""Severity and confidence scoring logic."""

from __future__ import annotations

from xfire.core.models import Finding, FindingStatus, Severity

SEVERITY_ORDER = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
}


def should_fail_ci(
    findings: list[Finding],
    fail_on: str,
    min_confidence: float,
    require_debate: bool,
) -> bool:
    """Determine if CI should fail based on findings and severity gate config.

    Args:
        findings: All findings from the analysis.
        fail_on: Minimum severity to trigger failure ("critical", "high", "medium", "low").
        min_confidence: Minimum confidence threshold.
        require_debate: Only fail on debated/confirmed findings.

    Returns:
        True if CI should fail.
    """
    try:
        threshold = Severity(fail_on.capitalize())
    except ValueError:
        threshold = Severity.HIGH

    threshold_order = SEVERITY_ORDER[threshold]

    for finding in findings:
        if SEVERITY_ORDER[finding.severity] < threshold_order:
            continue
        if finding.confidence < min_confidence:
            continue
        if require_debate and finding.status not in (FindingStatus.CONFIRMED, FindingStatus.LIKELY):
            continue
        return True

    return False
