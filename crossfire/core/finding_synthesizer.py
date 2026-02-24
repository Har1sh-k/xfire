"""Finding synthesizer — merge, dedupe, and score findings from all agents."""

from __future__ import annotations

import structlog

from crossfire.core.models import (
    AgentReview,
    BlastRadius,
    DebateTag,
    Evidence,
    Finding,
    FindingCategory,
    FindingStatus,
    IntentProfile,
    Severity,
)

logger = structlog.get_logger()

# Categories that represent architectural/design issues, not exploitable vulns
_ARCHITECTURAL_CATEGORIES = frozenset({
    FindingCategory.MISSING_RATE_LIMIT,
    FindingCategory.ERROR_SWALLOWING,
    FindingCategory.CONNECTION_LEAK,
})

# Severity ordering for comparisons
SEVERITY_ORDER = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
}

# Debate budget caps by total changed lines in the PR
_BUDGET_CAPS = [
    (20, 2),
    (100, 6),
    (500, 12),
]
_BUDGET_CAP_DEFAULT = 20


def compute_debate_budget(changed_lines: int) -> int:
    """Return the max debate rounds for a PR based on changed lines."""
    for threshold, cap in _BUDGET_CAPS:
        if changed_lines <= threshold:
            return cap
    return _BUDGET_CAP_DEFAULT


def merge_severity(severities: list[Severity]) -> Severity:
    """Deterministic severity merge: Critical wins outright, else median."""
    if not severities:
        return Severity.MEDIUM
    if Severity.CRITICAL in severities:
        return Severity.CRITICAL
    ordered = sorted(severities, key=lambda s: SEVERITY_ORDER[s])
    return ordered[len(ordered) // 2]


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

        # 1b. Filter out architectural/design findings and intended capabilities
        all_findings = self._filter_non_exploitable(all_findings, intent)
        if not all_findings:
            logger.info("synthesizer.all_filtered")
            return []

        # 2. Cluster similar findings (union-find for transitive grouping)
        n = len(all_findings)
        parent = list(range(n))

        def _find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(a: int, b: int) -> None:
            ra, rb = _find(a), _find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(n):
            for j in range(i + 1, n):
                if _is_similar_finding(all_findings[i], all_findings[j]):
                    _union(i, j)

        cluster_map: dict[int, list[Finding]] = {}
        for i in range(n):
            root = _find(i)
            cluster_map.setdefault(root, []).append(all_findings[i])

        clusters = list(cluster_map.values())

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

        # 6. Tag for debate routing (evidence-driven)
        all_agent_names = list(dict.fromkeys(r.agent_name for r in reviews))
        for finding in merged_findings:
            self._tag_for_debate(finding, all_agent_names, reviews)

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

    def _filter_non_exploitable(
        self, findings: list[Finding], intent: IntentProfile,
    ) -> list[Finding]:
        """Drop findings that are architectural design flaws or intended capabilities.

        Layer 2 of the two-layer filter (Layer 1 is the review prompt).
        """
        kept: list[Finding] = []
        for finding in findings:
            # Drop architectural categories
            if finding.category in _ARCHITECTURAL_CATEGORIES:
                logger.info(
                    "synthesizer.filtered_architectural",
                    title=finding.title,
                    category=finding.category.value,
                )
                continue

            # Drop intended capabilities with controls present
            pa = finding.purpose_aware_assessment
            if pa.is_intended_capability and pa.isolation_controls_present:
                logger.info(
                    "synthesizer.filtered_intended_capability",
                    title=finding.title,
                )
                continue

            kept.append(finding)

        dropped = len(findings) - len(kept)
        if dropped:
            logger.info("synthesizer.filter_complete", dropped=dropped, kept=len(kept))
        return kept

    def _apply_purpose_aware_adjustments(
        self, finding: Finding, intent: IntentProfile
    ) -> None:
        """Apply purpose-aware adjustments to severity and confidence."""
        # If in sensitive path → boost priority
        for path in finding.affected_files:
            if any(sp in path for sp in intent.sensitive_paths):
                finding.confidence = min(finding.confidence + 0.1, 0.99)
                break

    def _tag_for_debate(
        self,
        finding: Finding,
        all_agent_names: list[str],
        reviews: list[AgentReview],
    ) -> None:
        """Tag findings for debate routing using evidence-driven logic.

        Routing table (3-agent mode):
        - All agents found, agree on severity → auto_confirmed
        - All agents found, disagree severity → auto_confirmed (merge rule)
        - 2 of 3 found, no silent dissent → auto_confirmed
        - 2 of 3 found, with silent dissent → needs_debate
        - 1 of 3 found → needs_debate

        2-agent / 1-agent fallbacks handled similarly.
        """
        finders = finding.reviewing_agents
        total_agents = len(all_agent_names)
        finder_count = len(finders)
        missers = [a for a in all_agent_names if a not in finders]

        if total_agents == 0:
            finding.debate_tag = DebateTag.INFORMATIONAL
            return

        # 1-agent mode (only 1 total agent ran) — insufficient corroboration
        if total_agents == 1:
            finding.debate_tag = DebateTag.INFORMATIONAL
            finding.status = FindingStatus.UNCLEAR
            return

        # All agents found it → auto-confirm (severity via merge rule)
        if finder_count >= total_agents:
            finding.debate_tag = DebateTag.AUTO_CONFIRMED
            finding.status = FindingStatus.CONFIRMED
            return

        # 2+ agents found it out of 3+
        if finder_count >= 2:
            # Check for silent dissent from the missing agent
            has_dissent = self._check_silent_dissent(finding, missers, reviews)
            if has_dissent:
                finding.debate_tag = DebateTag.NEEDS_DEBATE
            else:
                finding.debate_tag = DebateTag.AUTO_CONFIRMED
                finding.status = FindingStatus.LIKELY
            return

        # 1 found, 1+ missed → needs debate
        finding.debate_tag = DebateTag.NEEDS_DEBATE

    def _check_silent_dissent(
        self,
        finding: Finding,
        missing_agents: list[str],
        reviews: list[AgentReview],
    ) -> bool:
        """Check if any missing agent explicitly analyzed and dismissed this area.

        Returns True if a missing agent's review contains a rejected finding
        that overlaps on affected_files and line_ranges, indicating informed
        dissent rather than ignorance.
        """
        finding_files = set(finding.affected_files)

        for review in reviews:
            if review.agent_name not in missing_agents:
                continue

            # Check for rejected findings that overlap
            for rf in review.findings:
                if rf.status != FindingStatus.REJECTED:
                    continue
                if set(rf.affected_files) & finding_files:
                    if _lines_overlap(finding, rf):
                        logger.info(
                            "synthesizer.silent_dissent_detected",
                            finding=finding.title,
                            dissenting_agent=review.agent_name,
                        )
                        return True

            # Check if the review's risk assessment mentions the same files
            if review.overall_risk_assessment:
                for fp in finding.affected_files:
                    basename = fp.rsplit("/", 1)[-1]
                    if basename in review.overall_risk_assessment:
                        logger.info(
                            "synthesizer.silent_dissent_detected",
                            finding=finding.title,
                            dissenting_agent=review.agent_name,
                            reason="file mentioned in risk assessment",
                        )
                        return True

        return False
