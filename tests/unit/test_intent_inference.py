"""Tests for intent inference."""

import asyncio
from unittest.mock import AsyncMock

from crossfire.config.settings import RepoConfig
from crossfire.core.intent_inference import (
    IntentInferrer,
    _format_heuristic_for_prompt,
    _merge_profiles,
    infer_with_llm,
)
from crossfire.core.models import (
    DiffHunk,
    FileContext,
    IntentProfile,
    PRContext,
    RelatedFile,
    SecurityControl,
    TrustBoundary,
)


def _make_context(**kwargs) -> PRContext:
    """Helper to create a PRContext with defaults."""
    defaults = {
        "repo_name": "test/repo",
        "pr_title": "Test PR",
    }
    defaults.update(kwargs)
    return PRContext(**defaults)


class TestReadmeParsing:
    def test_extracts_description_after_title(self):
        inferrer = IntentInferrer()
        ctx = _make_context(readme_content="# MyApp\n\nA web application for managing tasks.")
        intent = inferrer.infer(ctx)
        assert "web application" in intent.repo_purpose.lower() or "managing tasks" in intent.repo_purpose.lower()

    def test_extracts_blockquote_tagline(self):
        inferrer = IntentInferrer()
        ctx = _make_context(readme_content="# MyApp\n\n> A fast CLI tool for deployment.")
        intent = inferrer.infer(ctx)
        assert "CLI tool" in intent.repo_purpose or "deployment" in intent.repo_purpose

    def test_unknown_with_no_readme(self):
        inferrer = IntentInferrer()
        ctx = _make_context()
        intent = inferrer.infer(ctx)
        assert "Unknown" in intent.repo_purpose


class TestDependencyInference:
    def test_detects_flask_web_server(self):
        inferrer = IntentInferrer()
        ctx = _make_context(config_files={"requirements.txt": "flask==3.0\nredis\n"})
        intent = inferrer.infer(ctx)
        assert "web_server" in intent.intended_capabilities
        assert "http_input" in intent.intended_capabilities

    def test_detects_django(self):
        inferrer = IntentInferrer()
        ctx = _make_context(config_files={"requirements.txt": "django>=4.0\ncelery\n"})
        intent = inferrer.infer(ctx)
        assert "web_server" in intent.intended_capabilities
        assert "async_tasks" in intent.intended_capabilities

    def test_detects_llm_powered(self):
        inferrer = IntentInferrer()
        ctx = _make_context(config_files={"requirements.txt": "openai\nlangchain\n"})
        intent = inferrer.infer(ctx)
        assert "llm_powered" in intent.intended_capabilities


class TestFileStructureHeuristics:
    def test_detects_docker(self):
        inferrer = IntentInferrer()
        ctx = _make_context(directory_structure="myapp/\n├── Dockerfile\n├── docker-compose.yml\n├── app.py")
        intent = inferrer.infer(ctx)
        assert "containerized_service" in intent.intended_capabilities

    def test_detects_auth_layer(self):
        inferrer = IntentInferrer()
        ctx = _make_context(directory_structure="myapp/\n├── auth/\n│   ├── login.py\n├── middleware/\n│   ├── auth.py")
        intent = inferrer.infer(ctx)
        assert "has_auth_layer" in intent.intended_capabilities

    def test_detects_migrations(self):
        inferrer = IntentInferrer()
        ctx = _make_context(directory_structure="myapp/\n├── migrations/\n│   ├── 001_init.py")
        intent = inferrer.infer(ctx)
        assert "database_migrations" in intent.intended_capabilities


