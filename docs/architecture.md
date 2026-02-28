# CrossFire Architecture

> Generated from source code analysis. Reflects what the code **actually does**.
>
> Last updated: 2026-02-28

---

## Pipeline Overview

CrossFire has three pipelines.

| Pipeline | Command | Context Source |
|----------|---------|---------------|
| **Code Review** | `code-review .` | Whole repo — all source files, no diff |
| **PR Review** | `analyze-pr` | GitHub PR diff via API |
| **Baseline-Aware Scan** | `scan .` | Any commit range, persistent baseline |

---

### Code Review Pipeline

Audits the entire codebase as it currently stands. No diff, no PR, no commits.

```
crossfire code-review .
        |
        v
+------------------------------+
|  ContextBuilder               |  build_from_repo(repo_dir, max_files=150)
|  .build_from_repo()           |  Walks all source files (.py .ts .go .rs .yaml …)
|                               |  Full file contents — no diff hunks
+-------------+----------------+
              |
              v
+------------------------------+
|     Intent Inferrer           |  Multi-signal heuristic: what does this repo DO?
+-------------+----------------+
              |
              v
+------------------------------+
|     Skills (pre-compute)      |  Data flow tracing, git blame, config risks,
|                               |  dependency analysis, test gaps, code navigation
+-------------+----------------+
              |
              v
+------------------------------+
|  Independent Agent Reviews    |  CODE_REVIEW_SYSTEM_PROMPT
|  +--------++--------++--------+  "audit the codebase" — not "review a PR"
|  | Claude || Codex  || Gemini |  Agents trace data flows end-to-end across all files
|  +--------++--------++--------+  build_code_review_prompt() — full files, no diff section
+-------------+----------------+
              |
              v
+------------------------------+
|   Finding Synthesizer         |  Union-find clustering, cross-validation boost,
|                               |  purpose-aware adjustments, debate routing
+-------------+----------------+
              |
              v
+--------------------------------------+
|   Adversarial Debate (per finding)    |
|   Round 1: Prosecutor argues          |
|            Defense responds            |
|   Round 2: Judge asks questions       |  (if defense disagrees)
|            Both sides respond          |
|            Judge issues final ruling   |
+-------------+------------------------+
              |
              v
+------------------------------+
|   Policy Engine               |  Suppression rules (category, file, title)
+-------------+----------------+
              |
              v
+------------------------------+
|    Output / Reports           |  Markdown, JSON, SARIF, CI gating
+------------------------------+
```

---

### PR Review Pipeline

Reviews a GitHub pull request — what changed and what security implications those changes introduce.

```
crossfire analyze-pr --repo owner/repo --pr 123
        |
        v
+------------------------------+
|  GitHub API                   |  PR metadata, diff, file contents (head+base),
|  integrations/github/         |  commits, README, repo info, manifest files
+-------------+----------------+
              |
              v
+------------------------------+
|     Context Builder           |  parse_diff() + file enrichment + imports + blame
|     + Intent Inferrer         |  Multi-signal heuristic: what does this repo DO?
+-------------+----------------+
              |
              v
+------------------------------+
|     Skills (pre-compute)      |  Data flow tracing, git archeology, config analysis,
|                               |  dependency analysis, test coverage, code navigation
+-------------+----------------+
              |
              v
+------------------------------+
|  Independent Agent Reviews    |  REVIEW_SYSTEM_PROMPT — "review this PR"
|  +--------++--------++--------+  build_review_prompt() — diff hunks + full changed files
|  | Claude || Codex  || Gemini |
|  +--------++--------++--------+
+-------------+----------------+
              |
              v
+------------------------------+
|   Finding Synthesizer         |  Cluster, dedupe, purpose-aware adjustments
+-------------+----------------+
              |
              v
+--------------------------------------+
|   Adversarial Debate (per finding)    |
|   (2-round, judge-led)                |
+-------------+------------------------+
              |
              v
+------------------------------+
|   Policy Engine               |  Suppressions
+------------------------------+
              |
              v
+------------------------------+
|    Output / Reports + GitHub  |  Markdown, JSON, SARIF, optional PR comment
|    PR comment (--post-comment)|
+------------------------------+
```

### Baseline-Aware Scan Pipeline

```
crossfire scan . --base main --head feature
        |
        v
+------------------------------+
|     DiffResolver              |  Resolves input mode → DiffResult(diff, head, base)
|     (6 modes)                 |  from_refs, from_range, from_patch, from_since_last_scan,
+-------------+----------------+  from_since_date, from_last_n
              |
              v
+---------------------------------------------+
|     BaselineManager                          |
|     .crossfire/baseline/ exists?              |
|       NO  → IntentInferrer on whole repo      |  builds context.md + intent.json
|       YES → FastModel intent-change check     |  if changed → rebuild baseline
+-------------+-------------------------------+
              |
              v
+------------------------------+
|     FastModel                 |  claude-haiku-4-5 via API (CLI fallback)
|     build_context_prompt()    |  adapts AUDIT_TEMPLATE to this repo's exact
+-------------+----------------+  capabilities + trust boundaries
              |
              v
+------------------------------+
|     Context Builder           |  diff hunks + full changed files, reads from base_commit git ref
+-------------+----------------+  (no intent re-inference — uses baseline.intent)
              |
              v
+------------------------------+
|     Skills (pre-compute)      |  data flow, git blame, config risks, dependency analysis, test gaps
+-------------+----------------+
              |
              v
+------------------------------+
|  Independent Agent Reviews    |  system_prompt = repo-specific context prompt
|  (with context_system_prompt) |  (not generic REVIEW_SYSTEM_PROMPT)
+-------------+----------------+
              |
              v
+------------------------------+
|   Finding Synthesizer         |  union-find clustering, cross-validation boost, purpose-aware adjustments
+-------------+----------------+
              |
              v
+------------------------------+
|   baseline.filter_known()     |  Delta scanning: split new vs already-confirmed
|                               |  fingerprint = sha256(category:file:title[:50])[:16]
+-------------+----------------+
              |
              v
+--------------------------------------+
|   Adversarial Debate                  |  Only new_findings enter debate
|   (new findings only)                 |
+-------------+------------------------+
              |
              v
+------------------------------+
|   Policy Engine               |  suppression rules (category, file, title)
+-------------+----------------+
              |
              v
+------------------------------+
|   baseline.update_after_scan()|  [AUTO] persists confirmed findings to
|   → scan_state.json           |  known_findings.json for future delta scans
|   → known_findings.json       |
+-------------+----------------+
              |
              v
+------------------------------+
|    Output / Reports           |  Summary: "X new | Y known skipped"
+------------------------------+
```

---

## 1. Component Inventory

