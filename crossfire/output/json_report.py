"""JSON report generator for CrossFire."""

from __future__ import annotations

from crossfire.core.models import CrossFireReport


def generate_json_report(report: CrossFireReport) -> str:
    """Generate a JSON report from a CrossFire analysis report."""
    raise NotImplementedError("JSON report generation not yet implemented")
