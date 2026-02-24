"""Dependency analysis skill — analyze manifest/lockfile changes."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from crossfire.skills.base import BaseSkill, SkillResult


class ManifestDiff(BaseModel):
    """Differences between base and head manifest files."""

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)  # "pkg: old_ver -> new_ver"


class RiskyDep(BaseModel):
    """A dependency flagged as potentially risky."""

    package_name: str
    reason: str
    severity: str = "medium"


# Known risky or suspicious package patterns
RISKY_PACKAGE_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern, reason, severity)
    (r"^(event-stream|flatmap-stream|ua-parser-js)$",
     "Known compromised package in past supply chain attack", "high"),
    (r"^(colors|faker)$",
     "Package with known protest/sabotage release", "medium"),
    (r"^node-ipc$",
     "Package with known protestware incident", "high"),
    (r".*exec.*|.*shell.*|.*spawn.*",
     "Package name suggests command execution capabilities", "low"),
    (r".*crypto.*mine.*|.*coinhive.*",
     "Package name suggests cryptocurrency mining", "high"),
    (r".*keylog.*|.*keystroke.*",
     "Package name suggests keystroke logging", "critical"),
    (r".*reverse.?shell.*",
     "Package name suggests reverse shell capability", "critical"),
]


def _parse_requirements_txt(content: str) -> dict[str, str]:
    """Parse requirements.txt into {package: version} dict."""
    deps: dict[str, str] = {}
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Handle: package==1.0, package>=1.0, package
        match = re.match(r"^([\w.-]+)\s*([><=!~]+\s*[\w.*]+)?", line)
        if match:
            deps[match.group(1).lower()] = (match.group(2) or "").strip()
    return deps


def _parse_package_json_deps(content: str) -> dict[str, str]:
    """Parse package.json dependencies section."""
    deps: dict[str, str] = {}
    # Simple regex extraction of "name": "version" pairs from dependencies sections
    in_deps = False
    brace_depth = 0

    for line in content.split("\n"):
        stripped = line.strip()

        if re.match(r'"(dependencies|devDependencies|peerDependencies)"', stripped):
            in_deps = True
            brace_depth = 0
            continue

        if in_deps:
            if "{" in stripped:
                brace_depth += stripped.count("{")
            if "}" in stripped:
                brace_depth -= stripped.count("}")
                if brace_depth <= 0:
                    in_deps = False
                    continue

            match = re.match(r'"([@\w./-]+)"\s*:\s*"([^"]*)"', stripped)
            if match:
                deps[match.group(1).lower()] = match.group(2)

    return deps


def _parse_pyproject_deps(content: str) -> dict[str, str]:
    """Parse pyproject.toml dependencies."""
    deps: dict[str, str] = {}
    in_deps = False

    for line in content.split("\n"):
        stripped = line.strip()

        if stripped == "dependencies = [":
            in_deps = True
            continue
        if in_deps and stripped == "]":
            in_deps = False
            continue

        if in_deps:
            match = re.match(r'"([\w.-]+)([><=!~]+[\w.*]+)?"', stripped)
            if match:
                deps[match.group(1).lower()] = (match.group(2) or "").strip()

    return deps


class DependencyAnalysisSkill(BaseSkill):
    """Analyze dependency manifest changes."""

    name = "dependency_analysis"

    def execute(self, repo_dir: str, changed_files: list[str], **kwargs: Any) -> SkillResult:
        """Analyze dependency changes in the PR."""
        diffs: list[dict] = []
        risky_deps: list[dict] = []

        # Get base and head manifest content from kwargs if available
        file_contexts = kwargs.get("file_contexts", [])

        for fc in file_contexts:
            path = fc.path if hasattr(fc, "path") else fc.get("path", "")
            base_content = fc.base_content if hasattr(fc, "base_content") else fc.get("base_content", "")
            head_content = fc.content if hasattr(fc, "content") else fc.get("content", "")

            if not self._is_manifest(path):
                continue

            if base_content and head_content:
                diff = self.diff_manifests(base_content, head_content, path)
                diffs.append(diff.model_dump())

                # Check added deps for risk
                risky = self.check_known_risky_packages(diff.added)
                risky_deps.extend([r.model_dump() for r in risky])

        parts: list[str] = []
        total_added = sum(len(d.get("added", [])) for d in diffs)
        total_removed = sum(len(d.get("removed", [])) for d in diffs)
        if total_added or total_removed:
            parts.append(f"{total_added} dependencies added, {total_removed} removed")
        if risky_deps:
            parts.append(f"{len(risky_deps)} potentially risky dependency(ies) detected")
        if not parts:
            parts.append("No dependency changes detected")

        return SkillResult(
            skill_name=self.name,
            summary="; ".join(parts),
            details={
                "diffs": diffs,
                "risky_deps": risky_deps,
            },
        )

    def diff_manifests(self, base_content: str, head_content: str, file_path: str = "") -> ManifestDiff:
        """Compute differences between base and head manifest files."""
        parser = self._get_parser(file_path)
        base_deps = parser(base_content)
        head_deps = parser(head_content)

        added = [pkg for pkg in head_deps if pkg not in base_deps]
        removed = [pkg for pkg in base_deps if pkg not in head_deps]
        changed = [
            f"{pkg}: {base_deps[pkg]} -> {head_deps[pkg]}"
            for pkg in base_deps
            if pkg in head_deps and base_deps[pkg] != head_deps[pkg]
        ]

        return ManifestDiff(added=added, removed=removed, changed=changed)

    def check_known_risky_packages(self, added_deps: list[str]) -> list[RiskyDep]:
        """Flag packages known for supply chain attacks or risky behavior."""
        risky: list[RiskyDep] = []

        for dep in added_deps:
            for pattern, reason, severity in RISKY_PACKAGE_PATTERNS:
                if re.match(pattern, dep, re.IGNORECASE):
                    risky.append(RiskyDep(
                        package_name=dep,
                        reason=reason,
                        severity=severity,
                    ))
                    break  # one match per dep is enough

        return risky

    def detect_lockfile_inconsistency(
        self, manifest_path: str, lockfile_path: str, repo_dir: str
    ) -> list[str]:
        """Check for inconsistencies between manifest and lockfile.

        Compares declared dependencies in the manifest against what is
        recorded in the lockfile. Returns a list of inconsistency descriptions.
        """
        import os

        issues: list[str] = []

        manifest_full = os.path.join(repo_dir, manifest_path)
        lockfile_full = os.path.join(repo_dir, lockfile_path)

        if not os.path.isfile(manifest_full) or not os.path.isfile(lockfile_full):
            return issues

        try:
            manifest_content = open(manifest_full, errors="replace").read()
            lockfile_content = open(lockfile_full, errors="replace").read()
        except OSError:
            return issues

        # Parse manifest deps
        parser = self._get_parser(manifest_path)
        manifest_deps = parser(manifest_content)

        # Check each manifest dep against lockfile content
        lockfile_lower = lockfile_content.lower()
        for pkg in manifest_deps:
            if pkg.lower() not in lockfile_lower:
                issues.append(f"Package '{pkg}' declared in {manifest_path} but not found in {lockfile_path}")

        return issues

    def _is_manifest(self, path: str) -> bool:
        """Check if a file path is a dependency manifest."""
        manifest_names = {
            "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
            "package.json", "go.mod", "Gemfile", "Cargo.toml",
            "pom.xml", "build.gradle", "composer.json",
        }
        import os
        return os.path.basename(path) in manifest_names

    def _get_parser(self, file_path: str):
        """Get the appropriate parser for a manifest file."""
        import os
        basename = os.path.basename(file_path)

        if basename == "requirements.txt":
            return _parse_requirements_txt
        elif basename == "package.json":
            return _parse_package_json_deps
        elif basename == "pyproject.toml":
            return _parse_pyproject_deps
        else:
            return _parse_requirements_txt  # fallback