| Component | File | Purpose (from code) | Status | Internal Dependencies |
|-----------|------|---------------------|--------|-----------------------|
| **CLI Entry Point** | `crossfire/cli.py` | Typer app with 12+ commands: `code-review`, `analyze-pr`, `analyze-diff`, `baseline`, `scan`, `report`, `init`, `config-check`, `demo`, `test-llm`, `auth login`, `auth status`, `debates`. `code-review` runs the whole-repo Code Review Pipeline. `baseline` builds `.crossfire/baseline/`. `scan` supports 6 input modes, auto-builds baseline, and prints delta summary. All pipeline commands accept `--debug` (live log + markdown file), `--silent` (suppress output), `--debate` (stream live debate chat as each agent responds), `--format`, `--output`. `test-llm` tests every configured agent with `AgentTestUI`. `debates` re-renders debate chat from saved JSON. | ✅ IMPLEMENTED | `config.settings`, `core.orchestrator`, `core.baseline`, `core.diff_resolver`, `agents.fast_model`, `core.models`, `core.severity`, `core.context_builder`, `output.*`, `integrations.github.comment_poster`, `cli_ui`, `auth.store` |
| **Live UI** | `crossfire/cli_ui.py` | Terminal display layer. `HackerUI` — `rich.live.Live`-based display: braille phase spinner (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) at 10fps; rotating-circle agent icons (`◐◓◑◒`) at 6fps (visually distinct). `show_debate=True` streams live debate chat by intercepting `debate.argument`, `debate.judge_questions`, and `debate.verdict` structlog events above the Live area: `_print_debate_argument()` renders speech bubbles with severity badge `[HIGH]`/`[CRITICAL]` on the finding header; `_print_judge_questions()` renders a dim indented blockquote panel (not a speech bubble); `_print_debate_verdict()` renders a colored consensus panel. Optional `debug_mode` live-log ring buffer (last 8 events). structlog `processor()` raises `structlog.DropEvent()` to suppress stdout during pipeline. `AgentTestUI` — `transient=True` Live display for `test-llm`. Module-level `render_banner()` and `render_stats()`. | ✅ IMPLEMENTED | `rich.live`, `rich.text`, `rich.panel`, `rich.padding`, `structlog` |
| **Debate Chat Renderer** | `crossfire/output/debate_view.py` | `render_debates(report, console)` renders all debates as a hacker-style terminal chat. Prosecution: left-aligned, red `rich.panel.Panel`. Defense: indented 6 spaces via `rich.padding.Padding`, cyan border. Judge: bright-white border, ⚖ icon. Consensus box: colored by outcome (confirmed=red, rejected=green, modified/inconclusive=yellow). Internal helpers (`_bubble()`, `_CONSENSUS_CONFIG`, `_SEVERITY_STYLE`, `_RESPONSE_INDENT`) are imported by `HackerUI` for live streaming. `render_debates()` itself is called only by `crossfire debates --input`. | ✅ IMPLEMENTED | `rich.console`, `rich.panel`, `rich.padding`, `rich.rule`, `rich.text` |
| **Debug Log Writer** | `crossfire/output/debug_log.py` | `DebugCollector` — thread-safe structlog processor that buffers all pipeline log events in memory (time, level, event, extras). `write_debug_markdown(report, collector, command_info)` writes a timestamped markdown file (`crossfire-debug-YYYYMMDD-HHMMSS.md`) containing: pipeline events table, intent profile, context summary, full agent reviews with reasoning traces, debate transcripts, and the complete final report. Called by `--debug` flag on pipeline commands. | ✅ IMPLEMENTED | `threading`, `pathlib`, `output.markdown_report` |
| **Auth Store** | `crossfire/auth/store.py` | `AuthStore` Pydantic model persisted at `.crossfire/auth.json`. Stores OAuth tokens and CLI credentials for Claude, Codex, and Gemini. CLI commands: `crossfire auth login --provider <name> [--token <val>]`, `crossfire auth status`. | ✅ IMPLEMENTED | `pydantic`, `pathlib` |
| **Default Config** | `crossfire/config/defaults.py` | `DEFAULT_CONFIG` dict — nested default values for all settings, including `fast_model` section | ✅ IMPLEMENTED | _(none)_ |
| **Settings Loader** | `crossfire/config/settings.py` | Loads config with priority: CLI > env > YAML > defaults. Pydantic models for each config section. Added `FastModelConfig` + `fast_model` field on `CrossFireSettings` | ✅ IMPLEMENTED | `config.defaults` |
| **Core Models** | `crossfire/core/models.py` | 25+ Pydantic v2 models: `PRContext`, `Finding`, `DebateRecord`, `CrossFireReport`, enums, etc. | ✅ IMPLEMENTED | _(none — leaf module)_ |
| **Context Builder** | `crossfire/core/context_builder.py` | Builds `PRContext` from 5 sources: (1) `build_from_repo()` — whole-repo walk for Code Review Pipeline, reads all source files up to `max_files`, no diff hunks; (2) GitHub PR via API; (3) local diff/patch; (4) staged changes; (5) git refs range. Shared helpers: diff parsing, file enrichment, imports, blame, test discovery. | ✅ IMPLEMENTED | `config.settings.AnalysisConfig`, `core.models`, `integrations.github.pr_loader` |
| **Intent Inferrer** | `crossfire/core/intent_inference.py` | Heuristic-first intent inference with optional LLM enrichment. `IntentInferrer.infer(context)` runs the heuristic always. `infer_with_llm(context, agent, inferrer)` runs heuristic first, sends serialized result to LLM via `_format_heuristic_for_prompt()`, parses LLM `IntentProfile`, then merges via `_merge_profiles()`. Merge rules: scalars → LLM overrides if non-empty; lists → union with dedup; trust boundaries → merge by name; security controls → merge by `(type, location)`. On LLM failure: returns heuristic profile (already computed, zero wasted work). Call sites in `orchestrator.py` and `baseline.py` pass `inferrer` to enable enrichment. | ✅ IMPLEMENTED | `config.settings.RepoConfig`, `core.models` |
| **Finding Synthesizer** | `crossfire/core/finding_synthesizer.py` | Union-find clustering, merges, dedupes findings from multiple agents. Cross-validation boost. Purpose-aware adjustments. Debate routing tags | ✅ IMPLEMENTED | `core.models` |
| **Policy Engine** | `crossfire/core/policy_engine.py` | Applies suppression rules (category, file pattern, title pattern) to findings | ✅ IMPLEMENTED | `core.models` |
| **Severity Gate** | `crossfire/core/severity.py` | `should_fail_ci()` — checks if findings breach severity/confidence threshold | ✅ IMPLEMENTED | `core.models` |
| **Orchestrator** | `crossfire/core/orchestrator.py` | Four top-level entry points: `code_review()` — whole-repo audit using `build_from_repo()` + `CODE_REVIEW_SYSTEM_PROMPT`; `analyze_pr()` — GitHub PR via API; `analyze_diff()` — local diff/patch/staged; `scan_with_baseline()` — baseline-aware delta scan. Shared `_run_pipeline()`, `_run_skills()`, `_compute_overall_risk()`. `_build_scan_summary()` includes delta counts. | ✅ IMPLEMENTED | `agents.debate_engine`, `agents.review_engine`, `agents.prompts.review_prompt`, `agents.prompts.context_prompt`, `config.settings`, `core.baseline`, `core.context_builder`, `core.finding_synthesizer`, `core.intent_inference`, `core.models`, `core.policy_engine`, `skills.*` (all 6) |
| **Baseline Manager** | `crossfire/core/baseline.py` | Reads/writes `.crossfire/baseline/`. `build()` runs `IntentInferrer` on whole repo, writes context.md + intent.json + scan_state.json + known_findings.json (PID lock prevents concurrent builds). `load()` deserializes all files. `check_intent_changed()` delegates to fast model. `update_after_scan()` persists confirmed findings. `filter_known()` splits new vs already-known. `_fingerprint()` = `sha256(category:file:title[:50])[:16]`. | ✅ IMPLEMENTED | `core.intent_inference`, `core.models`, `agents.fast_model`, `agents.prompts.context_prompt` |
| **Diff Resolver** | `crossfire/core/diff_resolver.py` | Resolves all `crossfire scan` input modes into `DiffResult(diff_text, head_commit, base_commit, commit_range_desc)`. 6 static methods: `from_refs`, `from_range`, `from_patch`, `from_since_last_scan`, `from_since_date`, `from_last_n`. Uses `_run_git()` helper (same pattern as `context_builder.py`). | ✅ IMPLEMENTED | _(subprocess only)_ |
| **Base Agent** | `crossfire/agents/base.py` | Abstract base with CLI + API dual-mode execution, JSON parsing, subprocess runner (with FileNotFoundError → AgentError conversion) | ✅ IMPLEMENTED | `config.settings.AgentConfig` |
| **Claude Adapter** | `crossfire/agents/claude_adapter.py` | CLI: `claude -p "..." --output-format json --system-prompt "..."`. API: `anthropic.AsyncAnthropic.messages.create()` with timeout | ✅ IMPLEMENTED | `agents.base` |
| **Codex Adapter** | `crossfire/agents/codex_adapter.py` | CLI: `codex -q "{system+user prompt}"`. API: `openai.AsyncOpenAI.chat.completions.create()` with timeout | ✅ IMPLEMENTED | `agents.base` |
| **Gemini Adapter** | `crossfire/agents/gemini_adapter.py` | CLI: `gemini "{system+user prompt}"`. API: `google.generativeai.GenerativeModel.generate_content_async()` with `asyncio.wait_for` timeout | ✅ IMPLEMENTED | `agents.base` |
| **Fast Model** | `crossfire/agents/fast_model.py` | Lightweight API-first, CLI-fallback model for cheap inference. `_call_api()` uses `anthropic.AsyncAnthropic` with `ANTHROPIC_API_KEY`. Falls back to `_call_cli()` (subprocess) if key missing. Used for intent-change detection and context-aware prompt generation. Raises `FastModelUnavailable` if both paths fail. | ✅ IMPLEMENTED | `config.settings.FastModelConfig` |
| **Context Prompt** | `crossfire/agents/prompts/context_prompt.py` | `check_intent_changed()` — fast model checks if diff changes security model (returns bool). `build_context_system_prompt()` — adapts `AUDIT_TEMPLATE` to repo context using fast model. Both fall back gracefully on `FastModelUnavailable`. | ✅ IMPLEMENTED | `agents.fast_model`, `core.baseline`, `agents.prompts.review_prompt` |
| **Review Engine** | `crossfire/agents/review_engine.py` | Dispatches review prompt to all enabled agents in parallel (`asyncio.gather`), parses structured JSON responses into `AgentReview` with case-insensitive enum parsing via `_parse_enum_flexible()`. Added optional `system_prompt` param — if provided, overrides `REVIEW_SYSTEM_PROMPT`; backward compatible (None → default). | ✅ IMPLEMENTED | `agents.base`, `agents.claude_adapter`, `agents.codex_adapter`, `agents.gemini_adapter`, `agents.prompts.review_prompt`, `config.settings`, `core.models` |
| **Debate Engine** | `crossfire/agents/debate_engine.py` | 2-round judge-led debate: Round 1 prosecution/defense, optional Round 2 judge-led clarification (if defense disagrees). Evidence-driven role assignment | ✅ IMPLEMENTED | `agents.base`, `agents.claude_adapter`, `agents.codex_adapter`, `agents.gemini_adapter`, `agents.consensus`, `agents.prompts.prosecutor_prompt`, `agents.prompts.defense_prompt`, `agents.prompts.judge_prompt`, `config.settings`, `core.models` |
| **Consensus Logic** | `crossfire/agents/consensus.py` | Evidence-quality-based verdict: judge position + cross-checks + purpose-aware override + minimum evidence thresholds | ✅ IMPLEMENTED | `core.models` |
| **Review Prompt** | `crossfire/agents/prompts/review_prompt.py` | Two system prompts + two user prompt builders: `REVIEW_SYSTEM_PROMPT` + `build_review_prompt()` for PR/diff review (diff-focused); `CODE_REVIEW_SYSTEM_PROMPT` + `build_code_review_prompt()` for whole-repo audit (full-file, no diff section, "audit the codebase" framing). Both protected by `inject_guard_preamble()`. | ✅ IMPLEMENTED | `core.models`, `agents.prompts.guardrails` |
| **Prosecutor Prompt** | `crossfire/agents/prompts/prosecutor_prompt.py` | System prompt + `build_prosecutor_prompt()` | ✅ IMPLEMENTED | _(none)_ |
| **Defense Prompt** | `crossfire/agents/prompts/defense_prompt.py` | System prompt + `build_defense_prompt()` | ✅ IMPLEMENTED | _(none)_ |
| **Judge Prompt** | `crossfire/agents/prompts/judge_prompt.py` | System prompt + `build_judge_prompt()` | ✅ IMPLEMENTED | _(none)_ |
| **Skill Base** | `crossfire/skills/base.py` | `BaseSkill` ABC + `SkillResult` model | ✅ IMPLEMENTED | _(none)_ |
| **Data Flow Tracing** | `crossfire/skills/data_flow_tracing.py` | Regex-based source→sink detection for Python/JS/TS. Same-file variable sharing heuristic | ✅ IMPLEMENTED | `skills.base` |
| **Git Archeology** | `crossfire/skills/git_archeology.py` | Git blame, file history, security commit search, code age, contributors | ✅ IMPLEMENTED | `skills.base` |
| **Config Analysis** | `crossfire/skills/config_analysis.py` | CI workflow risk patterns, Docker risk patterns, security config summary | ✅ IMPLEMENTED | `skills.base` |
| **Dependency Analysis** | `crossfire/skills/dependency_analysis.py` | Manifest diff (requirements.txt, package.json, pyproject.toml), risky package detection | ✅ IMPLEMENTED | `skills.base` |
| **Test Coverage Check** | `crossfire/skills/test_coverage_check.py` | Test file discovery, per-function test existence check, coverage gap summary | ✅ IMPLEMENTED | `skills.base` |
| **Code Navigation** | `crossfire/skills/code_navigation.py` | Import tracing, caller discovery via `git grep`, symbol definition search | ✅ IMPLEMENTED | `skills.base` |
| **Markdown Report** | `crossfire/output/markdown_report.py` | Generates markdown report: summary table, findings by status, debate logs, purpose assessments | ✅ IMPLEMENTED | `core.models` |
| **JSON Report** | `crossfire/output/json_report.py` | `report.model_dump_json(indent=2)` — direct Pydantic serialization | ✅ IMPLEMENTED | `core.models` |
| **SARIF Report** | `crossfire/output/sarif_report.py` | SARIF v2.1.0 with rules (help text), results (partialFingerprints, rank, code snippets, relatedLocations), run properties. Filters rejected findings | ✅ IMPLEMENTED | `core.models` |
| **Debate Chat Renderer** | `crossfire/output/debate_view.py` | See entry above in CLI/UI section | ✅ IMPLEMENTED | `rich.*` |
| **Debug Log Writer** | `crossfire/output/debug_log.py` | See entry above in CLI/UI section | ✅ IMPLEMENTED | `output.markdown_report` |
| **PR Loader** | `crossfire/integrations/github/pr_loader.py` | Async httpx client: fetches PR metadata, diff, file contents (head+base in parallel), README, repo info, commits, manifest files. Populates `config_files`, `ci_config_files`, `directory_structure` | ✅ IMPLEMENTED | `config.settings.AnalysisConfig`, `core.models`, `core.context_builder.parse_diff` |
| **Comment Poster** | `crossfire/integrations/github/comment_poster.py` | Posts/updates review comment on GitHub PR via Issues API | ✅ IMPLEMENTED | _(none — uses httpx directly)_ |

