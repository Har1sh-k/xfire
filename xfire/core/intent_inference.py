"""Purpose and intent inference for CrossFire.

Heuristic-first with optional LLM enrichment:

1. **Heuristic** (IntentInferrer.infer): multi-signal regex/dependency analysis
   — always available, zero-cost, produces pr_intent, risk_surface_change,
   regex-detected security controls, and dependency-mapped capabilities.

2. **LLM-enriched** (infer_with_llm): runs heuristic first, sends results to
   Claude Sonnet as context for enrichment, then merges the LLM's threat model
   (threat_summary, rich trust boundaries, nuanced repo_purpose) with the
   heuristic output into a single IntentProfile. Falls back to the already-
   computed heuristic profile on any LLM failure — zero wasted work.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from xfire.config.settings import RepoConfig
from xfire.core.models import IntentProfile, PRContext, SecurityControl, TrustBoundary

if TYPE_CHECKING:
    from xfire.agents.base import BaseAgent


# ---------------------------------------------------------------------------
# LLM-based threat modelling
# ---------------------------------------------------------------------------

INTENT_INFERENCE_SYSTEM = """You are a senior security architect enriching a heuristic threat model for a code repository.

You will receive a pre-computed heuristic analysis alongside the raw repository context.  Your job is to:
1. **Validate** — confirm or correct heuristic findings using your understanding of the code.
2. **Extend** — add capabilities, trust boundaries, controls, or sensitive paths the heuristics missed.
3. **Enrich** — provide a nuanced repo_purpose, deployment_context, and threat_summary that go beyond regex signals.

Respond ONLY with a valid JSON object — no markdown fences, no explanation, no preamble.

JSON schema:
{
  "repo_purpose": "1-3 sentence description of what this system does and who uses it",
  "deployment_context": "where/how deployed — local agent, cloud service, CI runner, CLI tool, etc.",
  "pr_intent": "optional — override if heuristic classification is wrong, else omit or leave empty",
  "risk_surface_change": "optional — override if heuristic missed something significant, else omit or leave empty",
  "intended_capabilities": [
    "short string describing each intentional capability — e.g. 'execute user code in sandboxed subprocess', 'manage OAuth tokens for 3rd-party services', 'route WebSocket messages between clients'"
  ],
  "trust_boundaries": [
    {
      "name": "boundary name",
      "description": "what crosses this boundary and why it matters",
      "untrusted_inputs": ["input sources that arrive from untrusted parties"],
      "controls": ["controls that gate this boundary"]
    }
  ],
  "security_controls": [
    {
      "control_type": "type e.g. auth_check, rate_limit, input_validation, sandbox",
      "location": "file or component where this control lives",
      "description": "what it protects and how",
      "covers": ["what attack surfaces it covers"]
    }
  ],
  "sensitive_paths": ["path/to/auth", "path/to/payments", "etc."],
  "threat_summary": "2-3 sentence summary of the primary threat model — what attackers would target and how"
}

Be specific and technical. The intended_capabilities list is critical — it tells security reviewers what the system is *supposed* to do so they don't flag intentional behaviour as bugs."""


async def infer_with_llm(
    context: PRContext,
    agent: "BaseAgent",
    inferrer: "IntentInferrer | None" = None,
) -> IntentProfile:
    """Run heuristic-first intent inference enriched by an LLM threat model.

    1. Always runs the heuristic inferrer to produce a baseline IntentProfile.
    2. Sends the heuristic results + raw repo context to the LLM for enrichment.
    3. Merges LLM output on top of heuristic output via _merge_profiles().
    4. On any LLM failure, returns the already-computed heuristic profile.
    """
    # 1. Always run heuristic first
    if inferrer is None:
        inferrer = IntentInferrer()
    heuristic_profile = inferrer.infer(context)

    # 2. Build prompt with both raw context and heuristic summary
    readme = (context.readme_content or "")[:4000]
    directory = (context.directory_structure or "")[:2000]

    config_summary = ""
    for name, content in list((context.config_files or {}).items())[:6]:
        config_summary += f"\n### {name}\n{content[:600]}\n"

    heuristic_section = _format_heuristic_for_prompt(heuristic_profile)

    prompt = (
        f"Enrich the following heuristic threat model using the raw repository context below.\n\n"
        f"## Pre-Computed Heuristic Analysis\n{heuristic_section}\n\n"
        f"## README\n{readme}\n\n"
        f"## Directory Structure\n{directory}\n\n"
        f"## Key Config / Manifest Files\n{config_summary}\n\n"
        f"Return the enriched JSON threat model object only."
    )

    try:
        raw = await agent.execute(prompt, INTENT_INFERENCE_SYSTEM)
        data = _extract_json(raw)
        llm_profile = _parse_llm_response(data)
        return _merge_profiles(heuristic_profile, llm_profile)

    except Exception:
        # LLM failed — heuristic already computed, zero wasted work
        return heuristic_profile


