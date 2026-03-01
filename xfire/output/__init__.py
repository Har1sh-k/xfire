"""Report output generators for xfire."""

from xfire.output.json_report import generate_json_report
from xfire.output.markdown_report import generate_markdown_report
from xfire.output.sarif_report import generate_sarif_report

__all__ = [
    "generate_json_report",
    "generate_markdown_report",
    "generate_sarif_report",
]