### Status Legend
- ✅ **IMPLEMENTED** — Code is present, connected, and functional

---

## 2. Full Wiring Diagram

```mermaid
flowchart TD
    subgraph CLI["cli.py — Typer Entry Point"]
        CMD_CR["code-review"]
        CMD_PR["analyze-pr"]
        CMD_DIFF["analyze-diff"]
        CMD_BASELINE["baseline"]
        CMD_SCAN["scan"]
        CMD_REPORT["report"]
        CMD_INIT["init"]
        CMD_CHECK["config-check"]
        CMD_DEMO["demo"]
        CMD_TESTLLM["test-llm"]
        CMD_AUTH["auth login / auth status"]
        CMD_DEBATES["debates"]
    end

    subgraph UI["CLI Display Layer"]
        HACKER_UI["cli_ui.py<br/>HackerUI (live phase spinners)<br/>AgentTestUI (test-llm live display)<br/>render_banner() render_stats()"]
        DEBUG_COLL["output/debug_log.py<br/>DebugCollector (structlog processor)<br/>write_debug_markdown()"]
        DEBATE_VIEW["output/debate_view.py<br/>render_debates() (chat renderer)"]
    end

    subgraph AUTH["Auth Layer"]
        AUTH_STORE["auth/store.py<br/>AuthStore (.crossfire/auth.json)"]
    end

    subgraph CONFIG["Config Layer"]
        DEFAULTS["config/defaults.py<br/>DEFAULT_CONFIG dict"]
        SETTINGS["config/settings.py<br/>load_settings() → CrossFireSettings"]
    end

    subgraph ORCHESTRATOR["core/orchestrator.py — CrossFireOrchestrator"]
        ORCH_CR["code_review()"]
        ORCH_PR["analyze_pr()"]
        ORCH_DIFF["analyze_diff()"]
        ORCH_SCAN["scan_with_baseline()"]
        PIPELINE["_run_pipeline()"]
        SKILL_RUN["_run_skills()"]
        RISK["_compute_overall_risk()"]
        SUMMARY["_build_summary()"]
        SCAN_SUMMARY["_build_scan_summary()"]
    end

    subgraph CONTEXT["Context Building"]
        CTX["core/context_builder.py<br/>ContextBuilder"]
        CTX_DIFF["parse_diff()"]
        CTX_ENRICH["_enrich_file_context()"]
        PR_LOAD["integrations/github/pr_loader.py<br/>load_pr_context()"]
    end

    subgraph INTENT["Intent Inference"]
        INFERRER["core/intent_inference.py<br/>IntentInferrer.infer()"]
    end

    subgraph SKILLS["Skills (Pre-compute)"]
        S_DF["data_flow_tracing.py<br/>DataFlowTracingSkill"]
        S_GA["git_archeology.py<br/>GitArcheologySkill"]
        S_CA["config_analysis.py<br/>ConfigAnalysisSkill"]
        S_DA["dependency_analysis.py<br/>DependencyAnalysisSkill"]
        S_TC["test_coverage_check.py<br/>TestCoverageCheckSkill"]
        S_CN["code_navigation.py<br/>CodeNavigationSkill"]
    end

    subgraph BASELINE_SYS["Baseline System"]
        BASELINE_MGR["core/baseline.py<br/>BaselineManager"]
        DIFF_RES["core/diff_resolver.py<br/>DiffResolver"]
        FAST_MODEL["agents/fast_model.py<br/>FastModel"]
        CTX_PROMPT["agents/prompts/context_prompt.py<br/>check_intent_changed()<br/>build_context_system_prompt()"]
    end

    subgraph AGENTS["Agent Reviews"]
        REVIEW["agents/review_engine.py<br/>ReviewEngine"]
        REVIEW_PROMPT["agents/prompts/review_prompt.py<br/>build_review_prompt()<br/>CODE_REVIEW_SYSTEM_PROMPT<br/>build_code_review_prompt()"]
        CLAUDE["agents/claude_adapter.py<br/>ClaudeAgent"]
        CODEX["agents/codex_adapter.py<br/>CodexAgent"]
        GEMINI["agents/gemini_adapter.py<br/>GeminiAgent"]
        BASE["agents/base.py<br/>BaseAgent ABC"]
    end

    subgraph SYNTHESIS["Finding Synthesis"]
        SYNTH["core/finding_synthesizer.py<br/>FindingSynthesizer"]
    end

    subgraph DEBATE["Adversarial Debate"]
        DEBATE_ENG["agents/debate_engine.py<br/>DebateEngine"]
        PROS_PROMPT["prompts/prosecutor_prompt.py"]
        DEF_PROMPT["prompts/defense_prompt.py"]
        JUDGE_PROMPT["prompts/judge_prompt.py"]
        CONSENSUS["agents/consensus.py<br/>compute_consensus()"]
    end

    subgraph POLICY["Policy & Gating"]
        POLICY_ENG["core/policy_engine.py<br/>PolicyEngine"]
        SEVERITY["core/severity.py<br/>should_fail_ci()"]
    end

    subgraph OUTPUT["Report Generation"]
        MD_RPT["output/markdown_report.py"]
        JSON_RPT["output/json_report.py"]
        SARIF_RPT["output/sarif_report.py"]
        COMMENT["integrations/github/comment_poster.py"]
    end

    subgraph MODELS["core/models.py — 25+ Pydantic Models"]
        M_CTX["PRContext, FileContext, DiffHunk"]
        M_INTENT["IntentProfile, TrustBoundary, SecurityControl"]
        M_FIND["Finding, Evidence, PurposeAssessment"]
        M_REVIEW["AgentReview"]
        M_DEBATE["DebateRecord, AgentArgument, CitedEvidence"]
        M_REPORT["CrossFireReport"]
    end

    %% CLI → Config
    CMD_CR -->|"load_settings(repo_dir)"| SETTINGS
    CMD_PR -->|"load_settings(cli_overrides)"| SETTINGS
    CMD_DIFF -->|"load_settings(repo_dir, cli_overrides)"| SETTINGS
    CMD_SCAN -->|"load_settings(repo_dir, cli_overrides)"| SETTINGS
    CMD_BASELINE -->|"load_settings(repo_dir)"| SETTINGS
    CMD_CHECK -->|"load_settings(repo_dir)"| SETTINGS
    CMD_DEMO -->|"load_settings()"| SETTINGS
    SETTINGS -->|"copy.deepcopy(DEFAULT_CONFIG)"| DEFAULTS

    %% CLI → Orchestrator
    CMD_CR -->|"CrossFireOrchestrator(settings)"| ORCH_CR
    CMD_PR -->|"CrossFireOrchestrator(settings)"| ORCH_PR
    CMD_DIFF -->|"CrossFireOrchestrator(settings)"| ORCH_DIFF
    CMD_SCAN -->|"CrossFireOrchestrator(settings)"| ORCH_SCAN
    CMD_DEMO -->|"CrossFireOrchestrator(settings)"| PIPELINE

    %% Code Review pipeline
    ORCH_CR -->|"build_from_repo(repo_dir, max_files)"| CTX
    ORCH_CR -->|"infer(context) → IntentProfile"| INFERRER
    ORCH_CR -->|"_run_skills(context, intent, repo_dir)"| SKILL_RUN
    ORCH_CR -->|"build_code_review_prompt() + CODE_REVIEW_SYSTEM_PROMPT"| REVIEW_PROMPT
    ORCH_CR -->|"asyncio.gather(*agents)"| CLAUDE
    ORCH_CR -->|"asyncio.gather(*agents)"| CODEX
    ORCH_CR -->|"asyncio.gather(*agents)"| GEMINI
    ORCH_CR -->|"synthesize(reviews, intent)"| SYNTH
    ORCH_CR -->|"debate_all(findings, ctx, intent)"| DEBATE_ENG
    ORCH_CR -->|"policy_engine.apply(findings)"| POLICY_ENG

    %% CLI → Baseline system
    CMD_BASELINE -->|"BaselineManager(repo_dir).build()"| BASELINE_MGR
    CMD_SCAN -->|"DiffResolver.<mode>(repo_dir)"| DIFF_RES
    CMD_SCAN -->|"BaselineManager(repo_dir)"| BASELINE_MGR
    CMD_SCAN -->|"FastModel(settings.fast_model)"| FAST_MODEL
    BASELINE_MGR -->|"check_intent_changed(diff, fast_model)"| CTX_PROMPT
    CTX_PROMPT -->|"fast_model.call()"| FAST_MODEL

    %% scan_with_baseline pipeline
    ORCH_SCAN -->|"build_context_system_prompt(baseline, diff)"| CTX_PROMPT
    ORCH_SCAN -->|"build_from_diff(diff_text, repo_dir)"| CTX
    ORCH_SCAN -->|"_run_skills(context, intent, repo_dir)"| SKILL_RUN
    ORCH_SCAN -->|"run_independent_reviews(ctx, intent, skills, system_prompt)"| REVIEW
    ORCH_SCAN -->|"synthesize(reviews, intent)"| SYNTH
    ORCH_SCAN -->|"filter_known(findings, baseline)"| BASELINE_MGR
    ORCH_SCAN -->|"debate_all(new_findings, ctx, intent)"| DEBATE_ENG
    ORCH_SCAN -->|"policy_engine.apply(findings)"| POLICY_ENG
    ORCH_SCAN -->|"update_after_scan(commit, confirmed)"| BASELINE_MGR
    ORCH_SCAN -->|"_build_scan_summary()"| SCAN_SUMMARY

    %% Orchestrator pipeline
    ORCH_PR -->|"build_from_github_pr(repo, pr, token) → PRContext"| CTX
    ORCH_PR -->|"_run_pipeline(context, skip_debate)"| PIPELINE
    ORCH_DIFF -->|"build_from_patch_file/staged/refs → PRContext"| CTX
    ORCH_DIFF -->|"_run_pipeline(context, skip_debate, repo_dir)"| PIPELINE

    %% Context building
    CTX -->|"async"| PR_LOAD
    CTX -->|"parse_diff()"| CTX_DIFF
    CTX -->|"_enrich_file_context()"| CTX_ENRICH
    PR_LOAD -->|"config_files, ci_config_files, directory_structure"| M_CTX

    %% Pipeline steps
    PIPELINE -->|"1. infer(context) → IntentProfile"| INFERRER
    PIPELINE -->|"2. _run_skills(context, intent, repo_dir)"| SKILL_RUN
    SKILL_RUN --> S_DF
    SKILL_RUN --> S_GA
    SKILL_RUN --> S_CA
    SKILL_RUN --> S_DA
    SKILL_RUN --> S_TC
    SKILL_RUN --> S_CN
    PIPELINE -->|"3. run_independent_reviews(ctx, intent, skills) → list[AgentReview]"| REVIEW
    PIPELINE -->|"4. synthesize(reviews, intent) → list[Finding]"| SYNTH
    PIPELINE -->|"5. debate_all(findings, ctx, intent) → list[DebateRecord]"| DEBATE_ENG
    PIPELINE -->|"6. policy_engine.apply(findings)"| POLICY_ENG
    PIPELINE -->|"7. _compute_overall_risk()"| RISK

    %% Review engine
    REVIEW -->|"build_review_prompt(ctx, intent, skills)"| REVIEW_PROMPT
    REVIEW -->|"system_prompt or REVIEW_SYSTEM_PROMPT"| REVIEW_PROMPT
    REVIEW -->|"asyncio.gather(*tasks)"| CLAUDE
    REVIEW -->|"asyncio.gather(*tasks)"| CODEX
    REVIEW -->|"asyncio.gather(*tasks)"| GEMINI
    CLAUDE --> BASE
    CODEX --> BASE
    GEMINI --> BASE

    %% Debate engine (2-round judge-led)
    DEBATE_ENG -->|"Round 1: prosecution"| PROS_PROMPT
    DEBATE_ENG -->|"Round 1: defense"| DEF_PROMPT
    DEBATE_ENG -->|"Round 2: judge clarification + final"| JUDGE_PROMPT
    DEBATE_ENG -->|"compute_consensus(debate, intent)"| CONSENSUS
    DEBATE_ENG -->|"agents execute prompts"| CLAUDE
    DEBATE_ENG -->|"agents execute prompts"| CODEX
    DEBATE_ENG -->|"agents execute prompts"| GEMINI

    %% Output
    CMD_PR -->|"_output_report(report, fmt)"| MD_RPT
    CMD_PR -->|"_output_report(report, fmt)"| JSON_RPT
    CMD_PR -->|"_output_report(report, fmt)"| SARIF_RPT
    CMD_PR -->|"_check_severity_gate()"| SEVERITY
    CMD_PR -->|"post_review_comment()"| COMMENT
    CMD_DIFF -->|"_output_report(report, fmt)"| MD_RPT
    CMD_DIFF -->|"_check_severity_gate()"| SEVERITY
    CMD_REPORT -->|"_output_report(report, fmt)"| MD_RPT

    %% Live UI wiring
    CMD_CR -->|"HackerUI(show_debate=..., debug_mode=...)"| HACKER_UI
    CMD_PR -->|"HackerUI(show_debate=..., debug_mode=...)"| HACKER_UI
    CMD_DIFF -->|"HackerUI(show_debate=..., debug_mode=...)"| HACKER_UI
    CMD_TESTLLM -->|"AgentTestUI()"| HACKER_UI
    HACKER_UI -->|"--debug: DebugCollector in processor chain"| DEBUG_COLL
    HACKER_UI -->|"--debate: live bubble streaming"| DEBATE_VIEW
    DEBUG_COLL -->|"write_debug_markdown(report)"| MD_RPT
    CMD_DEBATES -->|"render_debates(report)"| DEBATE_VIEW

    %% Auth wiring
    CMD_AUTH -->|"AuthStore.load() / save()"| AUTH_STORE

```

