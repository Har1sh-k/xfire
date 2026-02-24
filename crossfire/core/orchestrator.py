"""Main pipeline orchestrator for CrossFire."""

from __future__ import annotations

from crossfire.config.settings import CrossFireSettings
from crossfire.core.models import CrossFireReport, IntentProfile, PRContext


class CrossFireOrchestrator:
    """Orchestrates the full CrossFire analysis pipeline."""

    def __init__(self, settings: CrossFireSettings) -> None:
        self.settings = settings

    async def analyze_pr(
        self,
        repo: str,
        pr_number: int,
        github_token: str,
        skip_debate: bool = False,
    ) -> CrossFireReport:
        """Analyze a GitHub PR through the full pipeline."""
        raise NotImplementedError("PR analysis pipeline not yet implemented")

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
        raise NotImplementedError("Diff analysis pipeline not yet implemented")
