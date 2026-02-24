"""Test coverage check skill — check if security-sensitive code has tests."""

from __future__ import annotations

import os
import re
from typing import Any

from pydantic import BaseModel, Field

from crossfire.skills.base import BaseSkill, SkillResult

# Test file patterns
TEST_PATTERNS: list[tuple[str, str]] = [
    # (test file pattern, source file to match against)
    (r"test_(.+)\.py$", r"\1.py"),
    (r"(.+)_test\.py$", r"\1.py"),
    (r"(.+)\.test\.[jt]sx?$", r"\1"),
    (r"(.+)\.spec\.[jt]sx?$", r"\1"),
    (r"(.+)_test\.go$", r"\1.go"),
    (r"(.+)Test\.java$", r"\1.java"),
    (r"(.+)_spec\.rb$", r"\1.rb"),
]


class CoverageGaps(BaseModel):
    """Summary of test coverage gaps for changed files."""

    files_with_tests: list[str] = Field(default_factory=list)
    files_without_tests: list[str] = Field(default_factory=list)
    functions_without_tests: list[str] = Field(default_factory=list)
    summary: str = ""


class TestCoverageCheckSkill(BaseSkill):
    """Check if security-sensitive code has test coverage."""

    name = "test_coverage_check"

    def execute(self, repo_dir: str, changed_files: list[str], **kwargs: Any) -> SkillResult:
        """Check test coverage for changed files."""
        gaps = self.summarize_coverage_gaps(changed_files, repo_dir)

        return SkillResult(
            skill_name=self.name,
            summary=gaps.summary,
            details={
                "files_with_tests": gaps.files_with_tests,
                "files_without_tests": gaps.files_without_tests,
                "functions_without_tests": gaps.functions_without_tests,
            },
        )

    def find_test_files_for(self, source_file: str, repo_dir: str) -> list[str]:
        """Find test files that likely cover the given source file."""
        basename = os.path.basename(source_file)
        stem = os.path.splitext(basename)[0]
        test_files: list[str] = []

        for root, _dirs, files in os.walk(repo_dir):
            rel_root = os.path.relpath(root, repo_dir)
            # Skip hidden, caches, node_modules
            if any(
                part.startswith(".") or part in ("node_modules", "__pycache__", "venv", ".venv")
                for part in rel_root.split(os.sep)
            ):
                continue

            for f in files:
                for test_pattern, source_pattern in TEST_PATTERNS:
                    match = re.match(test_pattern, f)
                    if match:
                        # Check if the test file name corresponds to our source file
                        matched_stem = match.group(1)
                        if matched_stem == stem:
                            rel_path = os.path.relpath(
                                os.path.join(root, f), repo_dir
                            ).replace("\\", "/")
                            test_files.append(rel_path)

        return test_files

    def check_test_exists(self, function_name: str, source_file: str, repo_dir: str) -> bool:
        """Check if a specific function has a test."""
        test_files = self.find_test_files_for(source_file, repo_dir)

        for test_file in test_files:
            full_path = os.path.join(repo_dir, test_file)
            try:
                content = open(full_path, errors="replace").read()
                # Look for test function that references our function
                if re.search(rf"def test.*{re.escape(function_name)}|{re.escape(function_name)}\(", content):
                    return True
            except OSError:
                continue

        return False

    def summarize_coverage_gaps(self, changed_files: list[str], repo_dir: str) -> CoverageGaps:
        """Generate a summary of test coverage gaps for changed files."""
        with_tests: list[str] = []
        without_tests: list[str] = []
        untested_functions: list[str] = []

        for file_path in changed_files:
            # Skip test files themselves
            basename = os.path.basename(file_path)
            if any(re.match(tp[0], basename) for tp in TEST_PATTERNS):
                continue
            # Skip non-code files
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".rb"):
                continue

            test_files = self.find_test_files_for(file_path, repo_dir)
            if test_files:
                with_tests.append(file_path)
            else:
                without_tests.append(file_path)

            # Check individual functions (Python only for now)
            if ext == ".py":
                full_path = os.path.join(repo_dir, file_path)
                try:
                    content = open(full_path, errors="replace").read()
                    for match in re.finditer(r"def\s+(\w+)\s*\(", content):
                        func_name = match.group(1)
                        if func_name.startswith("_"):
                            continue  # skip private functions
                        if not self.check_test_exists(func_name, file_path, repo_dir):
                            untested_functions.append(f"{file_path}:{func_name}")
                except OSError:
                    pass

        parts: list[str] = []
        total = len(with_tests) + len(without_tests)
        if total > 0:
            parts.append(f"{len(with_tests)}/{total} changed files have test coverage")
        if without_tests:
            parts.append(f"Missing tests: {', '.join(without_tests)}")
        if untested_functions:
            parts.append(f"{len(untested_functions)} public function(s) without tests")
        if not parts:
            parts.append("No testable code files in changed files")

        return CoverageGaps(
            files_with_tests=with_tests,
            files_without_tests=without_tests,
            functions_without_tests=untested_functions,
            summary="; ".join(parts),
        )