### Connection Legend
- **Solid arrows** → real, working connections

---

## 3. Data Model Relationship Diagram

```mermaid
classDiagram
    class PRContext {
        repo_name: str
        pr_number: int | None
        pr_title: str
        pr_description: str
        author: str
        base_branch: str
        head_branch: str
        files: list~FileContext~
        commit_messages: list~str~
        labels: list~str~
        readme_content: str | None
        repo_description: str | None
        ci_config_files: dict~str,str~
        config_files: dict~str,str~
        directory_structure: str
    }

    class FileContext {
        path: str
        language: str | None
        content: str | None
        base_content: str | None
        diff_hunks: list~DiffHunk~
        is_new: bool
        is_deleted: bool
        is_renamed: bool
        old_path: str | None
        related_files: list~RelatedFile~
        git_blame_summary: dict | None
        test_files: list~str~
    }

    class DiffHunk {
        file_path: str
        old_start: int
        old_count: int
        new_start: int
        new_count: int
        content: str
        added_lines: list~str~
        removed_lines: list~str~
    }

    class RelatedFile {
        path: str
        relationship: str
        content: str | None
        relevance: str
    }

    class IntentProfile {
        repo_purpose: str
        intended_capabilities: list~str~
        trust_boundaries: list~TrustBoundary~
        security_controls_detected: list~SecurityControl~
        deployment_context: str | None
        pr_intent: str
        risk_surface_change: str
        sensitive_paths: list~str~
    }

    class TrustBoundary {
        name: str
        description: str
        untrusted_inputs: list~str~
        controls: list~str~
    }

    class SecurityControl {
        control_type: str
        location: str
        description: str
        covers: list~str~
    }

    class Finding {
        id: str [uuid]
        title: str
        category: FindingCategory
        severity: Severity
        confidence: float
        exploitability: Exploitability
        blast_radius: BlastRadius
        status: FindingStatus
        purpose_aware_assessment: PurposeAssessment
        affected_files: list~str~
        line_ranges: list~LineRange~
        diff_hunks: list~str~
        evidence: list~Evidence~
        data_flow_trace: str | None
        reproduction_risk_notes: str
        mitigations: list~str~
        rationale_summary: str
        reviewing_agents: list~str~
        debate_summary: str | None
        consensus_outcome: str | None
        debate_tag: DebateTag
    }

    class Evidence {
        source: str
        evidence_type: str
        description: str
        file_path: str | None
        line_range: LineRange | None
        code_snippet: str | None
        context_snippet: str | None
        confidence: float
    }

    class PurposeAssessment {
        is_intended_capability: bool
        capability_description: str | None
        trust_boundary_violated: bool
        untrusted_input_reaches_sink: bool
        isolation_controls_present: bool
        policy_checks_present: bool
        audit_logging_present: bool
        enabled_by_default: bool
        remotely_triggerable: bool
        assessment: str
    }

    class LineRange {
        file_path: str
        start_line: int
        end_line: int
    }

    class CitedEvidence {
        file_path: str
        line_range: str | None
        code_snippet: str
        explanation: str
    }

    class AgentReview {
        agent_name: str
        findings: list~Finding~
        overall_risk_assessment: str
        review_methodology: str
        files_analyzed: list~str~
        skills_used: list~str~
        review_duration_seconds: float | None
    }

    class AgentArgument {
        agent_name: str
        role: str
        position: str
        argument: str
        cited_evidence: list~CitedEvidence~
        confidence: float
    }

    class DebateRecord {
        finding_id: str
        prosecutor_argument: AgentArgument
        defense_argument: AgentArgument
        judge_ruling: AgentArgument
        judge_questions: str | None
        round_2_prosecution: AgentArgument | None
        round_2_defense: AgentArgument | None
        rounds_used: int
        consensus: ConsensusOutcome
        final_severity: Severity
        final_confidence: float
        evidence_quality: str
    }

    class CrossFireReport {
        repo_name: str
        pr_number: int | None
        pr_title: str
        context: PRContext
        intent: IntentProfile
        agent_reviews: list~AgentReview~
        findings: list~Finding~
        debates: list~DebateRecord~
        overall_risk: str
        summary: str
        agents_used: list~str~
        review_duration_seconds: float | None
    }

    PRContext "1" *-- "*" FileContext : files
    FileContext "1" *-- "*" DiffHunk : diff_hunks
    FileContext "1" *-- "*" RelatedFile : related_files
    IntentProfile "1" *-- "*" TrustBoundary : trust_boundaries
    IntentProfile "1" *-- "*" SecurityControl : security_controls_detected
    Finding "1" *-- "1" PurposeAssessment : purpose_aware_assessment
    Finding "1" *-- "*" Evidence : evidence
    Finding "1" *-- "*" LineRange : line_ranges
    Evidence "1" o-- "0..1" LineRange : line_range
    AgentReview "1" *-- "*" Finding : findings
    AgentArgument "1" *-- "*" CitedEvidence : cited_evidence
    DebateRecord "1" *-- "1" AgentArgument : prosecutor_argument
    DebateRecord "1" *-- "1" AgentArgument : defense_argument
    DebateRecord "1" *-- "1" AgentArgument : judge_ruling
    DebateRecord "1" o-- "0..1" AgentArgument : round_2_prosecution
    DebateRecord "1" o-- "0..1" AgentArgument : round_2_defense
    CrossFireReport "1" *-- "1" PRContext : context
    CrossFireReport "1" *-- "1" IntentProfile : intent
    CrossFireReport "1" *-- "*" AgentReview : agent_reviews
    CrossFireReport "1" *-- "*" Finding : findings
    CrossFireReport "1" *-- "*" DebateRecord : debates
```

### Instantiation Map (where each model is actually created)

| Model | Created In | Notes |
|-------|-----------|-------|
| `PRContext` | `context_builder.py`, `pr_loader.py`, `cli.py` (demo) | ✅ All three paths |
| `FileContext` | `context_builder.parse_diff()` | ✅ |
| `DiffHunk` | `context_builder.parse_diff()` | ✅ |
| `RelatedFile` | `context_builder._find_imports_*()`, `_find_reverse_imports()` | ✅ |
| `IntentProfile` | `intent_inference.IntentInferrer.infer()` | ✅ |
| `TrustBoundary` | `intent_inference._infer_trust_boundaries()` | ✅ |
| `SecurityControl` | `intent_inference._detect_security_controls()` | ✅ |
| `Finding` | `review_engine._parse_finding_from_raw()`, `finding_synthesizer._merge_findings()` | ✅ |
| `Evidence` | `review_engine._parse_finding_from_raw()` | ✅ |
| `PurposeAssessment` | `review_engine._parse_finding_from_raw()` | ✅ |
| `LineRange` | `review_engine._parse_finding_from_raw()` | ✅ |
| `AgentReview` | `review_engine._dispatch_to_agent()` | ✅ |
| `AgentArgument` | `debate_engine._parse_agent_argument()` | ✅ |
| `CitedEvidence` | `debate_engine._parse_agent_argument()` | ✅ |
| `DebateRecord` | `debate_engine._debate_single()` | ✅ |
| `CrossFireReport` | `orchestrator._run_pipeline()` | ✅ |

