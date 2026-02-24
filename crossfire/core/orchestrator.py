"""Main pipeline orchestrator for CrossFire.

Ties together context building, intent inference, skills, agent reviews,
finding synthesis, adversarial debate, policy enforcement, and reporting
into a single end-to-end pipeline.
"""

from __future__ import annotations

import time

import structlog

from crossfire.agents.debate_engine import DebateEngine
from crossfire.agents.review_engine import ReviewEngine
from crossfire.config.settings import CrossFireSettings
from crossfire.core.context_builder import ContextBuilder
from crossfire.core.finding_synthesizer import FindingSynthesizer
from crossfire.core.intent_inference import IntentInferrer
from crossfire.core.models import (
    CrossFireReport,
    DebateRecord,
    DebateTag,
    Finding,
    IntentProfile,
    PRContext,
)
from crossfire.core.policy_engine import PolicyEngine
from crossfire.skills.code_navigation import CodeNavigationSkill
from crossfire.skills.config_analysis import ConfigAnalysisSkill
from crossfire.skills.data_flow_tracing import DataFlowTracingSkill
from crossfire.skills.dependency_analysis import DependencyAnalysisSkill
from crossfire.skills.git_archeology import GitArcheologySkill
from crossfire.skills.test_coverage_check import TestCoverageCheckSkill

logger = structlog.get_logger()


