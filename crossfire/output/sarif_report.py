"""SARIF v2.1.0 report generator for CrossFire."""

from __future__ import annotations

import json
from typing import Any

from crossfire.core.models import CrossFireReport, Finding, FindingStatus, Severity

# SARIF severity mapping
SARIF_LEVEL_MAP = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
}


def generate_sarif_report(report: CrossFireReport) -> str:
    """Generate a SARIF v2.1.0 report from a CrossFire analysis report."""
    sarif: dict[str, Any] = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "CrossFire",
                        "version": "0.1.0",
                        "informationUri": "https://github.com/crossfire",
                        "rules": _build_rules(report.findings),
                    }
                },
                "results": _build_results(report.findings),
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "toolExecutionNotifications": [],
                    }
                ],
            }
        ],
    }

    return json.dumps(sarif, indent=2)


def _build_rules(findings: list[Finding]) -> list[dict[str, Any]]:
    """Build SARIF rule entries from findings."""
    seen_categories: set[str] = set()
    rules: list[dict[str, Any]] = []

    for finding in findings:
        cat = finding.category.value
        if cat in seen_categories:
            continue
        seen_categories.add(cat)

        rules.append({
            "id": cat,
            "name": cat.replace("_", " ").title(),
            "shortDescription": {
                "text": cat.replace("_", " ").title(),
            },
            "defaultConfiguration": {
                "level": SARIF_LEVEL_MAP.get(finding.severity, "warning"),
            },
        })

    return rules


def _build_results(findings: list[Finding]) -> list[dict[str, Any]]:
    """Build SARIF result entries from findings."""
    results: list[dict[str, Any]] = []

    for finding in findings:
        # Skip rejected findings
        if finding.status == FindingStatus.REJECTED:
            continue

        result: dict[str, Any] = {
            "ruleId": finding.category.value,
            "level": SARIF_LEVEL_MAP.get(finding.severity, "warning"),
            "message": {
                "text": finding.title,
            },
            "locations": [],
            "properties": {
                "confidence": finding.confidence,
                "status": finding.status.value,
                "reviewingAgents": finding.reviewing_agents,
                "exploitability": finding.exploitability.value,
                "blastRadius": finding.blast_radius.value,
            },
        }

        # Add locations
        for lr in finding.line_ranges:
            result["locations"].append({
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": lr.file_path,
                    },
                    "region": {
                        "startLine": lr.start_line,
                        "endLine": lr.end_line,
                    },
                }
            })

        # Fallback: use affected files if no line ranges
        if not result["locations"] and finding.affected_files:
            for file_path in finding.affected_files:
                result["locations"].append({
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": file_path,
                        },
                    }
                })

        results.append(result)

    return results