### Enums (all instantiated)

| Enum | Values | Used By |
|------|--------|---------|
| `FindingCategory` | 50 categories across 7 groups | `Finding.category` |
| `Severity` | Critical, High, Medium, Low | `Finding.severity`, `DebateRecord.final_severity` |
| `Exploitability` | Proven, Likely, Possible, Unlikely | `Finding.exploitability` |
| `BlastRadius` | System, Service, Component, Limited | `Finding.blast_radius` |
| `FindingStatus` | Confirmed, Likely, Unclear, Rejected | `Finding.status` |
| `ConsensusOutcome` | Confirmed, Likely, Unclear, Rejected | `DebateRecord.consensus` |
| `DebateTag` | needs_debate, auto_confirmed, informational | `Finding.debate_tag` |

---

## 4. Call Graph — Entry Point Traces

### COMMAND: `crossfire analyze-pr --repo owner/repo --pr 123 --github-token XXX`

```
cli.py:45 analyze_pr(repo: str, pr: int, github_token: str, agents: str|None,
                      skip_debate: bool, context_depth: str|None, output: str|None,
                      format: str, post_comment: bool, verbose: bool, dry_run: bool)
│
├─ config/settings.py load_settings(cli_overrides={"analysis": {"context_depth": X}})
│  ├─ copy.deepcopy(DEFAULT_CONFIG)
│  ├─ _find_config_file() → Path | None
│  ├─ _load_yaml_config(path) → dict  [if config found]
│  ├─ _deep_merge(defaults, file_config)
│  ├─ _deep_merge(merged, cli_overrides)
│  └─ _parse_agents_config(merged) → (agents, debate, skills)
│     └─ Returns CrossFireSettings
│
├─ core/orchestrator.py:43 CrossFireOrchestrator.__init__(settings: CrossFireSettings)
│  ├─ ContextBuilder(settings.analysis)
│  ├─ IntentInferrer(settings.repo)
│  ├─ ReviewEngine(settings)
│  ├─ FindingSynthesizer()
│  ├─ DebateEngine(settings)
│  └─ PolicyEngine(settings.suppressions)
│
├─ core/orchestrator.py:55 analyze_pr(repo, pr_number, github_token, skip_debate)
│  │
│  ├─ core/context_builder.py:569 build_from_github_pr(repo, pr_number, github_token)
│  │  └─ integrations/github/pr_loader.py:13 load_pr_context(repo, pr_number, token, config)
│  │     ├─ httpx GET /repos/{repo}/pulls/{pr_number} → pr_data
│  │     ├─ _fetch_all_pr_files(client, repo, pr) → files_data (paginated, 100/page)
│  │     ├─ httpx GET /repos/{repo}/pulls/{pr_number} (Accept: diff) → diff_text
│  │     ├─ core/context_builder.py:143 parse_diff(diff_text) → list[FileContext]
│  │     ├─ asyncio.gather(*[_fetch_file_content(fc) for fc in files])  ← parallel
│  │     │  ├─ httpx GET /repos/{repo}/contents/{path}?ref=head → fc.content
│  │     │  └─ httpx GET /repos/{repo}/contents/{old_path|path}?ref=base → fc.base_content
│  │     ├─ asyncio.gather(readme_task, repo_task, commits_task)  ← parallel
│  │     │  ├─ httpx GET /repos/{repo}/readme → readme_content
│  │     │  ├─ httpx GET /repos/{repo} → repo_description
│  │     │  └─ httpx GET /repos/{repo}/pulls/{pr_number}/commits → commit_messages
│  │     ├─ Fetch manifest files for intent inference (requirements.txt, package.json, etc.)
│  │     ├─ _build_directory_structure(file_paths) → directory tree string
│  │     ├─ Identify config_files and ci_config_files from changed files
│  │     └─ Returns PRContext (fully populated including config_files, ci_config_files, directory_structure)
│  │
│  └─ core/orchestrator.py:128 _run_pipeline(context, skip_debate, repo_dir=None)
│     │
│     ├─ [Step 1] core/intent_inference.py infer(context) → IntentProfile
│     │  ├─ _extract_purpose_from_readme(readme)
│     │  ├─ _analyze_package_metadata(config_files)  ✅ populated for GH PRs
│     │  ├─ _analyze_file_structure(directory_structure)  ✅ populated for GH PRs
│     │  ├─ _analyze_dependencies(config_files)  ✅ populated for GH PRs
│     │  ├─ _detect_security_controls(context)
│     │  ├─ _infer_trust_boundaries(capabilities, controls)
│     │  ├─ _classify_pr_intent(title, description)
│     │  ├─ _analyze_risk_surface_change(context)
│     │  └─ _detect_sensitive_paths(context)
│     │
│     ├─ [Step 2] if repo_dir is not None:
│     │  │  await asyncio.to_thread(_run_skills, context, intent, repo_dir)
│     │  │  ├─ DataFlowTracingSkill().execute(repo_dir, changed_files)
│     │  │  ├─ GitArcheologySkill().execute(repo_dir, changed_files)
│     │  │  ├─ ConfigAnalysisSkill().execute(repo_dir, changed_files)
│     │  │  ├─ DependencyAnalysisSkill().execute(repo_dir, changed_files, file_contexts=context.files)
│     │  │  ├─ TestCoverageCheckSkill().execute(repo_dir, changed_files)
│     │  │  └─ CodeNavigationSkill().execute(repo_dir, changed_files)
│     │  └─ else: skills skipped (no local checkout for GH PRs)
│     │
│     ├─ [Step 3] agents/review_engine.py:150 run_independent_reviews(ctx, intent, skill_outputs)
│     │  ├─ build_review_prompt(context, intent, skill_outputs) → prompt string
│     │  ├─ for each enabled agent: _create_agent(name, config) → BaseAgent
│     │  └─ asyncio.gather(*[_dispatch_to_agent(agent, prompt, sys_prompt)])
│     │     └─ per agent:
│     │        ├─ agent.execute(prompt, REVIEW_SYSTEM_PROMPT) → raw JSON string
│     │        ├─ agent.parse_json_response(raw) → dict
│     │        ├─ _parse_finding_from_raw(finding_dict, agent_name) → Finding
│     │        └─ Returns AgentReview
│     │
│     ├─ [Step 4] core/finding_synthesizer.py:154 synthesize(reviews, intent)
│     │  ├─ Collect all findings from all reviews
│     │  ├─ Union-find clustering of similar findings (_is_similar_finding)
│     │  ├─ Merge clusters (_merge_findings)
│     │  ├─ Cross-validation confidence boost (2 agents: ×1.2, 3+: ×1.4)
│     │  ├─ _apply_purpose_aware_adjustments(finding, intent)
│     │  └─ _tag_for_debate(finding) → sets DebateTag
│     │
│     ├─ [Step 5] agents/debate_engine.py:149 debate_all(findings, context, intent)
│     │  └─ for each finding with debate_tag == NEEDS_DEBATE:
│     │     └─ _debate_single(finding, context, intent) → DebateRecord | None
│     │        ├─ _assign_roles() → (prosecutor, defense, judge)
│     │        ├─ Create agent instances from AGENT_CLASSES
│     │        ├─ Round 1: _run_prosecution(agent, ...) → AgentArgument
│     │        │  ├─ build_prosecutor_prompt(finding_summary, evidence, ctx, intent)
│     │        │  └─ agent.execute(prompt, PROSECUTOR_SYSTEM_PROMPT)
│     │        ├─ Round 1: _run_defense(agent, ...) → AgentArgument
│     │        │  ├─ build_defense_prompt(finding_summary, evidence, prosecutor_arg, ctx, intent)
│     │        │  └─ agent.execute(prompt, DEFENSE_SYSTEM_PROMPT)
│     │        ├─ If defense concedes → Judge issues immediate verdict
│     │        ├─ If defense disagrees → Round 2: Judge-led clarification
│     │        │  ├─ _run_judge_clarification(judge, ...) → targeted questions
│     │        │  ├─ asyncio.gather(prosecution response, defense response)
│     │        │  └─ _run_judge_final(judge, ...) → final ruling with all context
│     │        ├─ Parse final_severity from judge raw JSON
│     │        ├─ Build DebateRecord (rounds_used=1 or 2)
│     │        ├─ agents/consensus.py compute_consensus(debate, intent) → ConsensusOutcome
│     │        │  ├─ _evidence_quality_score(prosecutor) → float
│     │        │  ├─ _evidence_quality_score(defense) → float
│     │        │  ├─ Map judge position → initial outcome
│     │        │  ├─ Cross-check with other positions
│     │        │  ├─ Weak evidence downgrades
│     │        │  └─ Purpose-aware override
│     │        └─ _apply_debate_result(finding, debate) → updates finding status/severity
│     │
│     ├─ [Step 6] core/policy_engine.py:21 apply(findings) → list[Finding]
│     │  └─ for each finding: _check_suppressions(finding) → match by category/file/title
│     │
│     ├─ [Step 7] _compute_overall_risk(findings) → "critical"|"high"|"medium"|"low"|"none"
│     │
│     └─ Returns CrossFireReport
│
├─ cli.py:345 _output_report(report, format, output, post_comment, repo, pr_number, github_token)
│  ├─ generate_markdown_report(report) | generate_json_report(report) | generate_sarif_report(report)
│  ├─ Write to file or console
│  └─ if post_comment: post_review_comment(repo, pr_number, token, md_content)
│
└─ cli.py:320 _check_severity_gate(report, settings)
   └─ core/severity.py should_fail_ci(findings, fail_on, min_confidence, require_debate)
      └─ typer.Exit(1) if breached
```

### COMMAND: `crossfire analyze-diff --patch changes.patch --repo-dir /path/to/repo`

