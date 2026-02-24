"""Tests for CrossFire skills."""

import os
import subprocess
import tempfile
from pathlib import Path

from crossfire.skills.code_navigation import CodeNavigationSkill, ImportRef
from crossfire.skills.config_analysis import ConfigAnalysisSkill
from crossfire.skills.data_flow_tracing import DataFlowTracingSkill
from crossfire.skills.dependency_analysis import (
    DependencyAnalysisSkill,
    _parse_pyproject_deps,
    _parse_requirements_txt,
    _parse_package_json_deps,
)
from crossfire.skills.git_archeology import GitArcheologySkill
from crossfire.skills.test_coverage_check import TestCoverageCheckSkill


class TestCodeNavigation:
    def test_find_python_imports(self, tmp_path):
        # Create a mock file with imports
        app_py = tmp_path / "app.py"
        app_py.write_text("from utils import helper\nimport os\nimport config.settings\n")
        utils_py = tmp_path / "utils.py"
        utils_py.write_text("def helper(): pass\n")

        skill = CodeNavigationSkill()
        imports = skill.find_imports("app.py", str(tmp_path))

        # Should find at least the utils import
        modules = [i.imported_module for i in imports]
        assert "utils" in modules

    def test_find_js_imports(self, tmp_path):
        index_js = tmp_path / "index.js"
        index_js.write_text("import { foo } from './utils';\nconst bar = require('./config');\n")
        utils_js = tmp_path / "utils.js"
        utils_js.write_text("export function foo() {}\n")

        skill = CodeNavigationSkill()
        imports = skill.find_imports("index.js", str(tmp_path))

        modules = [i.imported_module for i in imports]
        assert "./utils" in modules
        assert "./config" in modules

    def test_extract_defined_symbols(self, tmp_path):
        app_py = tmp_path / "app.py"
        app_py.write_text("def hello():\n    pass\n\nclass MyClass:\n    pass\n")

        skill = CodeNavigationSkill()
        symbols = skill._extract_defined_symbols(app_py.read_text(), "app.py")

        names = [s.symbol for s in symbols]
        assert "hello" in names
        assert "MyClass" in names


class TestDataFlowTracing:
    def test_find_python_input_sources(self, tmp_path):
        app_py = tmp_path / "app.py"
        app_py.write_text(
            "from flask import request\n"
            "def view():\n"
            "    data = request.args.get('cmd')\n"
            "    env = os.environ['SECRET']\n"
        )

        skill = DataFlowTracingSkill()
        sources = skill.find_input_sources("app.py", str(tmp_path))

        types = [s.source_type for s in sources]
        assert "http_param" in types
        assert "env_var" in types

    def test_find_python_sinks(self, tmp_path):
        app_py = tmp_path / "app.py"
        app_py.write_text(
            "import subprocess\n"
            "def run(cmd):\n"
            "    subprocess.run(cmd, shell=True)\n"
            "    eval(code)\n"
        )

        skill = DataFlowTracingSkill()
        sinks = skill.find_dangerous_sinks("app.py", str(tmp_path))

        types = [s.sink_type for s in sinks]
        assert "exec" in types
        assert "eval" in types

    def test_trace_same_file_flow(self, tmp_path):
        app_py = tmp_path / "app.py"
        app_py.write_text(
            "from flask import request\n"
            "def view():\n"
            "    cmd = request.args.get('cmd')\n"
            "    subprocess.run(cmd)\n"
        )

        skill = DataFlowTracingSkill()
        sources = skill.find_input_sources("app.py", str(tmp_path))
        sinks = skill.find_dangerous_sinks("app.py", str(tmp_path))

        assert len(sources) >= 1
        assert len(sinks) >= 1

        flow = skill.trace_flow(sources[0], sinks[0], str(tmp_path))
        assert flow is not None
        assert flow.confidence > 0

    def test_summarize_data_flows(self, tmp_path):
        app_py = tmp_path / "app.py"
        app_py.write_text(
            "from flask import request\n"
            "def view():\n"
            "    cmd = request.args.get('cmd')\n"
            "    subprocess.run(cmd)\n"
        )

        skill = DataFlowTracingSkill()
        summary = skill.summarize_data_flows(["app.py"], str(tmp_path))

        assert len(summary.sources) >= 1
        assert len(summary.sinks) >= 1
        assert "input source" in summary.summary.lower()


class TestDependencyAnalysis:
    def test_parse_requirements_txt(self):
        content = "flask==3.0\nrequests>=2.28\npytest\n# comment\n"
        deps = _parse_requirements_txt(content)
        assert "flask" in deps
        assert "requests" in deps
        assert "pytest" in deps

    def test_parse_package_json_deps(self):
        content = """{
  "name": "test",
  "dependencies": {
    "express": "^4.18.0",
    "lodash": "^4.17.21"
  },
  "devDependencies": {
    "jest": "^29.0.0"
  }
}"""
        deps = _parse_package_json_deps(content)
        assert "express" in deps
        assert "lodash" in deps
        assert "jest" in deps

    def test_diff_manifests(self):
        base = "flask==3.0\nrequests==2.28\n"
        head = "flask==3.1\nrequests==2.28\nhttpx==0.25\n"

        skill = DependencyAnalysisSkill()
        diff = skill.diff_manifests(base, head, "requirements.txt")

        assert "httpx" in diff.added
        assert len(diff.changed) == 1  # flask version changed
        assert diff.removed == []

    def test_check_risky_packages(self):
        skill = DependencyAnalysisSkill()
        risky = skill.check_known_risky_packages(["event-stream", "flask", "lodash"])
        assert len(risky) == 1
        assert risky[0].package_name == "event-stream"


