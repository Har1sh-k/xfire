"""Tests for the context builder."""

import os
import subprocess

from crossfire.config.settings import AnalysisConfig
from crossfire.core.context_builder import (
    ContextBuilder,
    _find_imports_js,
    _find_imports_python,
    _find_test_files,
    _get_directory_structure,
    _read_file_safe,
    _run_git,
    detect_language,
    parse_diff,
)


class TestDetectLanguage:
    def test_python(self):
        assert detect_language("app.py") == "python"

    def test_javascript(self):
        assert detect_language("index.js") == "javascript"

    def test_typescript(self):
        assert detect_language("app.ts") == "typescript"
        assert detect_language("component.tsx") == "typescript"

    def test_go(self):
        assert detect_language("main.go") == "go"

    def test_yaml(self):
        assert detect_language("config.yml") == "yaml"
        assert detect_language("config.yaml") == "yaml"

    def test_dockerfile(self):
        assert detect_language("Dockerfile") == "dockerfile"

    def test_unknown(self):
        assert detect_language("README") is None


class TestParseDiff:
    SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
index abc1234..def5678 100644
--- a/app.py
+++ b/app.py
@@ -10,6 +10,8 @@ def main():
     existing_line()
+    import os
+    os.system(user_input)
     another_line()
"""

    def test_parse_single_file(self):
        files = parse_diff(self.SAMPLE_DIFF)
        assert len(files) == 1
        assert files[0].path == "app.py"
        assert files[0].language == "python"

    def test_parse_hunks(self):
        files = parse_diff(self.SAMPLE_DIFF)
        fc = files[0]
        assert len(fc.diff_hunks) == 1
        hunk = fc.diff_hunks[0]
        assert hunk.old_start == 10
        assert hunk.new_start == 10
        assert "    import os" in hunk.added_lines
        assert "    os.system(user_input)" in hunk.added_lines

    def test_parse_new_file(self):
        diff = """\
diff --git a/new_file.py b/new_file.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,3 @@
+def hello():
+    print("hello")
+    return True
"""
        files = parse_diff(diff)
        assert len(files) == 1
        assert files[0].is_new is True
        assert files[0].is_deleted is False
        assert len(files[0].diff_hunks[0].added_lines) == 3

    def test_parse_deleted_file(self):
        diff = """\
diff --git a/old_file.py b/old_file.py
deleted file mode 100644
index abc1234..0000000
--- a/old_file.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def goodbye():
-    pass
"""
        files = parse_diff(diff)
        assert len(files) == 1
        assert files[0].is_deleted is True
        assert files[0].path == "old_file.py"

    def test_parse_renamed_file(self):
        diff = """\
diff --git a/old_name.py b/new_name.py
similarity index 95%
rename from old_name.py
rename to new_name.py
index abc1234..def5678 100644
--- a/old_name.py
+++ b/new_name.py
@@ -1,3 +1,3 @@
 def hello():
-    print("old")
+    print("new")
     return True
"""
        files = parse_diff(diff)
        assert len(files) == 1
        assert files[0].is_renamed is True
        assert files[0].old_path == "old_name.py"
        assert files[0].path == "new_name.py"

    def test_parse_multi_file_diff(self):
        diff = """\
diff --git a/file1.py b/file1.py
index abc1234..def5678 100644
--- a/file1.py
+++ b/file1.py
@@ -1,3 +1,4 @@
 line1
+new_line
 line2
 line3
diff --git a/file2.js b/file2.js
index abc1234..def5678 100644
--- a/file2.js
+++ b/file2.js
@@ -5,3 +5,4 @@
 const a = 1;
+const b = 2;
 const c = 3;
"""
        files = parse_diff(diff)
        assert len(files) == 2
        assert files[0].path == "file1.py"
        assert files[1].path == "file2.js"
        assert files[1].language == "javascript"

    def test_parse_empty_diff(self):
        files = parse_diff("")
        assert files == []

    def test_parse_multiple_hunks(self):
        diff = """\
diff --git a/app.py b/app.py
index abc1234..def5678 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 line1
+added_at_top
 line2
 line3
@@ -20,3 +21,4 @@
 line20
+added_at_bottom
 line21
 line22
"""
        files = parse_diff(diff)
        assert len(files) == 1
        assert len(files[0].diff_hunks) == 2
        assert files[0].diff_hunks[0].new_start == 1
        assert files[0].diff_hunks[1].new_start == 21


# ─── _run_git Tests ──────────────────────────────────────────────────────────


class TestRunGit:
    def test_successful_command(self, tmp_path):
        # Init a git repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(tmp_path), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(tmp_path), capture_output=True,
        )
        result = _run_git(["status"], str(tmp_path))
        assert result is not None
        assert "branch" in result.lower() or "no commits" in result.lower()

    def test_invalid_command_returns_none(self, tmp_path):
        result = _run_git(["not-a-real-command"], str(tmp_path))
        assert result is None


# ─── _read_file_safe Tests ───────────────────────────────────────────────────


class TestReadFileSafe:
    def test_reads_small_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        assert _read_file_safe(str(f)) == "hello world"

    def test_returns_none_for_missing_file(self, tmp_path):
        assert _read_file_safe(str(tmp_path / "nope.txt")) is None

    def test_returns_none_for_large_file(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 100)
        assert _read_file_safe(str(f), max_size=50) is None


# ─── Import Detection Tests ─────────────────────────────────────────────────


class TestFindImportsPython:
    def test_finds_local_module(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        content = "from utils import helper\nimport os\n"
        related = _find_imports_python(content, "app.py", str(tmp_path))
        paths = [r.path for r in related]
        assert "utils.py" in paths

    def test_ignores_stdlib(self, tmp_path):
        content = "import os\nimport sys\n"
        related = _find_imports_python(content, "app.py", str(tmp_path))
        assert len(related) == 0


class TestFindImportsJs:
    def test_finds_relative_import(self, tmp_path):
        (tmp_path / "utils.js").write_text("export function foo() {}\n")
        content = "import { foo } from './utils';\n"
        related = _find_imports_js(content, "index.js", str(tmp_path))
        paths = [r.path for r in related]
        assert any("utils" in p for p in paths)


# ─── Directory Structure & Test Files Tests ──────────────────────────────────


class TestGetDirectoryStructure:
    def test_basic_structure(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")
        tree = _get_directory_structure(str(tmp_path))
        assert "src" in tree
        assert "main.py" in tree


class TestFindTestFiles:
    def test_finds_matching_test(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("pass")
        result = _find_test_files("app.py", str(tmp_path))
        assert any("test_app.py" in r for r in result)


# ─── ContextBuilder.build_from_diff Tests ────────────────────────────────────


class TestContextBuilderBuildFromDiff:
    def test_build_from_diff_in_git_repo(self, tmp_path):
        """Build context from a diff string in a tmp git repo."""
        # Init a minimal git repo
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=str(tmp_path), capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=str(tmp_path), capture_output=True,
        )
        (tmp_path / "app.py").write_text("def hello(): pass\n")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path), capture_output=True,
        )

        diff_text = """\
diff --git a/app.py b/app.py
index abc..def 100644
--- a/app.py
+++ b/app.py
@@ -1,1 +1,2 @@
 def hello(): pass
+def world(): pass
"""
        builder = ContextBuilder(AnalysisConfig(context_depth="shallow"))
        ctx = builder.build_from_diff(diff_text, str(tmp_path))
        assert ctx.repo_name  # should be detected
        assert len(ctx.files) == 1
        assert ctx.files[0].path == "app.py"
