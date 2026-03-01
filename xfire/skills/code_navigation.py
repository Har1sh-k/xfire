"""Code navigation skill — trace imports, find callers/callees, definitions."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from xfire.skills.base import BaseSkill, SkillResult


class CallSite(BaseModel):
    """A location where a function is called."""

    file_path: str
    line_number: int
    line_content: str
    function_name: str


class ImportRef(BaseModel):
    """An import reference."""

    source_file: str
    imported_module: str
    imported_names: list[str] = Field(default_factory=list)
    resolved_path: str | None = None


class Definition(BaseModel):
    """A symbol definition location."""

    symbol: str
    file_path: str
    line_number: int
    line_content: str
    kind: str = ""  # "function", "class", "variable", etc.


class CodeNavigationSkill(BaseSkill):
    """Trace imports, find callers/callees, and navigate code."""

    name = "code_navigation"

    def execute(self, repo_dir: str, changed_files: list[str], **kwargs: Any) -> SkillResult:
        """Analyze code navigation for changed files."""
        all_imports: list[dict] = []
        all_callers: list[dict] = []
        all_definitions: list[dict] = []

        for file_path in changed_files:
            full_path = os.path.join(repo_dir, file_path)
            if not os.path.isfile(full_path):
                continue

            imports = self.find_imports(file_path, repo_dir)
            all_imports.extend([i.model_dump() for i in imports])

            callers = self.find_callers_of_file(file_path, repo_dir)
            all_callers.extend([c.model_dump() for c in callers])

        summary_parts = [
            f"Analyzed {len(changed_files)} files",
            f"Found {len(all_imports)} import references",
            f"Found {len(all_callers)} caller sites",
        ]

        return SkillResult(
            skill_name=self.name,
            summary="; ".join(summary_parts),
            details={
                "imports": all_imports,
                "callers": all_callers,
                "definitions": all_definitions,
            },
        )

    def find_imports(self, file_path: str, repo_dir: str) -> list[ImportRef]:
        """Find all imports in a file."""
        full_path = os.path.join(repo_dir, file_path)
        try:
            content = open(full_path, errors="replace").read()
        except OSError:
            return []

        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".py":
            return self._find_python_imports(content, file_path, repo_dir)
        elif ext in (".js", ".ts", ".jsx", ".tsx"):
            return self._find_js_imports(content, file_path, repo_dir)
        return []

    def find_callers_of_file(self, file_path: str, repo_dir: str) -> list[CallSite]:
        """Find files that reference/call functions in the given file."""
        callers: list[CallSite] = []

        # Extract function/class names defined in the file
        full_path = os.path.join(repo_dir, file_path)
        try:
            content = open(full_path, errors="replace").read()
        except OSError:
            return []

        # Find defined symbols
        symbols = self._extract_defined_symbols(content, file_path)

        # Search for usages of those symbols in other files
        for defn in symbols[:10]:  # cap at 10 symbols to avoid explosion
            try:
                result = subprocess.run(
                    ["git", "grep", "-n", defn.symbol, "--", "*.py", "*.js", "*.ts"],
                    cwd=repo_dir,
                    capture_output=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=10,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split("\n"):
                        if not line or file_path in line.split(":")[0]:
                            continue  # skip self-references
                        parts = line.split(":", 2)
                        if len(parts) >= 3:
                            callers.append(CallSite(
                                file_path=parts[0],
                                line_number=int(parts[1]) if parts[1].isdigit() else 0,
                                line_content=parts[2].strip(),
                                function_name=defn.symbol,
                            ))
            except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
                continue

        return callers[:20]  # cap results

    def find_definitions(self, symbol: str, repo_dir: str) -> list[Definition]:
        """Find where a symbol is defined."""
        definitions: list[Definition] = []
        patterns = [
            (rf"def\s+{re.escape(symbol)}\s*\(", "function"),
            (rf"class\s+{re.escape(symbol)}\s*[:\(]", "class"),
            (rf"{re.escape(symbol)}\s*=", "variable"),
            (rf"function\s+{re.escape(symbol)}\s*\(", "function"),
            (rf"const\s+{re.escape(symbol)}\s*=", "variable"),
            (rf"let\s+{re.escape(symbol)}\s*=", "variable"),
        ]

        for pattern, kind in patterns:
            try:
                result = subprocess.run(
                    ["git", "grep", "-nE", pattern],
                    cwd=repo_dir,
                    capture_output=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=10,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split("\n"):
                        if not line:
                            continue
                        parts = line.split(":", 2)
                        if len(parts) >= 3:
                            definitions.append(Definition(
                                symbol=symbol,
                                file_path=parts[0],
                                line_number=int(parts[1]) if parts[1].isdigit() else 0,
                                line_content=parts[2].strip(),
                                kind=kind,
                            ))
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

        return definitions

    def _find_python_imports(self, content: str, file_path: str, repo_dir: str) -> list[ImportRef]:
        """Parse Python imports."""
        imports: list[ImportRef] = []
        for match in re.finditer(
            r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w,\s*]+)|import\s+([\w.]+))",
            content,
            re.MULTILINE,
        ):
            if match.group(1):
                module = match.group(1)
                names = [n.strip() for n in match.group(2).split(",")]
            else:
                module = match.group(3)
                names = []

            # Try to resolve to file path
            parts = module.split(".")
            candidates = [
                os.path.join(*parts) + ".py",
                os.path.join(*parts, "__init__.py"),
            ]
            resolved = None
            for c in candidates:
                if os.path.isfile(os.path.join(repo_dir, c)):
                    resolved = c
                    break

            imports.append(ImportRef(
                source_file=file_path,
                imported_module=module,
                imported_names=names,
                resolved_path=resolved,
            ))

        return imports

    def _find_js_imports(self, content: str, file_path: str, repo_dir: str) -> list[ImportRef]:
        """Parse JS/TS imports."""
        imports: list[ImportRef] = []
        for match in re.finditer(
            r"""(?:import\s+(?:\{([^}]+)\}|(\w+))\s+from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))""",
            content,
        ):
            names_group = match.group(1) or match.group(2) or ""
            module = match.group(3) or match.group(4) or ""
            names = [n.strip() for n in names_group.split(",") if n.strip()] if names_group else []

            imports.append(ImportRef(
                source_file=file_path,
                imported_module=module,
                imported_names=names,
            ))

        return imports

    def _extract_defined_symbols(self, content: str, file_path: str) -> list[Definition]:
        """Extract function and class definitions from a file."""
        definitions: list[Definition] = []

        for i, line in enumerate(content.split("\n"), 1):
            # Python defs
            match = re.match(r"(?:def|class|async def)\s+(\w+)", line.strip())
            if match:
                kind = "class" if line.strip().startswith("class") else "function"
                definitions.append(Definition(
                    symbol=match.group(1),
                    file_path=file_path,
                    line_number=i,
                    line_content=line.strip(),
                    kind=kind,
                ))
            # JS/TS defs
            match = re.match(r"(?:export\s+)?(?:function|class)\s+(\w+)", line.strip())
            if match and not line.strip().startswith(("def ", "class ")):
                definitions.append(Definition(
                    symbol=match.group(1),
                    file_path=file_path,
                    line_number=i,
                    line_content=line.strip(),
                    kind="function" if "function" in line else "class",
                ))

        return definitions
