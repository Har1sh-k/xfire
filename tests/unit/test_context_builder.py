"""Tests for the context builder."""

from crossfire.core.context_builder import detect_language, parse_diff


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