```
cli.py:119 analyze_diff(patch: str, repo_dir: str, staged: bool, base: str|None,
                         head: str|None, agents: str|None, skip_debate: bool,
                         context_depth: str|None, output: str|None, format: str,
                         verbose: bool, dry_run: bool)
│
├─ load_settings(repo_dir=repo_dir, cli_overrides=...)
├─ CrossFireOrchestrator(settings)
├─ orchestrator.analyze_diff(repo_dir, patch_path, staged, base_ref, head_ref, skip_debate)
│  │
│  ├─ if patch_path:   context_builder.build_from_patch_file(patch_path, repo_dir) → PRContext
│  ├─ elif staged:     context_builder.build_from_staged(repo_dir) → PRContext
│  ├─ elif base+head:  context_builder.build_from_refs(repo_dir, base, head) → PRContext
│  │  └─ All three call build_from_diff(diff_text, repo_dir, ...)
│  │     ├─ parse_diff(diff_text) → list[FileContext]
│  │     ├─ _enrich_file_context(fc, repo_dir, base_ref) for each file
│  │     │  ├─ _read_file_safe(path) → head content
│  │     │  ├─ _get_file_at_ref(old_path|path, base_ref) → base content
│  │     │  ├─ _find_imports(content, path, lang, repo_dir) → related files
│  │     │  ├─ _find_reverse_imports(path, repo_dir, lang) → imported_by
│  │     │  ├─ _get_git_blame_summary(path, repo_dir) → blame dict
│  │     │  └─ _find_test_files(path, repo_dir) → test files
│  │     ├─ _collect_configs(repo_dir) → config_files ✅ populated for local
│  │     ├─ _collect_ci_configs(configs) → ci_config_files ✅ populated for local
│  │     ├─ _get_directory_structure(repo_dir) ✅ populated for local
│  │     └─ Returns PRContext (fully enriched)
│  │
│  └─ _run_pipeline(context, skip_debate, repo_dir=repo_dir)  ✅ repo_dir passed
│     └─ [same pipeline as analyze-pr, but skills get correct repo_dir]
│
├─ _output_report(report, format, output, False)
└─ _check_severity_gate(report, settings)
```

### COMMAND: `crossfire report --input results.json --format sarif`

```
cli.py:196 report(input: str, format: str, output: str|None)
│
├─ Path(input).read_text() → JSON string
├─ json.loads(text) → dict
├─ CrossFireReport(**data) → report object
└─ _output_report(report, format, output, False)
   └─ generate_sarif_report(report) → SARIF JSON string
```

### COMMAND: `crossfire init`

```
cli.py:222 init()
│
├─ Path.cwd() / ".crossfire" → config_dir
├─ config_dir.mkdir(exist_ok=True)
├─ If .crossfire/config.yaml exists → exit
├─ If .crossfire/config.example.yaml exists at __file__/../../ → shutil.copy
└─ Else → config_file.write_text(_default_config_yaml())
```

### COMMAND: `crossfire config-check --repo-dir /path`

```
cli.py:243 config_check(repo_dir: str)
│
├─ load_settings(repo_dir=repo_dir)
└─ Print agents, context_depth, debate config, severity_gate
```

### COMMAND: `crossfire demo --fixture auth_bypass_regression`

```
cli.py:261 demo(fixture: str, format: str, verbose: bool)
│
├─ fixtures_dir = Path(__file__).parent.parent / "tests/fixtures/prs" / fixture
├─ diff_path = fixtures_dir / "diff.patch"
├─ context_path = fixtures_dir / "context.json"
├─ diff_text = diff_path.read_text()
├─ parse_diff(diff_text) → files
├─ json.loads(context_path.read_text()) → context_meta
├─ PRContext(repo_name=..., pr_title=..., files=files)
├─ load_settings()
├─ CrossFireOrchestrator(settings)
├─ orchestrator._run_pipeline(pr_context, skip_debate=False)
└─ _output_report(report, format, None, False)
```

---

## 5. Dependency Direction Map

### Layered Diagram

```
LAYER 0 — Leaf modules (no internal deps):
  ├─ crossfire/core/models.py
  ├─ crossfire/config/defaults.py
  ├─ crossfire/skills/base.py
  ├─ crossfire/agents/prompts/prosecutor_prompt.py
  ├─ crossfire/agents/prompts/defense_prompt.py
  └─ crossfire/agents/prompts/judge_prompt.py

LAYER 1 — Depends only on Layer 0:
  ├─ crossfire/config/settings.py → config/defaults.py
  ├─ crossfire/core/policy_engine.py → core/models.py
  ├─ crossfire/core/severity.py → core/models.py
  ├─ crossfire/output/json_report.py → core/models.py
  ├─ crossfire/output/markdown_report.py → core/models.py
  ├─ crossfire/output/sarif_report.py → core/models.py
  ├─ crossfire/agents/consensus.py → core/models.py
  └─ crossfire/integrations/github/comment_poster.py → (none internal)

LAYER 2 — Depends on Layer 0-1:
  ├─ crossfire/agents/base.py → config/settings.py (AgentConfig)
  ├─ crossfire/core/intent_inference.py → config/settings.py (RepoConfig), core/models.py
  ├─ crossfire/agents/prompts/review_prompt.py → core/models.py
  ├─ crossfire/skills/data_flow_tracing.py → skills/base.py
  ├─ crossfire/skills/git_archeology.py → skills/base.py
  ├─ crossfire/skills/config_analysis.py → skills/base.py
  ├─ crossfire/skills/dependency_analysis.py → skills/base.py
  ├─ crossfire/skills/test_coverage_check.py → skills/base.py
  └─ crossfire/skills/code_navigation.py → skills/base.py

LAYER 3 — Depends on Layer 0-2:
  ├─ crossfire/agents/claude_adapter.py → agents/base.py
  ├─ crossfire/agents/codex_adapter.py → agents/base.py
  ├─ crossfire/agents/gemini_adapter.py → agents/base.py
  └─ crossfire/core/context_builder.py → config/settings.py (AnalysisConfig), core/models.py
      └─ Lazy import: integrations/github/pr_loader.py [Layer 3b]

LAYER 3b — Depends on Layer 0-3:
  └─ crossfire/integrations/github/pr_loader.py → config/settings.py (AnalysisConfig),
                                                    core/models.py, core/context_builder.parse_diff

LAYER 4 — Depends on Layer 0-3:
  ├─ crossfire/agents/review_engine.py → agents/base.py, agents/claude_adapter.py,
  │                                       agents/codex_adapter.py, agents/gemini_adapter.py,
  │                                       agents/prompts/review_prompt.py, config/settings.py,
  │                                       core/models.py
  ├─ crossfire/agents/debate_engine.py → agents/base.py, agents/claude_adapter.py,
  │                                       agents/codex_adapter.py, agents/gemini_adapter.py,
  │                                       agents/consensus.py, agents/prompts/prosecutor_prompt.py,
  │                                       agents/prompts/defense_prompt.py, agents/prompts/judge_prompt.py,
  │                                       config/settings.py, core/models.py
  └─ crossfire/core/finding_synthesizer.py → core/models.py

LAYER 5 — Orchestration (depends on Layer 0-4):
  └─ crossfire/core/orchestrator.py → agents/debate_engine.py, agents/review_engine.py,
                                       config/settings.py, core/context_builder.py,
                                       core/finding_synthesizer.py, core/intent_inference.py,
                                       core/models.py, core/policy_engine.py,
                                       skills/* (all 6)

LAYER 6 — Entry point (depends on Layer 0-5):
  └─ crossfire/cli.py → config/settings.py, core/orchestrator.py, core/models.py,
                          core/context_builder.py (parse_diff only in demo),
                          core/severity.py, output/* (all 3),
                          integrations/github/comment_poster.py
```

**No layer violations detected.** All imports flow downward. No circular dependencies.

### Mermaid Dependency Graph

```mermaid
flowchart BT
    subgraph L0["Layer 0 — Leaf"]
        MODELS["core/models.py"]
        DEFAULTS["config/defaults.py"]
        SKILL_BASE["skills/base.py"]
        PROS_P["prompts/prosecutor"]
        DEF_P["prompts/defense"]
        JUDGE_P["prompts/judge"]
    end

    subgraph L1["Layer 1"]
        SETTINGS["config/settings.py"]
        POLICY["core/policy_engine.py"]
        SEVERITY["core/severity.py"]
        JSON_R["output/json_report.py"]
        MD_R["output/markdown_report.py"]
        SARIF_R["output/sarif_report.py"]
        CONSENSUS["agents/consensus.py"]
        POSTER["github/comment_poster.py"]
    end

    subgraph L2["Layer 2"]
        BASE_AGENT["agents/base.py"]
        INTENT["core/intent_inference.py"]
        REV_PROMPT["prompts/review_prompt.py"]
        S_DF["skills/data_flow"]
        S_GA["skills/git_archeology"]
        S_CA["skills/config_analysis"]
        S_DA["skills/dependency"]
        S_TC["skills/test_coverage"]
        S_CN["skills/code_nav"]
    end

    subgraph L3["Layer 3"]
        CLAUDE["agents/claude"]
        CODEX["agents/codex"]
        GEMINI["agents/gemini"]
        CTX["core/context_builder"]
        PR_LOAD["github/pr_loader"]
    end

    subgraph L4["Layer 4"]
        REVIEW["agents/review_engine"]
        DEBATE["agents/debate_engine"]
        SYNTH["core/finding_synth"]
    end

    subgraph L5["Layer 5"]
        ORCH["core/orchestrator"]
    end

    subgraph L6["Layer 6"]
        CLI["cli.py"]
    end

    %% L1 deps
    SETTINGS --> DEFAULTS
    POLICY --> MODELS
    SEVERITY --> MODELS
    JSON_R --> MODELS
    MD_R --> MODELS
    SARIF_R --> MODELS
    CONSENSUS --> MODELS

    %% L2 deps
    BASE_AGENT --> SETTINGS
    INTENT --> SETTINGS
    INTENT --> MODELS
    REV_PROMPT --> MODELS
    S_DF --> SKILL_BASE
    S_GA --> SKILL_BASE
    S_CA --> SKILL_BASE
    S_DA --> SKILL_BASE
    S_TC --> SKILL_BASE
    S_CN --> SKILL_BASE

    %% L3 deps
    CLAUDE --> BASE_AGENT
    CODEX --> BASE_AGENT
    GEMINI --> BASE_AGENT
    CTX --> SETTINGS
    CTX --> MODELS
    PR_LOAD --> SETTINGS
    PR_LOAD --> MODELS
    PR_LOAD -.-> CTX

    %% L4 deps
    REVIEW --> BASE_AGENT
    REVIEW --> CLAUDE
    REVIEW --> CODEX
    REVIEW --> GEMINI
    REVIEW --> REV_PROMPT
    REVIEW --> SETTINGS
    REVIEW --> MODELS
    DEBATE --> BASE_AGENT
    DEBATE --> CLAUDE
    DEBATE --> CODEX
    DEBATE --> GEMINI
    DEBATE --> CONSENSUS
    DEBATE --> PROS_P
    DEBATE --> DEF_P
    DEBATE --> JUDGE_P
    DEBATE --> SETTINGS
    DEBATE --> MODELS
    SYNTH --> MODELS

    %% L5 deps
    ORCH --> REVIEW
    ORCH --> DEBATE
    ORCH --> SETTINGS
    ORCH --> CTX
    ORCH --> SYNTH
    ORCH --> INTENT
    ORCH --> MODELS
    ORCH --> POLICY
    ORCH --> S_DF
    ORCH --> S_GA
    ORCH --> S_CA
    ORCH --> S_DA
    ORCH --> S_TC
    ORCH --> S_CN

    %% Baseline system deps
    ORCH --> BASELINE
    BASELINE --> INTENT
    BASELINE --> MODELS
    CTX_PROMPT_DEP["agents/prompts/context_prompt.py"] --> FAST_MODEL_DEP["agents/fast_model.py"]
    CTX_PROMPT_DEP --> REV_PROMPT
    BASELINE --> CTX_PROMPT_DEP
    DIFF_RES_DEP["core/diff_resolver.py"] -.-> MODELS

    %% L6 deps
    CLI --> ORCH
    CLI --> SETTINGS
    CLI --> MODELS
    CLI --> BASELINE
    CLI --> DIFF_RES_DEP
    CLI --> FAST_MODEL_DEP
    CLI --> SEVERITY
    CLI --> MD_R
    CLI --> JSON_R
    CLI --> SARIF_R
    CLI --> POSTER
```