class TestPRIntentClassification:
    def test_bugfix(self):
        inferrer = IntentInferrer()
        ctx = _make_context(pr_title="Fix crash in login handler")
        intent = inferrer.infer(ctx)
        assert intent.pr_intent == "bugfix"

    def test_feature(self):
        inferrer = IntentInferrer()
        ctx = _make_context(pr_title="Add user profile page")
        intent = inferrer.infer(ctx)
        assert intent.pr_intent == "feature"

    def test_security_fix(self):
        inferrer = IntentInferrer()
        ctx = _make_context(pr_title="Security fix for auth bypass vulnerability")
        intent = inferrer.infer(ctx)
        assert intent.pr_intent == "security_fix"

    def test_dependency_update(self):
        inferrer = IntentInferrer()
        ctx = _make_context(pr_title="Bump package dependencies")
        intent = inferrer.infer(ctx)
        assert intent.pr_intent == "dependency_update"

    def test_refactor(self):
        inferrer = IntentInferrer()
        ctx = _make_context(pr_title="Refactor database layer")
        intent = inferrer.infer(ctx)
        assert intent.pr_intent == "refactor"


class TestRiskSurfaceChange:
    def test_detects_new_endpoint(self):
        inferrer = IntentInferrer()
        ctx = _make_context(
            files=[
                FileContext(
                    path="api/users.py",
                    diff_hunks=[
                        DiffHunk(
                            file_path="api/users.py",
                            old_start=1, old_count=3, new_start=1, new_count=5,
                            content="@@ -1,3 +1,5 @@",
                            added_lines=["@app.route('/api/users')", "def list_users():"],
                        )
                    ],
                )
            ],
        )
        intent = inferrer.infer(ctx)
        assert "endpoint" in intent.risk_surface_change.lower() or "route" in intent.risk_surface_change.lower()

    def test_detects_exec_added(self):
        inferrer = IntentInferrer()
        ctx = _make_context(
            files=[
                FileContext(
                    path="runner.py",
                    diff_hunks=[
                        DiffHunk(
                            file_path="runner.py",
                            old_start=1, old_count=3, new_start=1, new_count=5,
                            content="@@ -1,3 +1,5 @@",
                            added_lines=["import subprocess", "subprocess.run(cmd)"],
                        )
                    ],
                )
            ],
        )
        intent = inferrer.infer(ctx)
        assert "execution" in intent.risk_surface_change.lower() or "process" in intent.risk_surface_change.lower()


class TestTrustBoundaries:
    def test_http_boundary_for_web_app(self):
        inferrer = IntentInferrer()
        ctx = _make_context(config_files={"requirements.txt": "flask\n"})
        intent = inferrer.infer(ctx)
        assert any(tb.name == "HTTP boundary" for tb in intent.trust_boundaries)

    def test_llm_boundary_for_ai_app(self):
        inferrer = IntentInferrer()
        ctx = _make_context(config_files={"requirements.txt": "langchain\n"})
        intent = inferrer.infer(ctx)
        assert any(tb.name == "LLM boundary" for tb in intent.trust_boundaries)


class TestSecurityControlDetection:
    def test_detects_auth_decorator(self):
        inferrer = IntentInferrer()
        ctx = _make_context(
            files=[
                FileContext(
                    path="api/views.py",
                    content="@login_required\ndef secret_view(request):\n    pass\n",
                )
            ],
        )
        intent = inferrer.infer(ctx)
        assert any(c.control_type == "auth_decorator" for c in intent.security_controls_detected)

    def test_detects_rate_limiting(self):
        inferrer = IntentInferrer()
        ctx = _make_context(
            files=[
                FileContext(
                    path="api/views.py",
                    content="from flask_limiter import RateLimit\n",
                )
            ],
        )
        intent = inferrer.infer(ctx)
        assert any(c.control_type == "rate_limiting" for c in intent.security_controls_detected)


class TestSensitivePaths:
    def test_detects_auth_path(self):
        inferrer = IntentInferrer()
        ctx = _make_context(
            files=[
                FileContext(path="auth/login.py"),
                FileContext(path="utils/helpers.py"),
            ],
        )
        intent = inferrer.infer(ctx)
        assert "auth/login.py" in intent.sensitive_paths
        assert "utils/helpers.py" not in intent.sensitive_paths


# ─── Package Metadata Tests ──────────────────────────────────────────────────