def _format_heuristic_for_prompt(profile: IntentProfile) -> str:
    """Serialize a heuristic IntentProfile as readable structured text for the LLM prompt."""
    lines: list[str] = []

    lines.append(f"repo_purpose: {profile.repo_purpose}")
    if profile.deployment_context:
        lines.append(f"deployment_context: {profile.deployment_context}")
    if profile.pr_intent:
        lines.append(f"pr_intent: {profile.pr_intent}")
    if profile.risk_surface_change:
        lines.append(f"risk_surface_change: {profile.risk_surface_change}")

    if profile.intended_capabilities:
        lines.append("intended_capabilities:")
        for cap in profile.intended_capabilities:
            lines.append(f"  - {cap}")

    if profile.security_controls_detected:
        lines.append("security_controls:")
        for sc in profile.security_controls_detected:
            lines.append(f"  - {sc.control_type} in {sc.location}: {sc.description}")

    if profile.trust_boundaries:
        lines.append("trust_boundaries:")
        for tb in profile.trust_boundaries:
            lines.append(f"  - {tb.name}: {tb.description}")
            if tb.untrusted_inputs:
                lines.append(f"    untrusted_inputs: {', '.join(tb.untrusted_inputs)}")
            if tb.controls:
                lines.append(f"    controls: {', '.join(tb.controls)}")

    if profile.sensitive_paths:
        lines.append("sensitive_paths:")
        for sp in profile.sensitive_paths:
            lines.append(f"  - {sp}")

    return "\n".join(lines)


def _parse_llm_response(data: dict) -> IntentProfile:
    """Parse raw LLM JSON response into an IntentProfile."""
    trust_boundaries = [
        TrustBoundary(
            name=tb.get("name", ""),
            description=tb.get("description", ""),
            untrusted_inputs=tb.get("untrusted_inputs", []),
            controls=tb.get("controls", []),
        )
        for tb in data.get("trust_boundaries", [])
    ]

    security_controls = [
        SecurityControl(
            control_type=sc.get("control_type", ""),
            location=sc.get("location", ""),
            description=sc.get("description", ""),
            covers=sc.get("covers", []),
        )
        for sc in data.get("security_controls", [])
    ]

    purpose = data.get("repo_purpose", "")
    threat_summary = data.get("threat_summary", "")
    if threat_summary:
        purpose = f"{purpose}\n\nThreat summary: {threat_summary}"

    return IntentProfile(
        repo_purpose=purpose,
        intended_capabilities=data.get("intended_capabilities", []),
        trust_boundaries=trust_boundaries,
        security_controls_detected=security_controls,
        deployment_context=data.get("deployment_context"),
        pr_intent=data.get("pr_intent", ""),
        risk_surface_change=data.get("risk_surface_change", ""),
        sensitive_paths=data.get("sensitive_paths", []),
    )