class TestConfigAnalysis:
    def test_analyze_ci_workflows_clean(self, tmp_path):
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "ci.yml").write_text(
            "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        )

        skill = ConfigAnalysisSkill()
        risks = skill.analyze_ci_workflows(str(tmp_path))
        assert len(risks) == 0

    def test_analyze_ci_workflows_risky(self, tmp_path):
        workflows_dir = tmp_path / ".github" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "pr.yml").write_text(
            "name: PR\non: pull_request_target\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        )

        skill = ConfigAnalysisSkill()
        risks = skill.analyze_ci_workflows(str(tmp_path))
        assert any(r.risk_type == "pull_request_target" for r in risks)

    def test_analyze_dockerfiles(self, tmp_path):
        (tmp_path / "Dockerfile").write_text(
            "FROM python:3.12\nARG SECRET_KEY=abc\nEXPOSE 8080\n"
        )

        skill = ConfigAnalysisSkill()
        risks = skill.analyze_dockerfiles(str(tmp_path))
        assert any(r.risk_type == "secret_in_build_arg" for r in risks)
        assert any(r.risk_type == "exposed_port" for r in risks)


class TestTestCoverageCheck:
    def test_find_test_files(self, tmp_path):
        # Create source and test files
        (tmp_path / "app.py").write_text("def hello(): pass\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("def test_hello(): pass\n")

        skill = TestCoverageCheckSkill()
        test_files = skill.find_test_files_for("app.py", str(tmp_path))
        assert len(test_files) >= 1
        assert any("test_app.py" in tf for tf in test_files)

    def test_find_no_test_files(self, tmp_path):
        (tmp_path / "utils.py").write_text("def compute(): pass\n")

        skill = TestCoverageCheckSkill()
        test_files = skill.find_test_files_for("utils.py", str(tmp_path))
        assert len(test_files) == 0

    def test_summarize_coverage_gaps(self, tmp_path):
        (tmp_path / "app.py").write_text("def hello(): pass\ndef world(): pass\n")
        (tmp_path / "config.yaml").write_text("key: value\n")

        skill = TestCoverageCheckSkill()
        gaps = skill.summarize_coverage_gaps(["app.py", "config.yaml"], str(tmp_path))

        assert "app.py" in gaps.files_without_tests
        assert "config.yaml" not in gaps.files_without_tests  # non-code files skipped


# ─── Git Archeology Tests ────────────────────────────────────────────────────


def _init_git_repo(path):
    """Helper: create a git repo with one committed file."""
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(path), capture_output=True)
    (path / "app.py").write_text("def hello():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)


class TestGitArcheology:
    def test_execute(self, tmp_path):
        _init_git_repo(tmp_path)
        skill = GitArcheologySkill()
        result = skill.execute(str(tmp_path), ["app.py"])
        assert result.skill_name == "git_archeology"
        assert "1 files" in result.summary
        assert "blame" in result.details

    def test_get_blame(self, tmp_path):
        _init_git_repo(tmp_path)
        skill = GitArcheologySkill()
        blame = skill.get_blame("app.py", str(tmp_path))
        assert blame is not None
        assert blame.total_lines > 0
        assert "T" in blame.authors

    def test_get_file_history(self, tmp_path):
        _init_git_repo(tmp_path)
        skill = GitArcheologySkill()
        history = skill.get_file_history("app.py", str(tmp_path))
        assert len(history) >= 1
        assert history[0].message == "init"


# ─── Code Navigation Execute Test ───────────────────────────────────────────


class TestCodeNavigationExecute:
    def test_execute_returns_skill_result(self, tmp_path):
        (tmp_path / "app.py").write_text("from utils import helper\ndef main(): pass\n")
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        # Need a git repo for find_callers_of_file (uses git grep)
        _init_git_repo(tmp_path)
        # Overwrite with our test files
        (tmp_path / "app.py").write_text("from utils import helper\ndef main(): pass\n")
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "add files"], cwd=str(tmp_path), capture_output=True)

        skill = CodeNavigationSkill()
        result = skill.execute(str(tmp_path), ["app.py"])
        assert result.skill_name == "code_navigation"
        assert "1 files" in result.summary
        assert "imports" in result.details


# ─── Config Analysis Permissions Test ────────────────────────────────────────


class TestConfigAnalysisPermissions:
    def test_detects_wildcard_cors(self, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "server.py").write_text(
            "from flask_cors import CORS\n"
            "CORS(app, origins='*')\n"
        )
        skill = ConfigAnalysisSkill()
        issues = skill.analyze_permissions(str(tmp_path))
        assert len(issues) >= 1
        assert issues[0].issue_type == "permissive_cors"


# ─── Dependency Pyproject Parser Test ────────────────────────────────────────


class TestDependencyPyproject:
    def test_parse_pyproject_deps(self):
        content = (
            "[project]\n"
            "name = \"myapp\"\n"
            "dependencies = [\n"
            '  "flask>=3.0",\n'
            '  "httpx",\n'
            "]\n"
        )
        deps = _parse_pyproject_deps(content)
        assert "flask" in deps
        assert "httpx" in deps


# ─── Test Coverage Execute Test ──────────────────────────────────────────────


class TestTestCoverageExecute:
    def test_execute_returns_skill_result(self, tmp_path):
        (tmp_path / "app.py").write_text("def hello(): pass\n")
        skill = TestCoverageCheckSkill()
        result = skill.execute(str(tmp_path), ["app.py"])
        assert result.skill_name == "test_coverage_check"
        assert "app.py" in result.details["files_without_tests"]