class TestAnalyzePackageMetadata:
    def test_pyproject_description(self):
        inferrer = IntentInferrer()
        purpose, caps = inferrer._analyze_package_metadata(
            {"pyproject.toml": 'description = "A CLI for managing deployments"'}
        )
        assert "CLI for managing deployments" in purpose

    def test_package_json_bin(self):
        inferrer = IntentInferrer()
        purpose, caps = inferrer._analyze_package_metadata(
            {"package.json": '{"description":"web tool","bin":"./cli.js","scripts":{"start":"node ."}}'}
        )
        assert "cli_tool" in caps
        assert "runnable_service" in caps

    def test_empty_config_files(self):
        inferrer = IntentInferrer()
        purpose, caps = inferrer._analyze_package_metadata({})
        assert purpose == ""
        assert caps == []


# ─── Deep Security Controls Detection Tests ──────────────────────────────────


class TestDetectSecurityControlsDeep:
    def test_detects_controls_in_related_files(self):
        """Controls found in related file content are detected."""
        inferrer = IntentInferrer()
        ctx = _make_context(
            files=[
                FileContext(
                    path="api/views.py",
                    content="def index(): pass",
                    related_files=[
                        RelatedFile(
                            path="auth/middleware.py",
                            relationship="imports",
                            content="@login_required\ndef secure(): pass",
                            relevance="imported by views.py",
                        ),
                    ],
                ),
            ],
        )
        controls = inferrer._detect_security_controls(ctx)
        assert any(c.control_type == "auth_decorator" for c in controls)

    def test_deduplicates_control_types(self):
        """Same control type in multiple files is only reported once."""
        inferrer = IntentInferrer()
        ctx = _make_context(
            files=[
                FileContext(path="a.py", content="@login_required\ndef v1(): pass"),
                FileContext(path="b.py", content="@login_required\ndef v2(): pass"),
            ],
        )
        controls = inferrer._detect_security_controls(ctx)
        auth_controls = [c for c in controls if c.control_type == "auth_decorator"]
        assert len(auth_controls) == 1


# ─── Trust Boundary Inference Tests ──────────────────────────────────────────


class TestInferTrustBoundaries:
    def test_database_boundary(self):
        inferrer = IntentInferrer()
        boundaries = inferrer._infer_trust_boundaries(["database_access"], [])
        assert any(tb.name == "Database boundary" for tb in boundaries)


# ─── Sensitive Path Detection Tests ──────────────────────────────────────────


class TestDetectSensitivePaths:
    def test_detects_payment_path(self):
        inferrer = IntentInferrer()
        ctx = _make_context(
            files=[FileContext(path="payments/stripe.py")],
        )
        paths = inferrer._detect_sensitive_paths(ctx)
        assert "payments/stripe.py" in paths

    def test_ignores_safe_path(self):
        inferrer = IntentInferrer()
        ctx = _make_context(
            files=[FileContext(path="lib/utils.py")],
        )
        paths = inferrer._detect_sensitive_paths(ctx)
        assert paths == []


# ─── _merge_profiles Tests ───────────────────────────────────────────────────