def _merge_profiles(heuristic: IntentProfile, llm: IntentProfile) -> IntentProfile:
    """Merge heuristic and LLM IntentProfiles into a single enriched profile.

    Strategy:
    - Scalars (repo_purpose, deployment_context, pr_intent, risk_surface_change):
      LLM overrides if non-empty, else heuristic preserved.
    - Lists (intended_capabilities, sensitive_paths):
      Union with deduplication, preserving order (heuristic first).
    - trust_boundaries: merge by name — LLM overrides same-name boundaries,
      heuristic-only boundaries are kept.
    - security_controls_detected: merge by (control_type, location) key —
      overlaps get LLM description + union of covers.
    """
    # Scalars: LLM wins if non-empty
    repo_purpose = llm.repo_purpose if llm.repo_purpose else heuristic.repo_purpose
    deployment_context = llm.deployment_context if llm.deployment_context else heuristic.deployment_context
    pr_intent = llm.pr_intent if llm.pr_intent else heuristic.pr_intent
    risk_surface_change = llm.risk_surface_change if llm.risk_surface_change else heuristic.risk_surface_change

    # Lists: union with dedup (heuristic first)
    capabilities = list(dict.fromkeys(
        heuristic.intended_capabilities + llm.intended_capabilities
    ))
    sensitive_paths = list(dict.fromkeys(
        heuristic.sensitive_paths + llm.sensitive_paths
    ))

    # Trust boundaries: merge by name
    tb_map: dict[str, TrustBoundary] = {}
    for tb in heuristic.trust_boundaries:
        tb_map[tb.name] = tb
    for tb in llm.trust_boundaries:
        tb_map[tb.name] = tb  # LLM overrides same-name
    trust_boundaries = list(tb_map.values())

    # Security controls: merge by (control_type, location)
    sc_map: dict[tuple[str, str], SecurityControl] = {}
    for sc in heuristic.security_controls_detected:
        sc_map[(sc.control_type, sc.location)] = sc
    for sc in llm.security_controls_detected:
        key = (sc.control_type, sc.location)
        if key in sc_map:
            # Overlap: LLM description + union of covers
            existing = sc_map[key]
            merged_covers = list(dict.fromkeys(existing.covers + sc.covers))
            sc_map[key] = SecurityControl(
                control_type=sc.control_type,
                location=sc.location,
                description=sc.description,
                covers=merged_covers,
            )
        else:
            sc_map[key] = sc
    security_controls = list(sc_map.values())

    return IntentProfile(
        repo_purpose=repo_purpose,
        intended_capabilities=capabilities,
        trust_boundaries=trust_boundaries,
        security_controls_detected=security_controls,
        deployment_context=deployment_context,
        pr_intent=pr_intent,
        risk_surface_change=risk_surface_change,
        sensitive_paths=sensitive_paths,
    )


def _extract_json(text: str) -> dict:
    """Extract JSON object from LLM response."""
    text = text.strip()
    # Strip markdown fences
    if "```" in text:
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    # Find outermost braces
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1:
        text = text[first : last + 1]
    return json.loads(text)


# ---------------------------------------------------------------------------
# Dependency → capability mapping
# ---------------------------------------------------------------------------

DEPENDENCY_CAPABILITIES: dict[str, list[str]] = {
    # Web frameworks → HTTP input is untrusted
    "flask": ["web_server", "http_input"],
    "django": ["web_server", "http_input", "orm", "admin_panel"],
    "fastapi": ["web_server", "http_input", "api"],
    "express": ["web_server", "http_input"],
    "next": ["web_server", "http_input", "ssr"],
    "nuxt": ["web_server", "http_input", "ssr"],
    "gin": ["web_server", "http_input"],
    "actix-web": ["web_server", "http_input"],
    "rails": ["web_server", "http_input", "orm"],
    "spring-boot": ["web_server", "http_input"],
    "laravel": ["web_server", "http_input", "orm"],
    # Async / task processing
    "celery": ["async_tasks", "message_queue"],
    "rq": ["async_tasks", "message_queue"],
    "dramatiq": ["async_tasks"],
    # Cloud / infra
    "boto3": ["aws_access"],
    "google-cloud-storage": ["gcs_access"],
    "azure-storage-blob": ["azure_access"],
    "docker": ["container_orchestration"],
    "kubernetes": ["container_orchestration"],
    # LLM / AI
    "langchain": ["llm_powered", "code_generation"],
    "openai": ["llm_powered"],
    "anthropic": ["llm_powered"],
    "google-generativeai": ["llm_powered"],
    # Database
    "sqlalchemy": ["database_access"],
    "psycopg2": ["database_access"],
    "pymongo": ["database_access"],
    "redis": ["cache", "message_queue"],
    "prisma": ["database_access"],
    "mongoose": ["database_access"],
    # Auth
    "pyjwt": ["jwt_auth"],
    "python-jose": ["jwt_auth"],
    "passlib": ["password_hashing"],
    "bcrypt": ["password_hashing"],
    "authlib": ["oauth"],
    "passport": ["auth_framework"],
    # Crypto
    "cryptography": ["crypto_operations"],
    "pynacl": ["crypto_operations"],
    # File / exec
    "paramiko": ["ssh_access"],
    "fabric": ["remote_execution"],
    "subprocess": ["process_execution"],
}

