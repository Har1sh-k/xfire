"""Config analysis skill — understand config and infrastructure files."""

from __future__ import annotations

import os
import re
from typing import Any

from pydantic import BaseModel

from xfire.skills.base import BaseSkill, SkillResult


class CIRisk(BaseModel):
    """A risk found in CI workflow configuration."""

    file_path: str
    risk_type: str
    description: str
    severity: str = "medium"
    line_content: str = ""


class DockerRisk(BaseModel):
    """A risk found in Docker configuration."""

    file_path: str
    risk_type: str
    description: str
    severity: str = "medium"
    line_content: str = ""


class PermissionIssue(BaseModel):
    """A permission-related issue in configuration."""

    file_path: str
    issue_type: str
    description: str


# CI workflow risk patterns
CI_RISK_PATTERNS: list[tuple[str, str, str, str]] = [
    # (regex, risk_type, description, severity)
    (r"pull_request_target", "pull_request_target",
     "Uses pull_request_target trigger — code from fork runs with write permissions", "high"),
    (r"actions/checkout.*ref:\s*\$\{\{.*github\.event\.pull_request\.head", "unsafe_checkout",
     "Checks out PR head ref in pull_request_target — allows code injection from forks", "critical"),
    (r"permissions:\s*write-all|permissions:\s*\n\s+contents:\s*write", "broad_permissions",
     "Workflow has broad write permissions", "medium"),
    (r"\$\{\{.*github\.event\.(issue|comment|pull_request)\.body", "expression_injection",
     "Injects event body into workflow expression — potential command injection", "high"),
    (r"npm publish|twine upload|cargo publish", "package_publish",
     "Workflow publishes packages — supply chain risk if compromised", "medium"),
    (r"secrets\.\w+.*\n.*echo|secrets\.\w+.*>>.*GITHUB", "secret_exposure",
     "Secret value may be exposed in logs or env", "high"),
    (r"actions/cache.*\n.*key:.*\$\{\{", "cache_poisoning",
     "Cache key uses potentially controllable input", "low"),
]

# Docker risk patterns
DOCKER_RISK_PATTERNS: list[tuple[str, str, str, str]] = [
    (r"^USER\s+root|^FROM.*AS\s+\w+\n(?!.*USER)", "root_user",
     "Container runs as root user", "medium"),
    (r"EXPOSE\s+\d+", "exposed_port",
     "Container exposes network port", "low"),
    (r"ARG\s+\w*(SECRET|PASSWORD|TOKEN|KEY)\w*", "secret_in_build_arg",
     "Secret passed as build argument — may be cached in image layers", "high"),
    (r"ADD\s+https?://", "remote_add",
     "ADD from remote URL — integrity not verified", "medium"),
    (r"--privileged|--cap-add|SYS_ADMIN|NET_ADMIN", "privileged_container",
     "Container runs with elevated privileges", "high"),
    (r"COPY\s+\.\s+", "full_context_copy",
     "Copies entire build context — may include secrets or unnecessary files", "low"),
]


