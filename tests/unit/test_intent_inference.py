"""Tests for intent inference."""

from crossfire.core.intent_inference import IntentInferrer
from crossfire.core.models import FileContext, DiffHunk, PRContext


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
