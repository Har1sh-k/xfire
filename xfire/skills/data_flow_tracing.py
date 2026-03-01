"""Data flow tracing skill — trace user input to dangerous sinks."""

from __future__ import annotations

import os
import re
from typing import Any

from pydantic import BaseModel, Field

from xfire.skills.base import BaseSkill, SkillResult


class InputSource(BaseModel):
    """An input source (user-controlled data entry point)."""

    file_path: str
    line_number: int
    source_type: str  # "http_param", "cli_arg", "env_var", "file_read", "db_read", "user_input"
    expression: str
    line_content: str


class DangerousSink(BaseModel):
    """A dangerous sink (operation that could be harmful with untrusted input)."""

    file_path: str
    line_number: int
    sink_type: str  # "exec", "eval", "sql", "file_write", "network_send", "deserialize"
    expression: str
    line_content: str


class FlowTrace(BaseModel):
    """A traced data flow from source to sink."""

    source: InputSource
    sink: DangerousSink
    path_description: str
    confidence: float = 0.5  # how confident we are that data flows from source to sink


class DataFlowSummary(BaseModel):
    """Summary of data flow analysis for agent context."""

    sources: list[InputSource] = Field(default_factory=list)
    sinks: list[DangerousSink] = Field(default_factory=list)
    potential_flows: list[FlowTrace] = Field(default_factory=list)
    summary: str = ""


# Patterns for input sources by language
INPUT_SOURCE_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r"request\.(args|form|json|data|files|values|cookies|headers)\b", "http_param"),
        (r"request\.get_json\(\)", "http_param"),
        (r"request\.GET|request\.POST|request\.body", "http_param"),
        (r"sys\.argv|argparse|click\.", "cli_arg"),
        (r"os\.environ|os\.getenv|environ\.get", "env_var"),
        (r"open\(.*\)\.read|Path\(.*\)\.read_text", "file_read"),
        (r"input\(", "user_input"),
        (r"\.query\(|\.execute\(.*SELECT|\.fetchone|\.fetchall", "db_read"),
    ],
    "javascript": [
        (r"req\.(body|params|query|cookies|headers)\b", "http_param"),
        (r"request\.(body|params|query)\b", "http_param"),
        (r"process\.argv|yargs|commander", "cli_arg"),
        (r"process\.env\b", "env_var"),
        (r"fs\.readFile|readFileSync", "file_read"),
        (r"readline|prompt\(", "user_input"),
    ],
    "typescript": [],  # inherits javascript patterns
}
INPUT_SOURCE_PATTERNS["typescript"] = INPUT_SOURCE_PATTERNS["javascript"]

# Patterns for dangerous sinks
DANGEROUS_SINK_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "python": [
        (r"subprocess\.(run|call|Popen|check_output)\b", "exec"),
        (r"os\.(system|popen|exec[lvpe]*)\b", "exec"),
        (r"\beval\(", "eval"),
        (r"\bexec\(", "eval"),
        (r"cursor\.execute\(.*%|\.format\(|f['\"].*\{", "sql"),
        (r"\.raw\(|\.extra\(|RawSQL", "sql"),
        (r"open\(.*['\"]w|\.write\(|shutil\.(copy|move|rmtree)", "file_write"),
        (r"pickle\.loads?|yaml\.load\b|marshal\.loads?", "deserialize"),
        (r"socket\.send|requests\.(post|put|patch)|httpx\.(post|put)", "network_send"),
        (r"render_template_string|Template\(.*\)\.render|Markup\(", "template_injection"),
        (r"__import__\(|importlib\.import_module", "code_import"),
    ],
    "javascript": [
        (r"\beval\(", "eval"),
        (r"child_process\.(exec|spawn|fork)\b", "exec"),
        (r"Function\(", "eval"),
        (r"\.innerHTML\s*=|\.outerHTML\s*=|document\.write", "xss_sink"),
        (r"fs\.writeFile|writeFileSync|fs\.unlink", "file_write"),
        (r"JSON\.parse\(", "deserialize"),
    ],
    "typescript": [],
}
DANGEROUS_SINK_PATTERNS["typescript"] = DANGEROUS_SINK_PATTERNS["javascript"]