# File structure → capability heuristics
STRUCTURE_HEURISTICS: list[tuple[list[str], str, str]] = [
    # (path patterns, capability, description)
    (["Dockerfile", "docker-compose"], "containerized_service", "Docker-based service"),
    (["setup.py", "pyproject.toml"], "python_package", "Python package/library"),
    (["manage.py", "wsgi.py"], "django_web_app", "Django web application"),
    (["next.config"], "nextjs_app", "Next.js frontend application"),
    (["nuxt.config"], "nuxt_app", "Nuxt.js frontend application"),
    (["sandbox/", "jail/", "isolate"], "has_isolation", "Has isolation/sandbox controls"),
    (["auth/", "middleware/auth", "permissions/"], "has_auth_layer", "Has authentication layer"),
    (["migrations/"], "database_migrations", "Database migration support"),
    (["agents/", "tools/"], "agent_tool_system", "Coding/AI agent with tool system"),
    (["terraform/", ".tf"], "infrastructure_as_code", "Terraform/IaC managed infrastructure"),
    (["k8s/", "kubernetes/", "helm/"], "kubernetes_deployment", "Kubernetes deployment"),
    ([".github/workflows/"], "ci_cd", "CI/CD pipeline"),
    (["api/", "routes/", "endpoints/"], "api_service", "API service with endpoints"),
    (["payments/", "billing/", "stripe"], "payment_processing", "Payment/billing processing"),
    (["upload/", "storage/", "media/"], "file_handling", "File upload/storage handling"),
]

# Security control patterns to scan for
SECURITY_CONTROL_PATTERNS: list[tuple[str, str, list[str]]] = [
    # (regex pattern, control_type, file_patterns_to_search)
    (r"@login_required|@auth_required|@requires_auth|@authenticated", "auth_decorator", ["*.py"]),
    (r"@require_permission|@has_permission|@permission_required", "authz_decorator", ["*.py"]),
    (r"jwt\.decode|verify_token|validate_token", "jwt_validation", ["*.py", "*.js", "*.ts"]),
    (r"rate_limit|RateLimit|throttle|Throttle", "rate_limiting", ["*.py", "*.js", "*.ts"]),
    (r"Sanitize|sanitize|escape_html|bleach\.clean|DOMPurify", "input_sanitization", ["*.py", "*.js", "*.ts"]),
    (r"CORS|cors|Access-Control-Allow", "cors_config", ["*.py", "*.js", "*.ts", "*.yaml", "*.yml"]),
    (r"helmet|csp|Content-Security-Policy", "security_headers", ["*.js", "*.ts"]),
    (r"sandbox|Sandbox|SANDBOX|container\.run|docker\.run", "sandbox_execution", ["*.py", "*.js", "*.ts"]),
    (r"audit_log|AuditLog|audit\.log|log_action", "audit_logging", ["*.py", "*.js", "*.ts"]),
    (r"Schema|validator|validate|Validator|ValidationError", "input_validation", ["*.py", "*.js", "*.ts"]),
    (r"csrf_token|csrf_protect|CSRF|csrfmiddleware", "csrf_protection", ["*.py", "*.js", "*.ts"]),
    (r"encrypt|Encrypt|ENCRYPTION|cipher", "encryption", ["*.py", "*.js", "*.ts"]),
]

