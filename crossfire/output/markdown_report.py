"""Markdown report generator for CrossFire."""

from __future__ import annotations

from crossfire.core.models import CrossFireReport


def generate_markdown_report(report: CrossFireReport) -> str:
    """Generate a markdown report from a CrossFire analysis report."""
    raise NotImplementedError("Markdown report generation not yet implemented")