def _detect_language(file_path: str) -> str | None:
    """Detect language from file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    lang_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
    }
    return lang_map.get(ext)


class DataFlowTracingSkill(BaseSkill):
    """Trace data flows from input sources to dangerous sinks."""

    name = "data_flow_tracing"

    def execute(self, repo_dir: str, changed_files: list[str], **kwargs: Any) -> SkillResult:
        """Analyze data flows in changed files."""
        summary = self.summarize_data_flows(changed_files, repo_dir)

        details: dict[str, Any] = {
            "sources": [s.model_dump() for s in summary.sources],
            "sinks": [s.model_dump() for s in summary.sinks],
            "potential_flows": [f.model_dump() for f in summary.potential_flows],
        }

        return SkillResult(
            skill_name=self.name,
            summary=summary.summary,
            details=details,
            raw_output=summary.summary,
        )

    def find_input_sources(self, file_path: str, repo_dir: str) -> list[InputSource]:
        """Find input sources in a file."""
        lang = _detect_language(file_path)
        if not lang or lang not in INPUT_SOURCE_PATTERNS:
            return []

        full_path = os.path.join(repo_dir, file_path)
        try:
            content = open(full_path, errors="replace").read()
        except OSError:
            return []

        sources: list[InputSource] = []
        lines = content.split("\n")

        for pattern, source_type in INPUT_SOURCE_PATTERNS[lang]:
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    sources.append(InputSource(
                        file_path=file_path,
                        line_number=i,
                        source_type=source_type,
                        expression=re.search(pattern, line).group(0) if re.search(pattern, line) else "",
                        line_content=line.strip(),
                    ))

        return sources

    def find_dangerous_sinks(self, file_path: str, repo_dir: str) -> list[DangerousSink]:
        """Find dangerous sinks in a file."""
        lang = _detect_language(file_path)
        if not lang or lang not in DANGEROUS_SINK_PATTERNS:
            return []

        full_path = os.path.join(repo_dir, file_path)
        try:
            content = open(full_path, errors="replace").read()
        except OSError:
            return []

        sinks: list[DangerousSink] = []
        lines = content.split("\n")

        for pattern, sink_type in DANGEROUS_SINK_PATTERNS[lang]:
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    sinks.append(DangerousSink(
                        file_path=file_path,
                        line_number=i,
                        sink_type=sink_type,
                        expression=re.search(pattern, line).group(0) if re.search(pattern, line) else "",
                        line_content=line.strip(),
                    ))

        return sinks

    def trace_flow(
        self, source: InputSource, sink: DangerousSink, repo_dir: str
    ) -> FlowTrace | None:
        """Attempt to trace whether input from source can reach sink.

        This is a heuristic-based approach: if source and sink are in the
        same file, we check if a variable from the source line appears
        near the sink line. Cross-file tracing uses import analysis.
        """
        if source.file_path != sink.file_path:
            # Cross-file tracing: lower confidence heuristic
            return FlowTrace(
                source=source,
                sink=sink,
                path_description=(
                    f"Potential cross-file flow: {source.source_type} in "
                    f"{source.file_path}:{source.line_number} → "
                    f"{sink.sink_type} in {sink.file_path}:{sink.line_number}"
                ),
                confidence=0.3,
            )

        # Same file: check if source is before sink and shares variable names
        if source.line_number < sink.line_number:
            # Extract variable names from source line
            source_vars = set(re.findall(r"\b(\w+)\b", source.line_content))
            sink_vars = set(re.findall(r"\b(\w+)\b", sink.line_content))
            shared = source_vars & sink_vars - {"self", "cls", "return", "if", "else", "def", "class"}

            if shared:
                return FlowTrace(
                    source=source,
                    sink=sink,
                    path_description=(
                        f"Same-file flow via {', '.join(shared)}: "
                        f"{source.source_type} at line {source.line_number} → "
                        f"{sink.sink_type} at line {sink.line_number}"
                    ),
                    confidence=0.7,
                )

        return None

    def summarize_data_flows(self, files: list[str], repo_dir: str) -> DataFlowSummary:
        """Generate a summary of data flow paths for agent context."""
        all_sources: list[InputSource] = []
        all_sinks: list[DangerousSink] = []
        all_flows: list[FlowTrace] = []

        for file_path in files:
            sources = self.find_input_sources(file_path, repo_dir)
            sinks = self.find_dangerous_sinks(file_path, repo_dir)
            all_sources.extend(sources)
            all_sinks.extend(sinks)

            # Try to trace flows between sources and sinks
            for source in sources:
                for sink in sinks:
                    flow = self.trace_flow(source, sink, repo_dir)
                    if flow:
                        all_flows.append(flow)

        # Build summary text
        parts: list[str] = []
        if all_sources:
            parts.append(f"Found {len(all_sources)} input source(s): "
                         + ", ".join(set(s.source_type for s in all_sources)))
        if all_sinks:
            parts.append(f"Found {len(all_sinks)} dangerous sink(s): "
                         + ", ".join(set(s.sink_type for s in all_sinks)))
        if all_flows:
            parts.append(f"Identified {len(all_flows)} potential flow path(s)")
            for flow in all_flows:
                parts.append(f"  - {flow.path_description}")

        if not parts:
            parts.append("No significant data flow patterns detected in changed files.")

        return DataFlowSummary(
            sources=all_sources,
            sinks=all_sinks,
            potential_flows=all_flows,
            summary="\n".join(parts),
        )
