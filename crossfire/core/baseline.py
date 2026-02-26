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
    ) -> Baseline:
        """Build baseline from the whole-repo context and write all files.

        Acquires a lock file to prevent concurrent builds.
        """
        lock_path = self.baseline_dir / SCAN_STATE_LOCK
        self.baseline_dir.mkdir(parents=True, exist_ok=True)

        # Write PID lock
        lock_path.write_text(str(os.getpid()))
        try:
            return self._do_build(settings, head_commit)
        finally:
            lock_path.unlink(missing_ok=True)

    def _do_build(
        self,
        settings: CrossFireSettings | None = None,
        head_commit: str = "",
    ) -> Baseline:
        """Internal build logic — runs intent inference on whole repo."""
        from crossfire.core.context_builder import (
            ContextBuilder,
            _collect_config_files,
            _collect_manifest_files,
            _get_directory_structure,
            _read_file_safe,
        )

        logger.info("baseline.build.start", repo_dir=self.repo_dir)

        # Resolve head commit
        if not head_commit:
            head_commit = self._get_head_commit()

        # Build a synthetic PRContext from the whole repo (no diff)
        from crossfire.core.models import PRContext

        repo_dir = self.repo_dir
        readme = _read_file_safe(os.path.join(repo_dir, "README.md"))
        config_files: dict[str, str] = {}
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

        # Run intent inference
        repo_config = settings.repo if settings else None
        inferrer = IntentInferrer(repo_config)
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
                text=True,
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
