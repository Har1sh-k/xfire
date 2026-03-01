"""SARIF v2.1.0 report generator for xfire."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from xfire.core.models import CrossFireReport, Finding, FindingStatus, Severity

# SARIF severity mapping
SARIF_LEVEL_MAP = {
    Severity.CRITICAL: "error",
    Severity.HIGH: "error",
    Severity.MEDIUM: "warning",
    Severity.LOW: "note",
}

# SARIF rank (0.0–100.0) for result prioritization
SARIF_RANK_MAP = {
    Severity.CRITICAL: 95.0,
    Severity.HIGH: 75.0,
    Severity.MEDIUM: 50.0,
    Severity.LOW: 25.0,
}


def generate_sarif_report(report: CrossFireReport) -> str:
    """Generate a SARIF v2.1.0 report from an xfire analysis report."""
    sarif: dict[str, Any] = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "xfire",
                        "version": "0.1.2",
                        "informationUri": "https://github.com/Har1sh-k/xfire",
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
                "properties": {
                    "xfire:overallRisk": report.overall_risk,
                    "xfire:agentsUsed": report.agents_used,
                    "xfire:summary": report.summary,
                },
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

        readable_name = cat.replace("_", " ").title()

        rule: dict[str, Any] = {
            "id": cat,
            "name": readable_name,
            "shortDescription": {
                "text": readable_name,
            },
            "defaultConfiguration": {
                "level": SARIF_LEVEL_MAP.get(finding.severity, "warning"),
            },
            "help": {
                "text": f"xfire rule for {readable_name} findings.",
            },
        }
        rules.append(rule)

    return rules


def _partial_fingerprint(finding: Finding) -> dict[str, str]:
    """Build a partial fingerprint for deduplication across runs."""
    key_parts = [
        finding.category.value,
        finding.title,
        ",".join(sorted(finding.affected_files)),
    ]
    digest = hashlib.sha256("|".join(key_parts).encode()).hexdigest()[:16]
    return {"xfire/v1": digest}


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
            "partialFingerprints": _partial_fingerprint(finding),
            "rank": SARIF_RANK_MAP.get(finding.severity, 50.0),
            "properties": {
                "confidence": finding.confidence,
                "status": finding.status.value,
                "reviewingAgents": finding.reviewing_agents,
                "exploitability": finding.exploitability.value,
                "blastRadius": finding.blast_radius.value,
            },
        }

        # Add rationale to message if available
        if finding.rationale_summary:
            result["message"]["text"] = f"{finding.title}: {finding.rationale_summary}"

        # Add locations
        for lr in finding.line_ranges:
            location: dict[str, Any] = {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": lr.file_path,
                    },
                    "region": {
                        "startLine": lr.start_line,
                        "endLine": lr.end_line,
                    },
                }
            }
            # Add code snippet if available from evidence
            for ev in finding.evidence:
                if ev.file_path == lr.file_path and ev.code_snippet:
                    location["physicalLocation"]["region"]["snippet"] = {
                        "text": ev.code_snippet,
                    }
                    break
            result["locations"].append(location)

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

        # Add related locations from evidence pointing to other files
        related_locations: list[dict[str, Any]] = []
        primary_files = {lr.file_path for lr in finding.line_ranges} | set(finding.affected_files)
        seen_related: set[str] = set()
        for ev in finding.evidence:
            if ev.file_path and ev.file_path not in primary_files and ev.file_path not in seen_related:
                seen_related.add(ev.file_path)
                related_locations.append({
                    "message": {"text": ev.description},
                    "physicalLocation": {
                        "artifactLocation": {"uri": ev.file_path},
                    },
                })
        if related_locations:
            result["relatedLocations"] = related_locations

        results.append(result)

    return results
