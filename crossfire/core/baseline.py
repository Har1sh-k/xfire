"""Baseline manager for CrossFire — persistent repo context and delta scanning.

Reads/writes .crossfire/baseline/ to enable:
- Baseline context built once from whole-repo analysis
- Delta scanning: skip already-confirmed findings
- Intent-change detection: fast model checks if diff changes security model
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from crossfire.core.intent_inference import IntentInferrer
from crossfire.core.models import Finding, IntentProfile

if TYPE_CHECKING:
    from crossfire.agents.base import BaseAgent
    from crossfire.agents.fast_model import FastModel
    from crossfire.config.settings import CrossFireSettings

logger = structlog.get_logger()

BASELINE_DIR = ".crossfire/baseline"
CONTEXT_MD = "context.md"
INTENT_JSON = "intent.json"
SCAN_STATE_JSON = "scan_state.json"
SCAN_STATE_LOCK = "scan_state.lock"
KNOWN_FINDINGS_JSON = "known_findings.json"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ScanState:
    """Tracks the last scan state for delta scanning."""

    last_scanned_commit: str = ""
    last_scanned_at: str = ""
    baseline_commit: str = ""
    baseline_built_at: str = ""
    total_scans: int = 0

    def to_dict(self) -> dict:
        return {
            "last_scanned_commit": self.last_scanned_commit,
            "last_scanned_at": self.last_scanned_at,
            "baseline_commit": self.baseline_commit,
            "baseline_built_at": self.baseline_built_at,
            "total_scans": self.total_scans,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ScanState:
        return cls(
            last_scanned_commit=data.get("last_scanned_commit", ""),
            last_scanned_at=data.get("last_scanned_at", ""),
            baseline_commit=data.get("baseline_commit", ""),
            baseline_built_at=data.get("baseline_built_at", ""),
            total_scans=data.get("total_scans", 0),
        )


@dataclass
class KnownFinding:
    """A finding confirmed in a previous scan — used for deduplication."""

    fingerprint: str
    title: str
    category: str
    severity: str
    affected_files: list[str] = field(default_factory=list)
    confirmed_at: str = ""
    scan_commit: str = ""

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "title": self.title,
            "category": self.category,
            "severity": self.severity,
            "affected_files": self.affected_files,
            "confirmed_at": self.confirmed_at,
            "scan_commit": self.scan_commit,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnownFinding:
        return cls(
            fingerprint=data.get("fingerprint", ""),
            title=data.get("title", ""),
            category=data.get("category", ""),
            severity=data.get("severity", ""),
            affected_files=data.get("affected_files", []),
            confirmed_at=data.get("confirmed_at", ""),
            scan_commit=data.get("scan_commit", ""),
        )


@dataclass
class Baseline:
    """Complete baseline for a repository."""

    context_md: str
    intent: IntentProfile
    scan_state: ScanState | None = None
    known_findings: list[KnownFinding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# BaselineManager
# ---------------------------------------------------------------------------


class BaselineManager:
    """Manages the .crossfire/baseline/ directory for a repository."""

    def __init__(self, repo_dir: str) -> None:
        self.repo_dir = os.path.abspath(repo_dir)
        self.baseline_dir = Path(self.repo_dir) / BASELINE_DIR

    def exists(self) -> bool:
        """Return True if a baseline has been built for this repo."""
        return (
            (self.baseline_dir / CONTEXT_MD).exists()
            and (self.baseline_dir / INTENT_JSON).exists()
        )

    def build(
        self,
        settings: CrossFireSettings | None = None,
        head_commit: str = "",
        base_ref: str = "",
        agent: "BaseAgent | None" = None,
    ) -> Baseline:
        """Build baseline from the repo context and write all files.

        Args:
            settings: CrossFire settings (for repo config overrides).
            head_commit: The commit SHA to record as the baseline commit.
                         Defaults to current HEAD.
            base_ref: Git ref to read repo content FROM — the state *before*
                      the diff being analysed. When scanning a range like
                      main..feature, pass the base commit so the baseline
                      reflects the repo before the changes under review.
                      Falls back to the working tree when empty.
            agent: Optional LLM agent (Claude Sonnet) for threat-model-quality
                   intent inference. Falls back to heuristics if None.

        Acquires a PID lock file to prevent concurrent baseline rebuilds.
        """
        lock_path = self.baseline_dir / SCAN_STATE_LOCK
        self.baseline_dir.mkdir(parents=True, exist_ok=True)

        # Write PID lock
        lock_path.write_text(str(os.getpid()))
        try:
            return self._do_build(settings, head_commit, base_ref, agent)
        finally:
            lock_path.unlink(missing_ok=True)

    def _do_build(
        self,
        settings: CrossFireSettings | None = None,
        head_commit: str = "",
        base_ref: str = "",
        agent: "BaseAgent | None" = None,
    ) -> Baseline:
        """Internal build logic — runs intent inference on repo at base_ref.

        When base_ref is provided, all key files (README, manifests, config)
        are read from that git ref via `git show`, NOT from the working tree.
        This ensures the baseline reflects the repo state *before* the diff
        being reviewed, regardless of what is currently checked out.
        """
        from crossfire.core.context_builder import (
            _collect_config_files,
            _collect_manifest_files,
            _get_directory_structure,
            _get_file_at_ref,
            _read_file_safe,
        )

        logger.info(
            "baseline.build.start",
            repo_dir=self.repo_dir,
            base_ref=base_ref or "(working tree)",
        )

        # Resolve head commit
        if not head_commit:
            head_commit = base_ref or self._get_head_commit()

        # Build a synthetic PRContext from the repo state at base_ref
        from crossfire.core.models import PRContext

        repo_dir = self.repo_dir

        if base_ref:
            # Read key files at the base git ref so the baseline captures
            # the repo BEFORE the diff, not whatever is checked out now.
            readme = _get_file_at_ref("README.md", base_ref, repo_dir)

            # Read manifest/config files from the ref using git show
            config_files = _collect_config_files_at_ref(repo_dir, base_ref)

            # Directory structure from the ref's tree
            directory_structure = _get_directory_structure_at_ref(repo_dir, base_ref)
        else:
            # No ref given (e.g. crossfire baseline .) — use working tree
            readme = _read_file_safe(os.path.join(repo_dir, "README.md"))
            config_files = {}
            config_files.update(_collect_config_files(repo_dir))
            config_files.update(_collect_manifest_files(repo_dir))
            directory_structure = _get_directory_structure(repo_dir)

        repo_name = Path(repo_dir).name
        context = PRContext(
            repo_name=repo_name,
            pr_title="Baseline context build",
            readme_content=readme,
            config_files=config_files,
            directory_structure=directory_structure,
        )

        # Run intent inference — LLM (threat model) if agent provided, else heuristic
        import asyncio as _asyncio

        from crossfire.core.intent_inference import infer_with_llm

        repo_config = settings.repo if settings else None
        inferrer = IntentInferrer(repo_config)

        if agent is not None:
            logger.info("baseline.intent_llm", agent=getattr(agent, "name", "?"))
            intent = _asyncio.run(infer_with_llm(context, agent))
        else:
            logger.info("baseline.intent_heuristic")
            intent = inferrer.infer(context)

        # Build context.md
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        context_md = _build_context_md(intent, head_commit, now)

        # Write all files
        self.baseline_dir.mkdir(parents=True, exist_ok=True)
        (self.baseline_dir / CONTEXT_MD).write_text(context_md, encoding="utf-8")
        (self.baseline_dir / INTENT_JSON).write_text(
            intent.model_dump_json(indent=2), encoding="utf-8"
        )

        # Write initial scan_state
        scan_state = ScanState(
            baseline_commit=head_commit,
            baseline_built_at=datetime.now(timezone.utc).isoformat(),
        )
        (self.baseline_dir / SCAN_STATE_JSON).write_text(
            json.dumps(scan_state.to_dict(), indent=2), encoding="utf-8"
        )

        # Write empty known_findings
        known_findings_path = self.baseline_dir / KNOWN_FINDINGS_JSON
        if not known_findings_path.exists():
            known_findings_path.write_text("[]", encoding="utf-8")

        logger.info(
            "baseline.build.complete",
            commit=head_commit,
            base_ref=base_ref or "(working tree)",
            capabilities=len(intent.intended_capabilities),
            controls=len(intent.security_controls_detected),
        )

        return Baseline(
            context_md=context_md,
            intent=intent,
            scan_state=scan_state,
            known_findings=[],
        )

    def load(self) -> Baseline:
        """Load baseline from .crossfire/baseline/."""
        if not self.exists():
            raise FileNotFoundError(
                f"No baseline found at {self.baseline_dir}. "
                "Run `crossfire baseline .` first."
            )

        context_md = (self.baseline_dir / CONTEXT_MD).read_text(encoding="utf-8")

        intent_data = json.loads(
            (self.baseline_dir / INTENT_JSON).read_text(encoding="utf-8")
        )
        intent = IntentProfile(**intent_data)

        scan_state: ScanState | None = None
        scan_state_path = self.baseline_dir / SCAN_STATE_JSON
        if scan_state_path.exists():
            scan_state = ScanState.from_dict(
                json.loads(scan_state_path.read_text(encoding="utf-8"))
            )

        known_findings: list[KnownFinding] = []
        kf_path = self.baseline_dir / KNOWN_FINDINGS_JSON
        if kf_path.exists():
            raw = json.loads(kf_path.read_text(encoding="utf-8"))
            known_findings = [KnownFinding.from_dict(f) for f in raw]

        return Baseline(
            context_md=context_md,
            intent=intent,
            scan_state=scan_state,
            known_findings=known_findings,
        )

    async def check_intent_changed(
        self,
        diff_text: str,
        fast_model: FastModel,
    ) -> bool:
        """Check if the diff materially changes the repo's security model.

        Uses the fast model (claude-haiku) for a cheap, quick check.
        Returns True if baseline needs rebuild.
        """
        from crossfire.agents.prompts.context_prompt import check_intent_changed

        if not self.exists():
            return True

        baseline = self.load()
        return await check_intent_changed(diff_text, baseline, fast_model)

    def update_after_scan(
        self,
        head_commit: str,
        confirmed_findings: list[Finding],
    ) -> None:
        """Update scan_state.json and merge confirmed findings into known_findings.json.

        Auto-called after every successful scan.
        """
        self.baseline_dir.mkdir(parents=True, exist_ok=True)

        # Update scan state
        scan_state_path = self.baseline_dir / SCAN_STATE_JSON
        if scan_state_path.exists():
            scan_state = ScanState.from_dict(
                json.loads(scan_state_path.read_text(encoding="utf-8"))
            )
        else:
            scan_state = ScanState()

        scan_state.last_scanned_commit = head_commit
        scan_state.last_scanned_at = datetime.now(timezone.utc).isoformat()
        scan_state.total_scans += 1

        scan_state_path.write_text(
            json.dumps(scan_state.to_dict(), indent=2), encoding="utf-8"
        )

        # Merge confirmed findings into known_findings
        kf_path = self.baseline_dir / KNOWN_FINDINGS_JSON
        existing: dict[str, KnownFinding] = {}
        if kf_path.exists():
            for kf in [KnownFinding.from_dict(f)
                       for f in json.loads(kf_path.read_text(encoding="utf-8"))]:
                existing[kf.fingerprint] = kf

        from crossfire.core.models import FindingStatus
        now = datetime.now(timezone.utc).isoformat()

        for finding in confirmed_findings:
            if finding.status not in (FindingStatus.CONFIRMED, FindingStatus.LIKELY):
                continue
            fp = self._fingerprint(finding)
            if fp not in existing:
                existing[fp] = KnownFinding(
                    fingerprint=fp,
                    title=finding.title,
                    category=finding.category.value,
                    severity=finding.severity.value,
                    affected_files=finding.affected_files,
                    confirmed_at=now,
                    scan_commit=head_commit,
                )

        kf_path.write_text(
            json.dumps([kf.to_dict() for kf in existing.values()], indent=2),
            encoding="utf-8",
        )

        logger.info(
            "baseline.updated",
            commit=head_commit,
            total_known=len(existing),
            new_confirmed=len(confirmed_findings),
        )

    def filter_known(
        self,
        findings: list[Finding],
        baseline: Baseline,
    ) -> tuple[list[Finding], list[Finding]]:
        """Split findings into (new_findings, known_skipped).

        Known findings are those with a matching fingerprint in known_findings.json.
        """
        known_fps = {kf.fingerprint for kf in baseline.known_findings}

        new_findings: list[Finding] = []
        known_skipped: list[Finding] = []

        for finding in findings:
            fp = self._fingerprint(finding)
            if fp in known_fps:
                known_skipped.append(finding)
            else:
                new_findings.append(finding)

        return new_findings, known_skipped

    def _fingerprint(self, finding: Finding) -> str:
        """Generate a stable fingerprint for deduplication.

        sha256(category:primary_file:title[:50])[:16]
        """
        primary_file = (
            sorted(finding.affected_files)[0] if finding.affected_files else ""
        )
        raw = f"{finding.category.value}:{primary_file}:{finding.title[:50]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _get_head_commit(self) -> str:
        """Get current HEAD commit SHA."""
        import subprocess

        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_dir,
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_config_files_at_ref(repo_dir: str, ref: str) -> dict[str, str]:
    """Read manifest and config files from a git ref using git show.

    Falls back silently for any file that doesn't exist at that ref.
    """
    import subprocess

    from crossfire.core.context_builder import MANIFEST_FILES, SECURITY_CONFIG_PATTERNS

    result: dict[str, str] = {}

    # Collect all non-glob filenames to try
    candidates: list[str] = list(MANIFEST_FILES)
    for pattern in SECURITY_CONFIG_PATTERNS:
        if "*" not in pattern:
            candidates.append(pattern)

    for filename in candidates:
        try:
            proc = subprocess.run(
                ["git", "show", f"{ref}:{filename}"],
                cwd=repo_dir,
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout:
                result[filename] = proc.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    return result


def _get_directory_structure_at_ref(repo_dir: str, ref: str) -> str:
    """Get a tree-like directory listing from a git ref via git ls-tree.

    Falls back to the working tree structure if the ref is unavailable.
    """
    import subprocess

    from crossfire.core.context_builder import _get_directory_structure

    try:
        proc = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", ref],
            cwd=repo_dir,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=15,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return _get_directory_structure(repo_dir)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return _get_directory_structure(repo_dir)

    # Convert flat file list to a tree-like structure (first 200 entries)
    paths = [p for p in proc.stdout.strip().splitlines() if p][:200]
    lines: list[str] = [f"{Path(repo_dir).name}/"]
    for path in paths:
        parts = path.split("/")
        indent = "  " * (len(parts) - 1)
        lines.append(f"{indent}{'└── ' if len(parts) > 1 else '├── '}{parts[-1]}")
    return "\n".join(lines)


def _build_context_md(intent: IntentProfile, commit: str, date: str) -> str:
    """Build the human-readable context.md from an IntentProfile."""
    lines: list[str] = [
        "# Repository Context",
        f"_Generated by CrossFire at commit {commit or 'unknown'} on {date}_",
        "",
        "## Purpose",
        intent.repo_purpose or "Unknown",
        "",
        "## Intended Capabilities",
    ]
    for cap in intent.intended_capabilities:
        lines.append(f"- {cap}")
    if not intent.intended_capabilities:
        lines.append("- (none detected)")

    lines += ["", "## Trust Boundaries"]
    for tb in intent.trust_boundaries:
        lines.append(f"- {tb.name}: {tb.description}")
    if not intent.trust_boundaries:
        lines.append("- (none detected)")

    lines += ["", "## Security Controls Detected"]
    for sc in intent.security_controls_detected:
        lines.append(f"- {sc.control_type} in {sc.location}")
    if not intent.security_controls_detected:
        lines.append("- (none detected)")

    lines += ["", "## Sensitive Paths"]
    for sp in intent.sensitive_paths:
        lines.append(f"- {sp}")
    if not intent.sensitive_paths:
        lines.append("- (none detected)")

    return "\n".join(lines) + "\n"