class ConfigAnalysisSkill(BaseSkill):
    """Analyze configuration and infrastructure files for security risks."""

    name = "config_analysis"

    def execute(self, repo_dir: str, changed_files: list[str], **kwargs: Any) -> SkillResult:
        """Analyze config files for security risks."""
        ci_risks = self.analyze_ci_workflows(repo_dir)
        docker_risks = self.analyze_dockerfiles(repo_dir)
        security_configs = self.get_security_configs(repo_dir)

        all_risks: list[dict] = []
        all_risks.extend([r.model_dump() for r in ci_risks])
        all_risks.extend([r.model_dump() for r in docker_risks])

        parts: list[str] = []
        if ci_risks:
            parts.append(f"Found {len(ci_risks)} CI workflow risk(s)")
        if docker_risks:
            parts.append(f"Found {len(docker_risks)} Docker risk(s)")
        if not parts:
            parts.append("No configuration risks detected")

        return SkillResult(
            skill_name=self.name,
            summary="; ".join(parts),
            details={
                "ci_risks": [r.model_dump() for r in ci_risks],
                "docker_risks": [r.model_dump() for r in docker_risks],
                "security_configs": security_configs,
            },
        )

    def analyze_ci_workflows(self, repo_dir: str) -> list[CIRisk]:
        """Check for dangerous CI patterns."""
        risks: list[CIRisk] = []
        workflows_dir = os.path.join(repo_dir, ".github", "workflows")

        if not os.path.isdir(workflows_dir):
            return []

        for filename in os.listdir(workflows_dir):
            if not filename.endswith((".yml", ".yaml")):
                continue
            filepath = os.path.join(workflows_dir, filename)
            try:
                content = open(filepath, errors="replace").read()
            except OSError:
                continue

            rel_path = os.path.join(".github", "workflows", filename).replace("\\", "/")

            for pattern, risk_type, description, severity in CI_RISK_PATTERNS:
                matches = re.finditer(pattern, content, re.MULTILINE | re.IGNORECASE)
                for match in matches:
                    risks.append(CIRisk(
                        file_path=rel_path,
                        risk_type=risk_type,
                        description=description,
                        severity=severity,
                        line_content=match.group(0).strip()[:200],
                    ))

        return risks

    def analyze_dockerfiles(self, repo_dir: str) -> list[DockerRisk]:
        """Check for Docker security risks."""
        risks: list[DockerRisk] = []

        for root, _dirs, files in os.walk(repo_dir):
            rel_root = os.path.relpath(root, repo_dir)
            if any(part.startswith(".") and part != "." for part in rel_root.split(os.sep)):
                continue

            for filename in files:
                if not (filename.startswith("Dockerfile") or filename == "docker-compose.yml"
                        or filename == "docker-compose.yaml"):
                    continue

                filepath = os.path.join(root, filename)
                try:
                    content = open(filepath, errors="replace").read()
                except OSError:
                    continue

                rel_path = os.path.relpath(filepath, repo_dir).replace("\\", "/")

                patterns = DOCKER_RISK_PATTERNS
                for pattern, risk_type, description, severity in patterns:
                    matches = re.finditer(pattern, content, re.MULTILINE | re.IGNORECASE)
                    for match in matches:
                        risks.append(DockerRisk(
                            file_path=rel_path,
                            risk_type=risk_type,
                            description=description,
                            severity=severity,
                            line_content=match.group(0).strip()[:200],
                        ))

        return risks

    def analyze_permissions(self, repo_dir: str) -> list[PermissionIssue]:
        """Check for permission-related issues in config."""
        issues: list[PermissionIssue] = []

        # Check for overly permissive CORS
        for root, _dirs, files in os.walk(repo_dir):
            rel_root = os.path.relpath(root, repo_dir)
            if any(part.startswith(".") or part in ("node_modules", "venv", ".venv")
                   for part in rel_root.split(os.sep)):
                continue

            for filename in files:
                if not filename.endswith((".py", ".js", ".ts", ".yaml", ".yml")):
                    continue

                filepath = os.path.join(root, filename)
                try:
                    content = open(filepath, errors="replace").read()
                except OSError:
                    continue

                rel_path = os.path.relpath(filepath, repo_dir).replace("\\", "/")

                # Check for wildcard CORS
                if re.search(r"""cors.*['"\*]['"]|Access-Control-Allow-Origin.*\*""", content, re.IGNORECASE):
                    issues.append(PermissionIssue(
                        file_path=rel_path,
                        issue_type="permissive_cors",
                        description="Wildcard CORS origin detected — allows any origin",
                    ))

        return issues

    def get_security_configs(self, repo_dir: str) -> dict[str, Any]:
        """Aggregate security-relevant configuration into a summary."""
        summary: dict[str, Any] = {
            "has_ci": os.path.isdir(os.path.join(repo_dir, ".github", "workflows")),
            "has_docker": any(
                f.startswith("Dockerfile") for f in os.listdir(repo_dir)
                if os.path.isfile(os.path.join(repo_dir, f))
            ) if os.path.isdir(repo_dir) else False,
            "has_env_example": os.path.isfile(os.path.join(repo_dir, ".env.example")),
            "has_gitignore": os.path.isfile(os.path.join(repo_dir, ".gitignore")),
        }
        return summary
