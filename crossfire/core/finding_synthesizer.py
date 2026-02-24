"""Finding synthesizer — merge, dedupe, and score findings from all agents."""

from __future__ import annotations

import structlog

from crossfire.core.models import (
    AgentReview,
    BlastRadius,
    DebateTag,
    Evidence,
    Finding,
    FindingStatus,
    IntentProfile,
    Severity,
)

logger = structlog.get_logger()

# Severity ordering for comparisons
SEVERITY_ORDER = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
}


def _severity_max(a: Severity, b: Severity) -> Severity:
    """Return the higher severity."""
    return a if SEVERITY_ORDER[a] >= SEVERITY_ORDER[b] else b


def _files_overlap(files_a: list[str], files_b: list[str]) -> bool:
    """Check if two file lists overlap."""
    return bool(set(files_a) & set(files_b))


def _lines_overlap(finding_a: Finding, finding_b: Finding) -> bool:
    """Check if two findings have overlapping line ranges."""
    for lr_a in finding_a.line_ranges:
        for lr_b in finding_b.line_ranges:
            if lr_a.file_path != lr_b.file_path:
                continue
            if lr_a.start_line <= lr_b.end_line and lr_b.start_line <= lr_a.end_line:
                return True
    return False


def _is_similar_finding(a: Finding, b: Finding) -> bool:
    """Determine if two findings are likely about the same issue."""
    # Same category + overlapping files + overlapping lines → likely same
    if a.category == b.category and _files_overlap(a.affected_files, b.affected_files):
        if _lines_overlap(a, b):
            return True
        # Same category + overlapping files without line overlap → possibly same
        # Check title similarity
        a_words = set(a.title.lower().split())
        b_words = set(b.title.lower().split())
        common = a_words & b_words - {"the", "a", "an", "in", "of", "to", "and", "or", "is"}
        if len(common) >= 2:
            return True

    return False


def _merge_findings(cluster: list[Finding]) -> Finding:
    """Merge a cluster of similar findings into one."""
    # Use the highest-severity finding as the base
    base = max(cluster, key=lambda f: SEVERITY_ORDER[f.severity])

    # Union all evidence
    all_evidence: list[Evidence] = []
    for f in cluster:
        all_evidence.extend(f.evidence)

    # Union all affected files
    all_files = list(dict.fromkeys(
        file for f in cluster for file in f.affected_files
    ))

    # Track which agents found this
    agents = list(dict.fromkeys(
        agent for f in cluster for agent in f.reviewing_agents
    ))

    # Combine rationale
    rationales = [f.rationale_summary for f in cluster if f.rationale_summary]
    combined_rationale = " | ".join(dict.fromkeys(rationales))

    # Union mitigations
    mitigations = list(dict.fromkeys(
        m for f in cluster for m in f.mitigations
    ))

    # Union line ranges (dedupe)
    seen_ranges: set[tuple] = set()
    line_ranges = []
    for f in cluster:
        for lr in f.line_ranges:
            key = (lr.file_path, lr.start_line, lr.end_line)
            if key not in seen_ranges:
                seen_ranges.add(key)
                line_ranges.append(lr)

    # Take best purpose assessment
    best_pa = base.purpose_aware_assessment

    merged = base.model_copy(update={
        "evidence": all_evidence,
        "affected_files": all_files,
        "reviewing_agents": agents,
        "rationale_summary": combined_rationale,
        "mitigations": mitigations,
        "line_ranges": line_ranges,
        "purpose_aware_assessment": best_pa,
    })

    return merged