---

## 6. Async Boundary Map

```
SYNC WORLD:
  ├─ cli.py (entry point — all commands are sync functions)
  ├─ config/defaults.py, config/settings.py — all sync
  ├─ core/context_builder.py — most methods are sync
  │   ├─ parse_diff() — sync
  │   ├─ build_from_diff() — sync
  │   ├─ build_from_staged() — sync
  │   ├─ build_from_refs() — sync
  │   ├─ build_from_patch_file() — sync
  │   ├─ _enrich_file_context() — sync (subprocess.run for git, os.walk for FS)
  │   └─ build_from_github_pr() — ❗ ASYNC (the only async method)
  ├─ core/intent_inference.py — all sync (regex, string ops)
  ├─ core/finding_synthesizer.py — all sync
  ├─ core/policy_engine.py — all sync
  ├─ core/severity.py — all sync
  ├─ skills/* — ALL SYNC (subprocess.run, open(), os.walk)
  ├─ output/* — all sync (string building)
  └─ agents/prompts/* — all sync (string building)

ASYNC BOUNDARY (asyncio.run() calls):
  ├─ cli.py     asyncio.run(orchestrator.code_review(...))          [code-review command]
  ├─ cli.py     asyncio.run(orchestrator.analyze_pr(...))           [analyze-pr command]
  ├─ cli.py     asyncio.run(orchestrator.analyze_diff(...))         [analyze-diff command]
  ├─ cli.py     asyncio.run(orchestrator.scan_with_baseline(...))   [scan command]
  ├─ cli.py     asyncio.run(mgr.check_intent_changed(...))          [scan command — intent check]
  ├─ cli.py     asyncio.run(orchestrator._run_pipeline(...))        [demo command]
  └─ cli.py     asyncio.run(post_review_comment(...))               [inside _output_report]

ASYNC WORLD:
  ├─ core/orchestrator.py
  │   ├─ code_review() — async
  │   │   └─ _run_skills() offloaded via asyncio.to_thread()
  │   │      build_from_repo() is sync (os.walk, file reads)
  │   ├─ analyze_pr() — async
  │   ├─ analyze_diff() — async
  │   ├─ scan_with_baseline() — async
  │   └─ _run_pipeline() — async
  │       └─ _run_skills() offloaded via asyncio.to_thread() — does not block event loop
  │          Skills skipped entirely when repo_dir is None (GitHub PR mode)

  ├─ core/baseline.py
  │   └─ check_intent_changed() — async (delegates to context_prompt)
  │
  ├─ agents/fast_model.py
  │   ├─ call() — async
  │   ├─ _call_api() — async (anthropic.AsyncAnthropic with asyncio.wait_for timeout)
  │   └─ _call_cli() — async (asyncio.create_subprocess_exec)
  │
  ├─ core/context_builder.py
  │   └─ build_from_github_pr() — async
  │
  ├─ integrations/github/pr_loader.py
  │   └─ load_pr_context() — async (httpx.AsyncClient)
  │
  ├─ agents/review_engine.py
  │   ├─ run_independent_reviews() — async
  │   └─ _dispatch_to_agent() — async
  │
  ├─ agents/base.py
  │   ├─ execute() — async
  │   ├─ _run_cli() — async (abstract)
  │   ├─ _run_api() — async (abstract)
  │   └─ _run_subprocess() — async (asyncio.create_subprocess_exec)
  │
  ├─ agents/*_adapter.py
  │   ├─ _run_cli() — async (delegates to _run_subprocess)
  │   └─ _run_api() — async (all use native async SDK clients)
  │       ├─ claude: anthropic.AsyncAnthropic.messages.create() — fully async with timeout
  │       ├─ codex: openai.AsyncOpenAI.chat.completions.create() — fully async with timeout
  │       └─ gemini: genai.GenerativeModel.generate_content_async() — async with asyncio.wait_for timeout
  │
  ├─ agents/debate_engine.py
  │   ├─ debate_all() — async
  │   ├─ _debate_single() — async
  │   ├─ _run_prosecution() — async
  │   ├─ _run_defense() — async
  │   ├─ _run_judge() — async (immediate verdict when defense concedes)
  │   ├─ _run_judge_clarification() — async (Round 2: targeted questions)
  │   ├─ _run_round2_response() — async (both sides respond in parallel)
  │   └─ _run_judge_final() — async (Round 2: final ruling)
  │
  └─ integrations/github/comment_poster.py
      └─ post_review_comment() — async (httpx.AsyncClient)
```

### Sync-in-Async Issues

All previously identified sync-in-async issues have been resolved:

| Location | Resolution |
|----------|-----------|
| `orchestrator._run_skills()` | Now offloaded via `asyncio.to_thread()` — skills run in a thread pool, no longer blocking the event loop. Skipped entirely for GitHub PR mode (no local checkout) |
| `claude_adapter._run_api()` | Now uses `anthropic.AsyncAnthropic` with configurable timeout |
| `codex_adapter._run_api()` | Now uses `openai.AsyncOpenAI` with configurable timeout |
| `gemini_adapter._run_api()` | Now uses `generate_content_async()` with `asyncio.wait_for` timeout |

> All three API adapters use native async SDK clients. Reviews dispatched via `asyncio.gather()` now execute truly concurrently in both CLI and API modes.

---

## 7. Config Flow Map

```
Source Priority (highest wins):
  1. CLI flags (--context-depth, --agents, --skip-debate, etc.)
  2. Environment: CROSSFIRE_CONFIG_PATH, GITHUB_TOKEN, API key env vars
  3. .crossfire/config.yaml (in repo root or CROSSFIRE_CONFIG_PATH)
  4. crossfire/config/defaults.py DEFAULT_CONFIG

Loading:
  load_settings(repo_dir, cli_overrides)
       │
       ├─ copy.deepcopy(DEFAULT_CONFIG)           ← Layer 4: defaults
       ├─ _find_config_file(repo_dir)             ← Layer 3: YAML file
       │   └─ CROSSFIRE_CONFIG_PATH env           ← Layer 2: env override
       ├─ _deep_merge(defaults, yaml)
       ├─ _deep_merge(merged, cli_overrides)      ← Layer 1: CLI flags
       └─ _parse_agents_config(merged)
           └─ Returns CrossFireSettings
```

### Config Consumption Map

| Component | Receives Config Via | Fields Actually Read |
|-----------|-------------------|---------------------|
| `CrossFireOrchestrator` | Constructor: `settings: CrossFireSettings` | `settings.analysis`, `settings.repo`, `settings.agents`, `settings.debate`, `settings.skills`, `settings.suppressions` |
| `FastModel` | Constructor: `config: FastModelConfig` | `provider`, `model`, `api_key_env`, `cli_command`, `cli_args`, `timeout` |
| `BaselineManager` | Constructor: `repo_dir: str` | _(reads/writes `.crossfire/baseline/` directly)_ |
| `ContextBuilder` | Constructor: `analysis_config: AnalysisConfig` | `context_depth`, `max_related_files`, `include_test_files` |
| `IntentInferrer` | Constructor: `repo_config: RepoConfig` | `purpose`, `intended_capabilities`, `sensitive_paths` |
| `ReviewEngine` | Constructor: `settings: CrossFireSettings` | `settings.agents` (which are enabled, their AgentConfig) |
| `DebateEngine` | Constructor: `settings: CrossFireSettings` | `settings.debate` (role_assignment, fixed_roles, enable_rebuttal, min_agents_for_debate), `settings.agents` |
| `PolicyEngine` | Constructor: `suppressions: list[dict]` | Full suppression rules |
| `BaseAgent` (all 3) | Constructor: `config: AgentConfig` | `mode`, `cli_command`, `cli_args`, `model`, `api_key_env`, `timeout` |
| `_check_severity_gate()` | Direct access: `settings.severity_gate` | `fail_on`, `min_confidence`, `require_debate` |
| `_run_skills()` | Direct access: `settings.skills` | `data_flow_tracing`, `git_archeology`, `config_analysis`, `dependency_analysis`, `test_coverage_check`, `code_navigation` (all booleans) |

### Config Fields Defined But Never Enforced

| Field | Defined In | Issue |
|-------|-----------|-------|
| `require_evidence_citations` | `DebateConfig` | 💀 Never validated — no code checks that arguments contain citations |
| `verbose` | CLI flags in 3 commands | 💀 Accepted but never wired to logging |

> `include_test_files` (previously listed here) is now wired — `ContextBuilder` checks this config before calling `_find_test_files()`.

---

## 8. External Interface Map

### GitHub REST API (httpx async client)

| Endpoint | Method | Used In | Purpose |
|----------|--------|---------|---------|
| `/repos/{repo}/pulls/{pr}` | GET | `pr_loader.py` | PR metadata (title, body, author, base/head refs, labels) |
| `/repos/{repo}/pulls/{pr}/files?per_page=100&page=N` | GET | `pr_loader.py` | PR file list (paginated, used for directory structure, config/CI file identification) |
| `/repos/{repo}/pulls/{pr}` | GET (Accept: diff) | `pr_loader.py` | Raw unified diff |
| `/repos/{repo}/contents/{path}?ref={sha}` | GET (Accept: raw) | `pr_loader.py` | File content at head/base ref (fetched in parallel via asyncio.gather) |
| `/repos/{repo}/readme` | GET (Accept: raw) | `pr_loader.py` | README content |
| `/repos/{repo}` | GET | `pr_loader.py` | Repo description |
| `/repos/{repo}/pulls/{pr}/commits` | GET | `pr_loader.py` | Commit messages |
| `/repos/{repo}/contents/{manifest}?ref={head}` | GET (Accept: raw) | `pr_loader.py` | Manifest files for intent inference (requirements.txt, package.json, etc.) |
| `/repos/{repo}/issues/{pr}/comments?per_page=100&page=N` | GET | `comment_poster.py` | Check for existing CrossFire comment (paginated) |
| `/repos/{repo}/issues/comments/{id}` | PATCH | `comment_poster.py` | Update existing comment |
| `/repos/{repo}/issues/{pr}/comments` | POST | `comment_poster.py` | Create new comment |

