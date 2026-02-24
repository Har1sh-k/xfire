"""SARIF report generator for CrossFire."""

from __future__ import annotations

from crossfire.core.models import CrossFireReport


def generate_sarif_report(report: CrossFireReport) -> str:
    """Generate a SARIF v2.1.0 report from a CrossFire analysis report."""
    raise NotImplementedError("SARIF report generation not yet implemented")