# PR title/description → intent classification
PR_INTENT_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)\bfix(es|ed)?\b.*\b(bug|issue|error|crash)", "bugfix"),
    (r"(?i)\b(security|vuln|cve|exploit|auth)\b.*\bfix", "security_fix"),
    (r"(?i)\bfeat(ure)?\b|\badd(s|ed|ing)?\b", "feature"),
    (r"(?i)\brefactor\b|\bcleanup\b|\breorganiz", "refactor"),
    (r"(?i)\bdep(endenc)?s?\b|\bbump\b|\bupgrade\b|\bupdate\b.*\bpackage", "dependency_update"),
    (r"(?i)\bmigrat(e|ion)\b", "migration"),
    (r"(?i)\bconfig\b|\bsetting\b|\benv\b", "config_change"),
    (r"(?i)\bdoc(s|umentation)?\b|\breadme\b", "documentation"),
    (r"(?i)\btest(s|ing)?\b", "testing"),
    (r"(?i)\bci\b|\bcd\b|\bpipeline\b|\bworkflow\b|\bgithub.action", "ci_cd"),
    (r"(?i)\bperformance\b|\boptimiz\b|\bspeed\b", "performance"),
]


# ---------------------------------------------------------------------------
# Intent inference engine
# ---------------------------------------------------------------------------