Auth: `Bearer {GITHUB_TOKEN}` header on all requests.

### Claude Code CLI

```
Command: claude -p "{prompt}" --output-format json --system-prompt "{system_prompt}" {extra_cli_args}
Fallback: anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, timeout=config.timeout).messages.create(
            model="{config.model}", system="{system_prompt}",
            messages=[{"role": "user", "content": "{prompt}"}], max_tokens=8192)
```

### Codex CLI

```
Command: codex -q "{system_prompt}\n\n{prompt}" {extra_cli_args}
Fallback: openai.AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=config.timeout).chat.completions.create(
            model="{config.model}",
            messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}])
```

### Gemini CLI

```
Command: gemini "{system_prompt}\n\n{prompt}" {extra_cli_args}
Fallback: await asyncio.wait_for(
            google.generativeai.GenerativeModel("{config.model}",
                system_instruction="{system_prompt}").generate_content_async("{prompt}"),
            timeout=config.timeout)
```

### Local Git (subprocess.run — sync)

| Command | Used In | Purpose |
|---------|---------|---------|
| `git diff --cached` | `context_builder.build_from_staged()` | Staged changes |
| `git diff` | `context_builder.build_from_staged()` | Unstaged changes (fallback) |
| `git diff {base}...{head}` | `context_builder.build_from_refs()` | Commit range diff |
| `git show {ref}:{path}` | `context_builder._get_file_at_ref()` | File at specific ref |
| `git blame --porcelain {path}` | `context_builder._get_git_blame_summary()`, `git_archeology.get_blame()` | Blame info |
| `git log --format=%s {base}..HEAD` | `context_builder._get_commit_messages()` | Commit messages |
| `git remote get-url origin` | `context_builder._detect_repo_name()` | Repo name detection |
| `git log --max-count=N --format=%H\|%an\|%ai\|%s --follow -- {path}` | `git_archeology.get_file_history()` | File history |
| `git log --since=N days ago --grep=... -i` | `git_archeology.get_recent_security_commits()` | Security commits |
| `git blame -L {start},{end} --porcelain {path}` | `git_archeology.get_code_age()` | Code age |
| `git grep -l {module} -- *.py` | `context_builder._find_reverse_imports()` | Reverse imports |
| `git grep -n {symbol} -- *.py *.js *.ts` | `code_navigation.find_callers_of_file()` | Caller sites |
| `git grep -nE {pattern}` | `code_navigation.find_definitions()` | 💀 Never called from pipeline |

### Local Filesystem

| Operation | Used In | Purpose |
|-----------|---------|---------|
| Read files (`open()`, `Path.read_text()`) | `context_builder`, all 6 skills, `config/settings` | Source code, configs |
| `os.walk()` | `context_builder._find_test_files()`, `config_analysis`, `test_coverage_check`, `code_navigation` | File discovery |
| `os.listdir()` | `context_builder._get_directory_structure()`, `config_analysis` | Directory listing |
| Write files (`Path.write_text()`) | `cli.py` (`init` command, report output) | Config init, report output |

### External Dependencies Note

> Previously unused dependencies `gitpython`, `pygithub`, and `pydantic-settings` have been removed from `pyproject.toml`.
> `respx` (dev dependency) is actively used in `test_comment_poster.py` and `test_pr_loader.py` for mocking httpx.

---

## Dead Code Index

| Item | File:Line | Type |
|------|-----------|------|
| `SEVERITY_ORDER` | `consensus.py:17` | 💀 Defined but never used in that file (duplicated in `finding_synthesizer.py:29` and `severity.py:7` where it IS used) |
| `AGENT_CLASSES` | `debate_engine.py:42`, `review_engine.py:51` | Duplicated constant (both copies are used, could be consolidated) |
| `analyze_permissions()` | `config_analysis.py:179` | 💀 Dead method (never called from `execute()`) |
| `detect_lockfile_inconsistency()` | `dependency_analysis.py:197` | 💀 Dead method (never called) |
| `find_definitions()` | `code_navigation.py:137` | 💀 Dead method (never called from `execute()`) |
| `context_files` param | `base.py:41`, all 3 adapters | 💀 Accepted but never read |

> Resolved dead code (removed in previous fixes):
> - `ROLE_CYCLE` in `debate_engine.py` — removed (replaced by evidence-driven role assignment)
> - `files_data` in `pr_loader.py` — now used for directory structure, config/CI file identification
> - `emoji` / `SEVERITY_EMOJI` / `STATUS_EMOJI` in `markdown_report.py` — removed
> - `_severity_max()` in `finding_synthesizer.py` — removed

---

## 5. Recent Additions (Feb 2026)

### Live Terminal UI (`crossfire/cli_ui.py`)

All pipeline commands now show a live phase-by-phase status display using `rich.live.Live`.

**`HackerUI`** — wraps the entire pipeline:
- Phase list with animated braille spinner (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) at 10fps for the active phase, `✓`/`✗` for completed
- Per-agent rows use a rotating-circle spinner (`◐◓◑◒`) at 6fps — visually distinct from the phase spinner
- `show_debate=True` enables live debate streaming: intercepts `debate.argument`, `debate.judge_questions`, and `debate.verdict` structlog events and renders them above the Live area via `console.print()` (Rich handles thread safety):
  - `_print_debate_argument()` — speech bubble with coloured severity badge (`[HIGH]`/`[CRITICAL]`) on the finding header rule
  - `_print_judge_questions()` — dim indented blockquote `Panel` (not a speech bubble, visually distinct)
  - `_print_debate_verdict()` — consensus outcome `Panel` coloured by verdict
- `debate.start` event now includes `severity` field so the header badge is available before the first argument arrives
- `debug_mode=True` appends a live-log section showing last 8 structlog events from a ring buffer
- `processor()` method slots into structlog processor chain: buffers events (debug) and raises `structlog.DropEvent()` to suppress stdout during pipeline

**`AgentTestUI`** — `transient=True` Live display for `crossfire test-llm`:
- Per-agent `set_testing(name)` / `set_done(name, ok, msg)` called from async test coroutines
- Disappears after completion; results rendered as a hacker-styled cyan table

**Module-level functions**:
- `render_banner()` — ASCII CrossFire logo + tagline
- `render_stats(repo, mode, agents, ...)` — metadata header (repo, mode, enabled agents, context depth, debate flag)

**structlog configuration pattern**:
```python
processors = []
if debug:
    processors.append(collector.processor)   # DebugCollector — buffers to memory
processors.append(ui.processor)              # HackerUI — captures + drops event
structlog.configure(processors=processors, ...)
```

---

### Debate Chat Renderer (`crossfire/output/debate_view.py`)

`render_debates(report, console)` renders all `DebateRecord` objects as a terminal chat.

Layout:
- Round headers via `rich.rule.Rule`
- Prosecution speech: left-aligned `Panel` with red border
- Defense speech: `Padding((0,0,0,6))` left-indent + cyan `Panel`
- Judge speech: bright-white `Panel` with ⚖ icon, full width
- Consensus box: `Panel` colored by outcome — confirmed=red, rejected=green, modified/inconclusive=yellow

Used by:
- `crossfire debates --input results.json` — replays a saved JSON result as a full chat transcript
- `HackerUI` internally (when `--debate` is passed to pipeline commands) imports `_bubble()`, `_CONSENSUS_CONFIG`, `_SEVERITY_STYLE`, and `_RESPONSE_INDENT` for live streaming above the Live area

---

### Debug Log Writer (`crossfire/output/debug_log.py`)

`DebugCollector` is a thread-safe structlog processor that buffers all pipeline events in memory.

`write_debug_markdown(report, collector, command_info)` writes `crossfire-debug-YYYYMMDD-HHMMSS.md` to the current working directory, containing:
1. Pipeline events table (time, level, event, extras)
2. Intent profile (purpose, capabilities, trust boundaries, security controls, sensitive paths)
3. Context summary (files changed, directory structure excerpt, README excerpt)
4. Agent reviews (methodology, files analyzed, reasoning traces in `<details>`, raw findings)
5. Debate transcripts (all rounds, judge ruling, consensus)
6. Full final report (generated by `markdown_report.generate_markdown_report()`)

---

### Heuristic-First Intent Inference (`crossfire/core/intent_inference.py`)

Intent inference now always runs the heuristic engine first, then optionally enriches with an LLM.

**Flow**:
```
IntentInferrer.infer(context)           → heuristic IntentProfile (always runs)
        ↓
_format_heuristic_for_prompt(profile)   → structured text for LLM context
        ↓
LLM call (reframe: "enrich this heuristic threat model")
        ↓
parse LLM response → llm IntentProfile
        ↓
_merge_profiles(heuristic, llm)         → merged IntentProfile
        ↓
on LLM failure: return heuristic (zero wasted work)
```

**`_merge_profiles` rules**:
- Scalars (`repo_purpose`, `deployment_context`, `pr_intent`, `risk_surface_change`): LLM overrides if non-empty, else heuristic preserved
- Lists (`intended_capabilities`, `sensitive_paths`): union with deduplication
- `trust_boundaries`: merge by name — LLM overrides same-name boundaries, heuristic-only kept
- `security_controls_detected`: merge by `(control_type, location)` key — overlaps get LLM description + union of covers

**Call sites**:
- `orchestrator.py` `_run_pipeline()` and `code_review()`: pass `self.intent_inferrer`
- `baseline.py` `_do_build()`: passes `inferrer`

---

### Auth Store (`crossfire/auth/store.py`)

`AuthStore` is a Pydantic model persisted at `.crossfire/auth.json`. Stores OAuth tokens and CLI credentials per provider (claude, codex, gemini).

CLI commands:
- `crossfire auth login --provider <name> [--token <val>]`
- `crossfire auth status`

---

### New CLI Commands & Flags

| Addition | Description |
|----------|-------------|
| `crossfire test-llm` | Tests connectivity for every configured agent; shows mode (cli/api), model, latency, pass/fail |
| `crossfire auth login` | OAuth login or token store for a provider |
| `crossfire auth status` | Shows auth state for all providers |
| `crossfire debates --input` | Re-renders debate chat from a saved JSON result file |
| `--debug` (pipeline cmds) | Shows live log display + writes `crossfire-debug-*.md` to CWD |
| `--silent` (pipeline cmds) | Suppresses all output (structlog → `/dev/null`) |
| `--debate` (pipeline cmds) | Streams live debate chat as each agent responds — shows severity badge, judge questions, and consensus verdict in real time |