class TestMergeProfiles:
    def test_llm_overrides_scalar_when_nonempty(self):
        heuristic = IntentProfile(
            repo_purpose="heuristic purpose",
            deployment_context="Docker",
            pr_intent="bugfix",
            risk_surface_change="1 file modified",
        )
        llm = IntentProfile(
            repo_purpose="LLM enriched purpose",
            deployment_context="Kubernetes cluster",
            pr_intent="",
            risk_surface_change="",
        )
        merged = _merge_profiles(heuristic, llm)
        assert merged.repo_purpose == "LLM enriched purpose"
        assert merged.deployment_context == "Kubernetes cluster"
        # LLM empty → heuristic preserved
        assert merged.pr_intent == "bugfix"
        assert merged.risk_surface_change == "1 file modified"

    def test_heuristic_preserved_when_llm_empty(self):
        heuristic = IntentProfile(repo_purpose="heuristic", pr_intent="feature")
        llm = IntentProfile()
        merged = _merge_profiles(heuristic, llm)
        assert merged.repo_purpose == "heuristic"
        assert merged.pr_intent == "feature"

    def test_capabilities_union_deduped(self):
        heuristic = IntentProfile(intended_capabilities=["web_server", "http_input", "database_access"])
        llm = IntentProfile(intended_capabilities=["http_input", "llm_powered", "database_access"])
        merged = _merge_profiles(heuristic, llm)
        assert merged.intended_capabilities == ["web_server", "http_input", "database_access", "llm_powered"]

    def test_sensitive_paths_union_deduped(self):
        heuristic = IntentProfile(sensitive_paths=["auth/login.py", "payments/stripe.py"])
        llm = IntentProfile(sensitive_paths=["auth/login.py", "crypto/keys.py"])
        merged = _merge_profiles(heuristic, llm)
        assert merged.sensitive_paths == ["auth/login.py", "payments/stripe.py", "crypto/keys.py"]

    def test_trust_boundaries_merge_by_name(self):
        heuristic = IntentProfile(trust_boundaries=[
            TrustBoundary(name="HTTP boundary", description="heuristic desc", untrusted_inputs=["body"]),
            TrustBoundary(name="DB boundary", description="heuristic DB"),
        ])
        llm = IntentProfile(trust_boundaries=[
            TrustBoundary(name="HTTP boundary", description="LLM enriched HTTP", untrusted_inputs=["body", "headers"]),
            TrustBoundary(name="LLM boundary", description="LLM-only boundary"),
        ])
        merged = _merge_profiles(heuristic, llm)
        tb_names = [tb.name for tb in merged.trust_boundaries]
        assert "HTTP boundary" in tb_names
        assert "DB boundary" in tb_names
        assert "LLM boundary" in tb_names
        # HTTP boundary should be LLM's version
        http_tb = next(tb for tb in merged.trust_boundaries if tb.name == "HTTP boundary")
        assert http_tb.description == "LLM enriched HTTP"

    def test_security_controls_merge_by_key(self):
        heuristic = IntentProfile(security_controls_detected=[
            SecurityControl(
                control_type="auth_decorator", location="views.py",
                description="heuristic found auth", covers=["views.py"],
            ),
            SecurityControl(
                control_type="rate_limiting", location="middleware.py",
                description="heuristic rate limit", covers=["api/"],
            ),
        ])
        llm = IntentProfile(security_controls_detected=[
            SecurityControl(
                control_type="auth_decorator", location="views.py",
                description="LLM: comprehensive auth check", covers=["views.py", "admin.py"],
            ),
            SecurityControl(
                control_type="csrf_protection", location="settings.py",
                description="LLM found CSRF", covers=["forms/"],
            ),
        ])
        merged = _merge_profiles(heuristic, llm)
        types = [(sc.control_type, sc.location) for sc in merged.security_controls_detected]
        assert ("auth_decorator", "views.py") in types
        assert ("rate_limiting", "middleware.py") in types
        assert ("csrf_protection", "settings.py") in types

        # Overlapping control: LLM description + union of covers
        auth = next(sc for sc in merged.security_controls_detected
                    if sc.control_type == "auth_decorator")
        assert auth.description == "LLM: comprehensive auth check"
        assert "views.py" in auth.covers
        assert "admin.py" in auth.covers

    def test_empty_profiles(self):
        merged = _merge_profiles(IntentProfile(), IntentProfile())
        assert merged.repo_purpose == ""
        assert merged.intended_capabilities == []


# ─── _format_heuristic_for_prompt Tests ──────────────────────────────────────


