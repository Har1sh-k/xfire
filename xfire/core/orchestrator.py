"""Main pipeline orchestrator for CrossFire.

Ties together context building, intent inference, skills, agent reviews,
finding synthesis, adversarial debate, policy enforcement, and reporting
into a single end-to-end pipeline.
"""

from __future__ import annotations

import asyncio
import time

import structlog

from xfire.agents.debate_engine import DebateEngine
from xfire.agents.review_engine import ReviewEngine
from xfire.config.settings import CrossFireSettings
from xfire.core.context_builder import ContextBuilder
from xfire.core.finding_synthesizer import FindingSynthesizer
from xfire.core.intent_inference import IntentInferrer
from xfire.core.models import (
    CrossFireReport,
    DebateRecord,
    DebateTag,
    Finding,
    FindingStatus,
    IntentProfile,
    PRContext,
)
from xfire.core.policy_engine import PolicyEngine
from xfire.skills.code_navigation import CodeNavigationSkill
from xfire.skills.config_analysis import ConfigAnalysisSkill
from xfire.skills.data_flow_tracing import DataFlowTracingSkill
from xfire.skills.dependency_analysis import DependencyAnalysisSkill
from xfire.skills.git_archeology import GitArcheologySkill
from xfire.skills.test_coverage_check import TestCoverageCheckSkill

logger = structlog.get_logger()