class CrossFireOrchestrator:
    """Orchestrates the full CrossFire analysis pipeline."""

    def __init__(self, settings: CrossFireSettings) -> None:
        self.settings = settings
        self.context_builder = ContextBuilder(settings.analysis)
        self.intent_inferrer = IntentInferrer(settings.repo)
        self.review_engine = ReviewEngine(settings)
        self.finding_synthesizer = FindingSynthesizer()
        self.debate_engine = DebateEngine(settings)
        self.policy_engine = PolicyEngine(settings.suppressions)

    async def analyze_pr(
        self,
        repo: str,
        pr_number: int,
        github_token: str,
        skip_debate: bool = False,
    ) -> CrossFireReport:
        """Analyze a GitHub PR through the full pipeline."""
        start_time = time.monotonic()

        # 1. Build context
        logger.info("pipeline.context_building", repo=repo, pr=pr_number)
        context = await self.context_builder.build_from_github_pr(
            repo=repo,
            pr_number=pr_number,
            github_token=github_token,
        )

        # Run the common pipeline
        report = await self._run_pipeline(context, skip_debate)
        report.review_duration_seconds = time.monotonic() - start_time

        return report

    async def analyze_diff(
        self,
        repo_dir: str,
        patch_path: str | None = None,
        staged: bool = False,
        base_ref: str | None = None,
        head_ref: str | None = None,
        skip_debate: bool = False,
    ) -> CrossFireReport:
        """Analyze a local diff through the full pipeline."""
        start_time = time.monotonic()

        # 1. Build context based on input mode
        logger.info("pipeline.context_building", repo_dir=repo_dir, mode="local")

        if patch_path:
            context = self.context_builder.build_from_patch_file(patch_path, repo_dir)
        elif staged:
            context = self.context_builder.build_from_staged(repo_dir)
        elif base_ref and head_ref:
            context = self.context_builder.build_from_refs(repo_dir, base_ref, head_ref)
        else:
            context = self.context_builder.build_from_staged(repo_dir)

        # Run the common pipeline
        report = await self._run_pipeline(context, skip_debate)
        report.review_duration_seconds = time.monotonic() - start_time

        return report

    async def _run_pipeline(
        self,
        context: PRContext,
        skip_debate: bool = False,
    ) -> CrossFireReport:
        """Run the common analysis pipeline on a PRContext."""

        logger.info(
            "pipeline.context_ready",
            files_changed=len(context.files),
            repo=context.repo_name,
        )

        # 2. Infer intent
        logger.info("pipeline.intent_inference")
        intent = self.intent_inferrer.infer(context)
        logger.info(
            "pipeline.intent_ready",
            purpose=intent.repo_purpose[:100],
            capabilities=len(intent.intended_capabilities),
            controls=len(intent.security_controls_detected),
        )

        # 3. Run skills (pre-compute for agent context)
        logger.info("pipeline.skills_running")
        skill_outputs = self._run_skills(context, intent)
        logger.info("pipeline.skills_complete", skills=list(skill_outputs.keys()))

        # 4. Independent agent reviews (parallel)
        logger.info("pipeline.agent_reviews")
        reviews = await self.review_engine.run_independent_reviews(context, intent, skill_outputs)
        logger.info(
            "pipeline.reviews_complete",
            agent_count=len(reviews),
            total_findings=sum(len(r.findings) for r in reviews),
        )

        if len(reviews) < 2:
            logger.warning("pipeline.insufficient_reviews", count=len(reviews))

        # 5. Synthesize findings
        logger.info("pipeline.synthesizing")
        findings = self.finding_synthesizer.synthesize(reviews, intent)
        logger.info(
            "pipeline.synthesis_complete",
            merged_findings=len(findings),
        )

        # 6. Adversarial debate (for findings tagged needs_debate)
        debates: list[DebateRecord] = []
        if not skip_debate:
            debate_findings = [f for f in findings if f.debate_tag == DebateTag.NEEDS_DEBATE]
            if debate_findings:
                logger.info("pipeline.debate_starting", count=len(debate_findings))
                debate_results = await self.debate_engine.debate_all(
                    findings=debate_findings,
                    context=context,
                    intent=intent,
                )
                debates = [dr for _, dr in debate_results]
                logger.info("pipeline.debate_complete", debates=len(debates))
        else:
            logger.info("pipeline.debate_skipped")

        # 7. Apply policy (suppressions, waivers)
        findings = self.policy_engine.apply(findings)

        # 8. Determine overall risk
        overall_risk = self._compute_overall_risk(findings)

        # 9. Build report
        agents_used = [r.agent_name for r in reviews]
        summary = self._build_summary(findings, reviews, debates)

        return CrossFireReport(
            repo_name=context.repo_name,
            pr_number=context.pr_number,
            pr_title=context.pr_title,
            context=context,
            intent=intent,
            agent_reviews=reviews,
            findings=findings,
            debates=debates,
            overall_risk=overall_risk,
            summary=summary,
            agents_used=agents_used,
        )

    def _run_skills(self, context: PRContext, intent: IntentProfile) -> dict[str, str]:
        """Run all enabled skills and return their outputs as strings."""
        outputs: dict[str, str] = {}
        changed_files = [f.path for f in context.files]

        # We don't have a repo_dir for GitHub PRs, so skills that need it
        # will work with limited capability. For local diffs, we pass repo_dir.
        repo_dir = "."  # default for GitHub PRs

        if self.settings.skills.data_flow_tracing:
            try:
                skill = DataFlowTracingSkill()
                result = skill.execute(repo_dir, changed_files)
                outputs["data_flow"] = result.summary
            except Exception as e:
                logger.warning("skill.error", skill="data_flow_tracing", error=str(e))
                outputs["data_flow"] = "Not available"

        if self.settings.skills.git_archeology:
            try:
                skill = GitArcheologySkill()
                result = skill.execute(repo_dir, changed_files)
                outputs["git_history"] = result.summary
            except Exception as e:
                logger.warning("skill.error", skill="git_archeology", error=str(e))
                outputs["git_history"] = "Not available"

        if self.settings.skills.config_analysis:
            try:
                skill = ConfigAnalysisSkill()
                result = skill.execute(repo_dir, changed_files)
                outputs["config_analysis"] = result.summary
            except Exception as e:
                logger.warning("skill.error", skill="config_analysis", error=str(e))
                outputs["config_analysis"] = "Not available"

        if self.settings.skills.dependency_analysis:
            try:
                skill = DependencyAnalysisSkill()
                result = skill.execute(
                    repo_dir, changed_files,
                    file_contexts=context.files,
                )
                outputs["dependency_diff"] = result.summary
            except Exception as e:
                logger.warning("skill.error", skill="dependency_analysis", error=str(e))
                outputs["dependency_diff"] = "Not available"

        if self.settings.skills.test_coverage_check:
            try:
                skill = TestCoverageCheckSkill()
                result = skill.execute(repo_dir, changed_files)
                outputs["test_coverage"] = result.summary
            except Exception as e:
                logger.warning("skill.error", skill="test_coverage", error=str(e))
                outputs["test_coverage"] = "Not available"

        if self.settings.skills.code_navigation:
            try:
                skill = CodeNavigationSkill()
                result = skill.execute(repo_dir, changed_files)
                outputs["code_navigation"] = result.summary
            except Exception as e:
                logger.warning("skill.error", skill="code_navigation", error=str(e))
                outputs["code_navigation"] = "Not available"

        return outputs

    def _compute_overall_risk(self, findings: list[Finding]) -> str:
        """Compute overall risk level from findings."""
        from crossfire.core.models import FindingStatus, Severity

        active = [
            f for f in findings
            if f.status in (FindingStatus.CONFIRMED, FindingStatus.LIKELY, FindingStatus.UNCLEAR)
        ]

        if not active:
            return "none"

        severities = [f.severity for f in active]
        if Severity.CRITICAL in severities:
            return "critical"
        if Severity.HIGH in severities:
            return "high"
        if Severity.MEDIUM in severities:
            return "medium"
        return "low"

    def _build_summary(
        self,
        findings: list[Finding],
        reviews: list,
        debates: list[DebateRecord],
    ) -> str:
        """Build a human-readable summary."""
        from crossfire.core.models import FindingStatus

        confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
        likely = [f for f in findings if f.status == FindingStatus.LIKELY]
        unclear = [f for f in findings if f.status == FindingStatus.UNCLEAR]
        rejected = [f for f in findings if f.status == FindingStatus.REJECTED]

        parts = [
            f"{len(confirmed)} confirmed, {len(likely)} likely, "
            f"{len(unclear)} unclear, {len(rejected)} rejected",
            f"from {len(reviews)} agent(s) with {len(debates)} debate(s)",
        ]

        return " | ".join(parts)