class FindingSynthesizer:
    """Merge findings from independent agent reviews into a unified list."""

    def synthesize(
        self, reviews: list[AgentReview], intent: IntentProfile
    ) -> list[Finding]:
        """Merge, dedupe, score, and tag findings from all agent reviews.

        Steps:
        1. Collect all findings
        2. Cluster similar findings
        3. Merge clusters
        4. Boost confidence for cross-validated findings
        5. Apply purpose-aware adjustments
        6. Tag for debate routing
        """
        # 1. Collect all findings
        all_findings: list[Finding] = []
        for review in reviews:
            all_findings.extend(review.findings)

        if not all_findings:
            logger.info("synthesizer.no_findings")
            return []

        logger.info("synthesizer.start", total_findings=len(all_findings))

        # 2. Cluster similar findings
        clusters: list[list[Finding]] = []
        used: set[int] = set()

        for i, finding_a in enumerate(all_findings):
            if i in used:
                continue
            cluster = [finding_a]
            used.add(i)

            for j, finding_b in enumerate(all_findings):
                if j in used:
                    continue
                if _is_similar_finding(finding_a, finding_b):
                    cluster.append(finding_b)
                    used.add(j)

            clusters.append(cluster)

        # 3. Merge clusters
        merged_findings: list[Finding] = []
        for cluster in clusters:
            merged = _merge_findings(cluster)
            merged_findings.append(merged)

        # 4. Boost confidence for cross-validated findings
        for finding in merged_findings:
            agent_count = len(finding.reviewing_agents)
            if agent_count == 2:
                finding.confidence = min(finding.confidence * 1.2, 0.95)
            elif agent_count >= 3:
                finding.confidence = min(finding.confidence * 1.4, 0.99)

        # 5. Apply purpose-aware adjustments
        for finding in merged_findings:
            self._apply_purpose_aware_adjustments(finding, intent)

        # 6. Tag for debate routing
        for finding in merged_findings:
            self._tag_for_debate(finding)

        # Sort by severity then confidence (highest first)
        merged_findings.sort(
            key=lambda f: (SEVERITY_ORDER[f.severity], f.confidence),
            reverse=True,
        )

        logger.info(
            "synthesizer.complete",
            merged_count=len(merged_findings),
            needs_debate=sum(1 for f in merged_findings if f.debate_tag == DebateTag.NEEDS_DEBATE),
            auto_confirmed=sum(1 for f in merged_findings if f.debate_tag == DebateTag.AUTO_CONFIRMED),
        )

        return merged_findings

    def _apply_purpose_aware_adjustments(
        self, finding: Finding, intent: IntentProfile
    ) -> None:
        """Apply purpose-aware adjustments to severity and confidence."""
        pa = finding.purpose_aware_assessment

        # If intended capability AND controls exist → reduce severity
        if pa.is_intended_capability and pa.isolation_controls_present:
            if finding.severity == Severity.CRITICAL:
                finding.severity = Severity.MEDIUM
            elif finding.severity == Severity.HIGH:
                finding.severity = Severity.LOW

        # If in sensitive path → boost priority
        for path in finding.affected_files:
            if any(sp in path for sp in intent.sensitive_paths):
                finding.confidence = min(finding.confidence + 0.1, 0.99)
                break

    def _tag_for_debate(self, finding: Finding) -> None:
        """Tag findings for debate routing.

        - Catastrophic (Critical severity OR System blast radius) → needs_debate
        - High → needs_debate
        - Medium with low confidence → needs_debate
        - Medium with high confidence + 2+ agents → auto_confirmed
        - Low with 2+ agents → auto_confirmed
        - Low with 1 agent → informational
        """
        agent_count = len(finding.reviewing_agents)

        if finding.severity == Severity.CRITICAL or finding.blast_radius == BlastRadius.SYSTEM:
            finding.debate_tag = DebateTag.NEEDS_DEBATE
        elif finding.severity == Severity.HIGH:
            finding.debate_tag = DebateTag.NEEDS_DEBATE
        elif finding.severity == Severity.MEDIUM:
            if finding.confidence >= 0.8 and agent_count >= 2:
                finding.debate_tag = DebateTag.AUTO_CONFIRMED
                finding.status = FindingStatus.LIKELY
            else:
                finding.debate_tag = DebateTag.NEEDS_DEBATE
        elif finding.severity == Severity.LOW:
            if agent_count >= 2:
                finding.debate_tag = DebateTag.AUTO_CONFIRMED
                finding.status = FindingStatus.LIKELY
            else:
                finding.debate_tag = DebateTag.INFORMATIONAL
