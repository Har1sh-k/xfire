"""JSON report generator for xfire."""

from __future__ import annotations

from xfire.core.models import CrossFireReport


def generate_json_report(report: CrossFireReport) -> str:
    """Generate a JSON report from an xfire analysis report."""
    return report.model_dump_json(indent=2)