class IntentInferrer:
    """Infers purpose and intent from repository context."""

    def __init__(self, repo_config: RepoConfig | None = None) -> None:
        self.repo_config = repo_config

    def infer(self, context: PRContext) -> IntentProfile:
        """Infer intent from PR context using multi-signal approach."""
        capabilities: list[str] = []
        trust_boundaries: list[TrustBoundary] = []
        security_controls: list[SecurityControl] = []

        # 1. Config override
        repo_purpose = ""
        sensitive_paths: list[str] = []
        if self.repo_config:
            if self.repo_config.purpose:
                repo_purpose = self.repo_config.purpose
            capabilities.extend(self.repo_config.intended_capabilities)
            sensitive_paths = list(self.repo_config.sensitive_paths)

        # 2. README parsing
        if not repo_purpose and context.readme_content:
            repo_purpose = self._extract_purpose_from_readme(context.readme_content)

        # 3. Package metadata
        pkg_purpose, pkg_caps = self._analyze_package_metadata(context.config_files)
        if not repo_purpose and pkg_purpose:
            repo_purpose = pkg_purpose
        capabilities.extend(pkg_caps)

        # 4. File structure heuristics
        structure_caps = self._analyze_file_structure(context.directory_structure)
        capabilities.extend(structure_caps)

        # 5. Dependency inference
        dep_caps = self._analyze_dependencies(context.config_files)
        capabilities.extend(dep_caps)

        # 6. Security control detection
        security_controls = self._detect_security_controls(context)

        # 7. Trust boundary inference
        trust_boundaries = self._infer_trust_boundaries(capabilities, security_controls)

        # 8. PR-specific intent
        pr_intent = self._classify_pr_intent(context.pr_title, context.pr_description)

        # 9. Risk surface change analysis
        risk_surface = self._analyze_risk_surface_change(context)

        # 10. Sensitive path detection
        sensitive_paths.extend(self._detect_sensitive_paths(context))

        # Dedupe
        capabilities = list(dict.fromkeys(capabilities))
        sensitive_paths = list(dict.fromkeys(sensitive_paths))

        if not repo_purpose:
            repo_purpose = "Unknown — could not infer from available signals"

        return IntentProfile(
            repo_purpose=repo_purpose,
            intended_capabilities=capabilities,
            trust_boundaries=trust_boundaries,
            security_controls_detected=security_controls,
            deployment_context=self._infer_deployment_context(context),
            pr_intent=pr_intent,
            risk_surface_change=risk_surface,
            sensitive_paths=sensitive_paths,
        )

    # --- Signal extraction methods ---

    def _extract_purpose_from_readme(self, readme: str) -> str:
        """Extract project description from README."""
        lines = readme.strip().split("\n")

        # Try to find the first non-header, non-empty line after the title
        found_title = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                found_title = True
                continue
            if found_title and stripped:
                # Skip badges, links-only lines
                if stripped.startswith("[![") or stripped.startswith("!["):
                    continue
                if stripped.startswith(">"):
                    return stripped.lstrip("> ").strip()
                return stripped[:300]

        return ""

    def _analyze_package_metadata(self, config_files: dict[str, str]) -> tuple[str, list[str]]:
        """Extract purpose and capabilities from package metadata."""
        purpose = ""
        capabilities: list[str] = []

        # pyproject.toml
        if "pyproject.toml" in config_files:
            content = config_files["pyproject.toml"]
            desc_match = re.search(r'description\s*=\s*"([^"]+)"', content)
            if desc_match:
                purpose = desc_match.group(1)
            if "console_scripts" in content or "scripts" in content:
                capabilities.append("cli_tool")

        # package.json
        if "package.json" in config_files:
            content = config_files["package.json"]
            desc_match = re.search(r'"description"\s*:\s*"([^"]+)"', content)
            if desc_match:
                purpose = desc_match.group(1)
            if '"bin"' in content:
                capabilities.append("cli_tool")
            if '"scripts"' in content and '"start"' in content:
                capabilities.append("runnable_service")

        return purpose, capabilities

    def _analyze_file_structure(self, directory_structure: str) -> list[str]:
        """Infer capabilities from directory structure."""
        capabilities: list[str] = []
        structure_lower = directory_structure.lower()

        for patterns, capability, _desc in STRUCTURE_HEURISTICS:
            for pattern in patterns:
                if pattern.lower() in structure_lower:
                    capabilities.append(capability)
                    break

        return capabilities

    def _analyze_dependencies(self, config_files: dict[str, str]) -> list[str]:
        """Infer capabilities from project dependencies."""
        capabilities: list[str] = []
        all_deps_text = ""

        for filename in ("pyproject.toml", "requirements.txt", "setup.py", "package.json",
                         "go.mod", "Gemfile", "Cargo.toml"):
            if filename in config_files:
                all_deps_text += " " + config_files[filename].lower()

        for dep_name, caps in DEPENDENCY_CAPABILITIES.items():
            if dep_name.lower() in all_deps_text:
                capabilities.extend(caps)

        return capabilities

    def _detect_security_controls(self, context: PRContext) -> list[SecurityControl]:
        """Scan code for security controls."""
        controls: list[SecurityControl] = []
        seen: set[str] = set()

        # Scan changed files and their content
        for fc in context.files:
            content = fc.content or ""
            for pattern, control_type, _file_patterns in SECURITY_CONTROL_PATTERNS:
                if control_type in seen:
                    continue
                if re.search(pattern, content):
                    seen.add(control_type)
                    controls.append(SecurityControl(
                        control_type=control_type,
                        location=fc.path,
                        description=f"Detected {control_type} pattern in {fc.path}",
                        covers=[fc.path],
                    ))

            # Also scan related files
            for rf in fc.related_files:
                rf_content = rf.content or ""
                for pattern, control_type, _file_patterns in SECURITY_CONTROL_PATTERNS:
                    if control_type in seen:
                        continue
                    if re.search(pattern, rf_content):
                        seen.add(control_type)
                        controls.append(SecurityControl(
                            control_type=control_type,
                            location=rf.path,
                            description=f"Detected {control_type} pattern in {rf.path}",
                            covers=[rf.path],
                        ))

        return controls

    def _infer_trust_boundaries(
        self,
        capabilities: list[str],
        controls: list[SecurityControl],
    ) -> list[TrustBoundary]:
        """Infer trust boundaries from capabilities and controls."""
        boundaries: list[TrustBoundary] = []

        if any(cap in capabilities for cap in ("web_server", "http_input", "api_service")):
            control_names = [c.control_type for c in controls]
            boundaries.append(TrustBoundary(
                name="HTTP boundary",
                description="All HTTP input from external clients is untrusted",
                untrusted_inputs=["request.body", "request.params", "request.headers",
                                  "request.cookies", "query_string", "path_params"],
                controls=control_names,
            ))

        if "database_access" in capabilities:
            boundaries.append(TrustBoundary(
                name="Database boundary",
                description="Data from database may contain user-supplied content",
                untrusted_inputs=["db_query_results", "stored_user_content"],
                controls=[c.control_type for c in controls if c.control_type in
                         ("input_validation", "input_sanitization")],
            ))

        if "llm_powered" in capabilities:
            boundaries.append(TrustBoundary(
                name="LLM boundary",
                description="LLM outputs may contain prompt injection or unexpected content",
                untrusted_inputs=["llm_response", "generated_code", "tool_calls"],
                controls=[c.control_type for c in controls if c.control_type in
                         ("sandbox_execution", "input_validation")],
            ))

        return boundaries

    def _classify_pr_intent(self, title: str, description: str) -> str:
        """Classify PR intent from title and description."""
        combined = f"{title} {description}"

        for pattern, intent_type in PR_INTENT_PATTERNS:
            if re.search(pattern, combined):
                return intent_type

        return "unknown"

    def _analyze_risk_surface_change(self, context: PRContext) -> str:
        """Analyze how the PR changes the attack surface."""
        new_files = [f for f in context.files if f.is_new]
        deleted_files = [f for f in context.files if f.is_deleted]
        modified_files = [f for f in context.files if not f.is_new and not f.is_deleted]

        changes: list[str] = []

        if new_files:
            changes.append(f"{len(new_files)} new file(s) added")
        if deleted_files:
            changes.append(f"{len(deleted_files)} file(s) removed")
        if modified_files:
            changes.append(f"{len(modified_files)} file(s) modified")

        # Check for specific risk indicators
        for fc in context.files:
            for hunk in fc.diff_hunks:
                added = " ".join(hunk.added_lines).lower()
                if any(kw in added for kw in ("subprocess", "exec(", "eval(", "os.system")):
                    changes.append(f"Process execution code added in {fc.path}")
                if any(kw in added for kw in ("route(", "@app.", "@router.", "endpoint")):
                    changes.append(f"New endpoint/route added in {fc.path}")
                if any(kw in added for kw in ("secret", "api_key", "password", "token")):
                    changes.append(f"Secret/credential handling changed in {fc.path}")

        return "; ".join(changes) if changes else "No significant risk surface change detected"

    def _detect_sensitive_paths(self, context: PRContext) -> list[str]:
        """Detect sensitive paths in changed files."""
        sensitive_keywords = [
            "auth", "login", "session", "token", "password", "secret",
            "payment", "billing", "stripe", "crypto", "encrypt",
            "migration", "admin", "permission", "role", "acl",
            "upload", "download", "exec", "eval", "deploy",
        ]
        sensitive: list[str] = []

        for fc in context.files:
            path_lower = fc.path.lower()
            if any(kw in path_lower for kw in sensitive_keywords):
                sensitive.append(fc.path)

        return sensitive

    def _infer_deployment_context(self, context: PRContext) -> str | None:
        """Infer deployment context from config files."""
        indicators: list[str] = []

        for path in context.config_files:
            if "docker" in path.lower():
                indicators.append("Docker")
            if "kubernetes" in path.lower() or "k8s" in path.lower():
                indicators.append("Kubernetes")
            if path.endswith(".tf"):
                indicators.append("Terraform")
            if "heroku" in path.lower():
                indicators.append("Heroku")
            if "vercel" in path.lower():
                indicators.append("Vercel")

        for path in context.ci_config_files:
            if "github" in path.lower():
                indicators.append("GitHub Actions")
            if "gitlab" in path.lower():
                indicators.append("GitLab CI")

        return ", ".join(dict.fromkeys(indicators)) if indicators else None