class TestFormatHeuristicForPrompt:
    def test_all_fields_present(self):
        profile = IntentProfile(
            repo_purpose="A web app",
            deployment_context="Docker",
            pr_intent="feature",
            risk_surface_change="2 new files",
            intended_capabilities=["web_server", "database_access"],
            security_controls_detected=[
                SecurityControl(
                    control_type="auth_decorator", location="views.py",
                    description="login required", covers=["views.py"],
                ),
            ],
            trust_boundaries=[
                TrustBoundary(
                    name="HTTP boundary", description="all HTTP input untrusted",
                    untrusted_inputs=["body", "headers"], controls=["auth_decorator"],
                ),
            ],
            sensitive_paths=["auth/login.py"],
        )
        text = _format_heuristic_for_prompt(profile)
        assert "repo_purpose: A web app" in text
        assert "deployment_context: Docker" in text
        assert "pr_intent: feature" in text
        assert "risk_surface_change: 2 new files" in text
        assert "web_server" in text
        assert "database_access" in text
        assert "auth_decorator" in text
        assert "HTTP boundary" in text
        assert "auth/login.py" in text

    def test_empty_profile(self):
        text = _format_heuristic_for_prompt(IntentProfile())
        assert "repo_purpose:" in text
        # Should not contain section headers for empty lists
        assert "intended_capabilities:" not in text
        assert "trust_boundaries:" not in text
        assert "sensitive_paths:" not in text


# ─── infer_with_llm Enrichment Flow Tests ────────────────────────────────────


class TestInferWithLlmEnrichment:
    def test_heuristic_always_runs_and_llm_enriches(self):
        """When LLM succeeds, result is merged heuristic + LLM."""
        llm_response = '''{
            "repo_purpose": "LLM: an API service for payments",
            "deployment_context": "AWS ECS",
            "intended_capabilities": ["payment_processing", "web_server"],
            "trust_boundaries": [
                {"name": "HTTP boundary", "description": "LLM HTTP desc", "untrusted_inputs": ["body"], "controls": ["auth"]}
            ],
            "security_controls": [
                {"control_type": "encryption", "location": "crypto.py", "description": "LLM found encryption", "covers": ["payments/"]}
            ],
            "sensitive_paths": ["payments/charge.py"],
            "threat_summary": "Attackers target payment flow"
        }'''

        mock_agent = AsyncMock()
        mock_agent.execute.return_value = llm_response

        ctx = _make_context(
            pr_title="Add payment endpoint",
            config_files={"requirements.txt": "flask\n"},
            readme_content="# PayApp\n\nA payment processing service.",
        )

        inferrer = IntentInferrer()
        result = asyncio.get_event_loop().run_until_complete(
            infer_with_llm(ctx, mock_agent, inferrer)
        )

        # LLM scalars should override
        assert "LLM: an API service for payments" in result.repo_purpose
        assert result.deployment_context == "AWS ECS"

        # Heuristic fields should be preserved (pr_intent from heuristic)
        assert result.pr_intent == "feature"  # heuristic classified "Add payment endpoint"

        # Capabilities should be union
        assert "web_server" in result.intended_capabilities
        assert "payment_processing" in result.intended_capabilities
        assert "http_input" in result.intended_capabilities  # from heuristic Flask detection

        # Agent was called once
        mock_agent.execute.assert_called_once()

    def test_fallback_to_heuristic_on_llm_failure(self):
        """When LLM fails, we get the heuristic profile back."""
        mock_agent = AsyncMock()
        mock_agent.execute.side_effect = RuntimeError("LLM unavailable")

        ctx = _make_context(
            pr_title="Fix crash in login handler",
            config_files={"requirements.txt": "django\n"},
        )

        inferrer = IntentInferrer()
        result = asyncio.get_event_loop().run_until_complete(
            infer_with_llm(ctx, mock_agent, inferrer)
        )

        # Should still have heuristic results
        assert result.pr_intent == "bugfix"
        assert "web_server" in result.intended_capabilities  # django detected
        assert "http_input" in result.intended_capabilities

    def test_default_inferrer_created_when_none(self):
        """When no inferrer passed, a default IntentInferrer() is created."""
        mock_agent = AsyncMock()
        mock_agent.execute.side_effect = RuntimeError("fail")

        ctx = _make_context(pr_title="Add feature", config_files={"requirements.txt": "flask\n"})

        result = asyncio.get_event_loop().run_until_complete(
            infer_with_llm(ctx, mock_agent)
        )

        # Heuristic still ran with default inferrer
        assert "web_server" in result.intended_capabilities