class CrossFireOrchestrator:
    """Orchestrates the full CrossFire analysis pipeline."""

    def __init__(
        self, settings: CrossFireSettings, cache_dir: str | None = None,
    ) -> None:
        self.settings = settings
        self.cache_dir = cache_dir
        self.context_builder = ContextBuilder(settings.analysis)
        self.intent_inferrer = IntentInferrer(settings.repo)
        self.review_engine = ReviewEngine(settings)
        self.finding_synthesizer = FindingSynthesizer()
        self.debate_engine = DebateEngine(settings)
        self.policy_engine = PolicyEngine(settings.suppressions)

        # Build the Claude agent used for LLM-based intent/threat-model inference
        from xfire.agents.claude_adapter import ClaudeAgent
        self._intent_agent = (
            ClaudeAgent(settings.agents["claude"])
            if settings.agents.get("claude") and settings.agents["claude"].enabled
            else None
        )

    async def analyze_pr(
        self,
        repo: str,
        pr_number: int,
        github_token: str,
        skip_debate: bool = False,
    ) -> CrossFireReport:
        """Analyze a GitHub PR through the full pipeline."""
        from xfire.core.cache import (
            load_cached_context,
            save_context_cache,
        )

        start_time = time.monotonic()

        # 1. Try loading context from cache (keyed on head SHA)
        context = None
        if self.cache_dir:
            from xfire.integrations.github.pr_loader import fetch_pr_shas

            head_sha, base_sha = await fetch_pr_shas(repo, pr_number, github_token)
            if head_sha:
                context = load_cached_context(self.cache_dir, pr_number, head_sha)

        # 2. Build context from GitHub API on cache miss
        if context is None:
            logger.info("pipeline.context_building", repo=repo, pr=pr_number)
            context = await self.context_builder.build_from_github_pr(
                repo=repo,
                pr_number=pr_number,
                github_token=github_token,
            )
            if self.cache_dir and context.head_sha:
                save_context_cache(
                    self.cache_dir, pr_number, context.head_sha, context,
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

        # Run the common pipeline, passing repo_dir so skills inspect the correct directory
        report = await self._run_pipeline(context, skip_debate, repo_dir=repo_dir)
        report.review_duration_seconds = time.monotonic() - start_time

        return report

    async def code_review(
        self,
        repo_dir: str,
        max_files: int = 150,
        skip_debate: bool = False,
    ) -> CrossFireReport:
        """Run a full codebase security audit — no diff, no PR, no commits.

        Reads all source files from the repo directory, runs the full pipeline
        (intent → skills → independent reviews → debate → policy), and returns
        a report covering the entire codebase's security posture.
        """
        from xfire.agents.prompts.review_prompt import (
            CODE_REVIEW_SYSTEM_PROMPT,
            build_code_review_prompt,
        )

        start_time = time.monotonic()

        logger.info("pipeline.code_review_start", repo_dir=repo_dir, max_files=max_files)

        # 1. Build context from whole repo (no diff)
        context = self.context_builder.build_from_repo(repo_dir, max_files=max_files)

        logger.info(
            "pipeline.context_ready",
            files=len(context.files),
            repo=context.repo_name,
        )

        # 2. Intent inference — LLM threat model via Sonnet if available
        from xfire.core.intent_inference import infer_with_llm
        if self._intent_agent is not None:
            logger.info("pipeline.intent_inference", mode="llm_enriched")
            intent = await infer_with_llm(context, self._intent_agent, self.intent_inferrer)
        else:
            logger.info("pipeline.intent_inference", mode="heuristic")
            intent = self.intent_inferrer.infer(context)

        logger.info(
            "pipeline.intent_ready",
            purpose=intent.repo_purpose[:100],
            capabilities=len(intent.intended_capabilities),
            controls=len(intent.security_controls_detected),
        )

        # 3. Skills
        logger.info("pipeline.skills_running")
        skill_outputs = await asyncio.to_thread(
            self._run_skills, context, intent, repo_dir
        )
        logger.info("pipeline.skills_complete", skills=list(skill_outputs.keys()))

        # 4. Independent reviews — use CODE_REVIEW_SYSTEM_PROMPT and
        #    build_code_review_prompt (whole-file, not diff-focused)
        logger.info("pipeline.agent_reviews")

        # Build the review prompt using the whole-repo variant
        from xfire.agents.base import AgentError, BaseAgent
        from xfire.agents.review_engine import AGENT_CLASSES, _create_agent, _parse_finding_from_raw

        user_prompt = build_code_review_prompt(context, intent, skill_outputs)

        agents: list[BaseAgent] = []
        for name, config in self.settings.agents.items():
            if config.enabled:
                try:
                    agents.append(_create_agent(name, config, repo_dir=repo_dir))
                except ValueError as e:
                    logger.warning("agent.create_failed", agent=name, error=str(e))

        import time as _time
        from xfire.core.models import AgentReview, Finding

        async def _dispatch(agent: BaseAgent) -> AgentReview | None:
            t0 = _time.monotonic()
            try:
                raw = await agent.execute(
                    prompt=user_prompt,
                    system_prompt=CODE_REVIEW_SYSTEM_PROMPT,
                )
                parsed = agent.parse_json_response(raw)
                findings: list[Finding] = []
                for rf in parsed.get("findings", []):
                    f = _parse_finding_from_raw(rf, agent.name)
                    if f:
                        findings.append(f)
                return AgentReview(
                    agent_name=agent.name,
                    findings=findings,
                    overall_risk_assessment=parsed.get("overall_risk", "unknown"),
                    review_methodology=parsed.get("risk_summary", ""),
                    review_duration_seconds=_time.monotonic() - t0,
                    thinking_trace=agent.thinking_trace,
                )
            except AgentError as e:
                logger.error("review.agent_error", agent=agent.name, error=str(e))
                return None
            except Exception as e:
                logger.error("review.unexpected_error", agent=agent.name, error=str(e))
                return None

        results = await asyncio.gather(*[_dispatch(a) for a in agents], return_exceptions=True)
        reviews = [r for r in results if isinstance(r, AgentReview) and r is not None]

        logger.info(
            "pipeline.reviews_complete",
            agent_count=len(reviews),
            total_findings=sum(len(r.findings) for r in reviews),
        )

        # 5. Synthesize
        findings = self.finding_synthesizer.synthesize(reviews, intent)

        # 6. Debate
        debates: list[DebateRecord] = []
        if not skip_debate:
            debate_findings = [f for f in findings if f.debate_tag == DebateTag.NEEDS_DEBATE]
            if debate_findings:
                from xfire.core.finding_synthesizer import compute_debate_budget
                budget = compute_debate_budget(
                    sum(len(fc.content or "") // 80 for fc in context.files)
                )
                logger.info("pipeline.debate_starting", count=len(debate_findings), budget=budget)
                debate_results = await self.debate_engine.debate_all(
                    findings=debate_findings,
                    context=context,
                    intent=intent,
                    debate_budget=budget,
                )
                debates = [dr for _, dr in debate_results]

        # 7. Policy
        findings = self.policy_engine.apply(findings)

        overall_risk = self._compute_overall_risk(findings)
        summary = self._build_summary(findings, reviews, debates)

        report = CrossFireReport(
            repo_name=context.repo_name,
            pr_title=context.pr_title,
            context=context,
            intent=intent,
            agent_reviews=reviews,
            findings=findings,
            debates=debates,
            overall_risk=overall_risk,
            summary=summary,
            agents_used=[r.agent_name for r in reviews],
            review_duration_seconds=time.monotonic() - start_time,
        )
        return report

    async def scan_with_baseline(
        self,
        repo_dir: str,
        diff_result: object,
        baseline: object,
        fast_model: object,
        skip_debate: bool = False,
    ) -> CrossFireReport:
        """Run the full analysis pipeline using pre-built baseline context.

        Steps:
          1. Fast model → repo-specific context-aware system prompt
          2. Build PRContext from diff_result
          3. Use baseline.intent (skip intent inference)
          4. Run skills
          5. run_independent_reviews with context_system_prompt
          6. Synthesize findings
          7. filter_known() — split new vs known
          8. Debate only new findings
          9. Apply policy
          10. baseline.update_after_scan()
          11. Return CrossFireReport with known_skipped_count in summary
        """
        from xfire.agents.prompts.context_prompt import build_context_system_prompt
        from xfire.core.baseline import BaselineManager
        from xfire.core.diff_resolver import DiffResult

        start_time = time.monotonic()

        # Type-narrow for local use
        diff_result_typed: DiffResult = diff_result  # type: ignore[assignment]
        baseline_typed = baseline  # type: ignore[assignment]

        # 1. Build repo-specific system prompt from fast model
        diff_summary = diff_result_typed.diff_text[:2000]
        logger.info("pipeline.context_prompt_building")
        context_system_prompt = await build_context_system_prompt(
            baseline_typed, diff_summary, fast_model  # type: ignore[arg-type]
        )

        # 2. Build PRContext from diff
        logger.info(
            "pipeline.context_building",
            repo_dir=repo_dir,
            range=diff_result_typed.commit_range_desc,
        )
        context = self.context_builder.build_from_diff(
            diff_text=diff_result_typed.diff_text,
            repo_dir=repo_dir,
            base_ref=diff_result_typed.base_commit or "HEAD~1",
            pr_title=f"Scan: {diff_result_typed.commit_range_desc}",
        )

        # 3. Use baseline intent directly (no re-inference)
        intent: IntentProfile = baseline_typed.intent

        logger.info(
            "pipeline.intent_from_baseline",
            purpose=intent.repo_purpose[:80],
            capabilities=len(intent.intended_capabilities),
        )

        # 4. Run skills
        logger.info("pipeline.skills_running")
        skill_outputs = await asyncio.to_thread(
            self._run_skills, context, intent, repo_dir
        )
        logger.info("pipeline.skills_complete", skills=list(skill_outputs.keys()))

        # 5. Independent reviews with context-aware system prompt
        logger.info("pipeline.agent_reviews")
        reviews = await self.review_engine.run_independent_reviews(
            context, intent, skill_outputs, system_prompt=context_system_prompt, repo_dir=repo_dir
        )
        logger.info(
            "pipeline.reviews_complete",
            agent_count=len(reviews),
            total_findings=sum(len(r.findings) for r in reviews),
        )

        # 6. Synthesize findings
        logger.info("pipeline.synthesizing")
        all_findings = self.finding_synthesizer.synthesize(reviews, intent)

        # 7. Filter known findings (delta scanning)
        baseline_mgr = BaselineManager(repo_dir)
        new_findings, known_skipped = baseline_mgr.filter_known(all_findings, baseline_typed)
        logger.info(
            "pipeline.delta_filter",
            new=len(new_findings),
            known_skipped=len(known_skipped),
        )

        # 8. Debate only new findings
        debates: list[DebateRecord] = []
        if not skip_debate and new_findings:
            debate_findings = [f for f in new_findings if f.debate_tag == DebateTag.NEEDS_DEBATE]
            if debate_findings:
                from xfire.core.finding_synthesizer import compute_debate_budget

                changed_lines = sum(
                    sum(len(h.added_lines) + len(h.removed_lines) for h in f.diff_hunks)
                    for f in context.files
                )
                budget = compute_debate_budget(changed_lines)
                logger.info(
                    "pipeline.debate_starting",
                    count=len(debate_findings),
                    budget=budget,
                )
                debate_results = await self.debate_engine.debate_all(
                    findings=debate_findings,
                    context=context,
                    intent=intent,
                    debate_budget=budget,
                )
                debates = [dr for _, dr in debate_results]
                logger.info("pipeline.debate_complete", debates=len(debates))
        else:
            logger.info("pipeline.debate_skipped")

        # 9. Apply policy
        new_findings = self.policy_engine.apply(new_findings)

        # 10. Update baseline with confirmed findings
        confirmed_findings = [
            f for f in new_findings
            if f.status in (FindingStatus.CONFIRMED, FindingStatus.LIKELY)
        ]
        baseline_mgr.update_after_scan(diff_result_typed.head_commit, confirmed_findings)

        # 11. Build report
        overall_risk = self._compute_overall_risk(new_findings)
        agents_used = [r.agent_name for r in reviews]
        summary = self._build_scan_summary(new_findings, reviews, debates, len(known_skipped))

        report = CrossFireReport(
            repo_name=context.repo_name,
            pr_title=context.pr_title,
            context=context,
            intent=intent,
            agent_reviews=reviews,
            findings=new_findings,
            debates=debates,
            overall_risk=overall_risk,
            summary=summary,
            agents_used=agents_used,
            review_duration_seconds=time.monotonic() - start_time,
        )
        return report

    def _build_scan_summary(
        self,
        findings: list[Finding],
        reviews: list,
        debates: list[DebateRecord],
        known_skipped_count: int,
    ) -> str:
        """Build scan summary including delta information."""
        confirmed = [f for f in findings if f.status == FindingStatus.CONFIRMED]
        likely = [f for f in findings if f.status == FindingStatus.LIKELY]
        unclear = [f for f in findings if f.status == FindingStatus.UNCLEAR]
        rejected = [f for f in findings if f.status == FindingStatus.REJECTED]

        parts = [
            f"{len(confirmed)} confirmed, {len(likely)} likely, "
            f"{len(unclear)} unclear, {len(rejected)} rejected",
            f"from {len(reviews)} agent(s) with {len(debates)} debate(s)",
            f"{len(confirmed) + len(likely)} new | {known_skipped_count} known skipped",
        ]
        return " | ".join(parts)

    async def _run_pipeline(
        self,
        context: PRContext,
        skip_debate: bool = False,
        repo_dir: str | None = None,
    ) -> CrossFireReport:
        """Run the common analysis pipeline on a PRContext."""
        from xfire.core.cache import (
            load_cached_intent,
            save_intent_cache,
        )

        logger.info(
            "pipeline.context_ready",
            files_changed=len(context.files),
            repo=context.repo_name,
        )

        # 2. Try loading intent from cache (keyed on base SHA)
        intent = None
        if self.cache_dir and context.base_sha:
            intent = load_cached_intent(self.cache_dir, context.base_sha)

        if intent is None:
            from xfire.core.intent_inference import infer_with_llm
            if self._intent_agent is not None:
                logger.info("pipeline.intent_inference", mode="llm_enriched")
                intent = await infer_with_llm(context, self._intent_agent, self.intent_inferrer)
            else:
                logger.info("pipeline.intent_inference", mode="heuristic")
                intent = self.intent_inferrer.infer(context)
            if self.cache_dir and context.base_sha:
                save_intent_cache(self.cache_dir, context.base_sha, intent)

        logger.info(
            "pipeline.intent_ready",
            purpose=intent.repo_purpose[:100],
            capabilities=len(intent.intended_capabilities),
            controls=len(intent.security_controls_detected),
        )

        # 3. Run skills (pre-compute for agent context)
        if repo_dir is not None:
            logger.info("pipeline.skills_running")
            skill_outputs = await asyncio.to_thread(
                self._run_skills, context, intent, repo_dir,
            )
            logger.info("pipeline.skills_complete", skills=list(skill_outputs.keys()))
        else:
            logger.info(
                "pipeline.skills_skipped",
                msg="No local checkout — filesystem skills unavailable",
            )
            skill_outputs = {}

        # 4. Independent agent reviews (parallel)
        logger.info("pipeline.agent_reviews")
        reviews = await self.review_engine.run_independent_reviews(
            context, intent, skill_outputs, repo_dir=repo_dir
        )
        logger.info(
            "pipeline.reviews_complete",
            agent_count=len(reviews),
            total_findings=sum(len(r.findings) for r in reviews),
        )

        enabled_count = sum(1 for c in self.settings.agents.values() if c.enabled)
        if not reviews and enabled_count > 0:
            logger.error(
                "pipeline.all_agents_failed",
                enabled=enabled_count,
                msg="All agents failed — report may be incomplete",
            )
        elif len(reviews) < 2:
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
                # Compute debate budget based on PR size
                from xfire.core.finding_synthesizer import compute_debate_budget

                changed_lines = sum(
                    sum(len(h.added_lines) + len(h.removed_lines) for h in f.diff_hunks)
                    for f in context.files
                )
                budget = compute_debate_budget(changed_lines)
                logger.info(
                    "pipeline.debate_starting",
                    count=len(debate_findings),
                    budget=budget,
                    changed_lines=changed_lines,
                )
                debate_results = await self.debate_engine.debate_all(
                    findings=debate_findings,
                    context=context,
                    intent=intent,
                    debate_budget=budget,
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

    def _run_skills(
        self, context: PRContext, intent: IntentProfile, repo_dir: str = ".",
    ) -> dict[str, str]:
        """Run all enabled skills and return their outputs as strings."""
        outputs: dict[str, str] = {}
        changed_files = [f.path for f in context.files]

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
        from xfire.core.models import FindingStatus, Severity

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
        from xfire.core.models import FindingStatus

        enabled_count = sum(1 for c in self.settings.agents.values() if c.enabled)

        if not reviews and enabled_count > 0:
            return (
                f"WARNING: All {enabled_count} agent(s) failed. "
                "No findings were produced — this does NOT mean the code is safe."
            )

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
