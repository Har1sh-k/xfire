"""Core data models for CrossFire.

All Pydantic v2 BaseModel types used throughout the pipeline.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FindingCategory(str, Enum):
    """Full taxonomy of finding categories."""

    # Injection
    COMMAND_INJECTION = "COMMAND_INJECTION"
    SQL_INJECTION = "SQL_INJECTION"
    TEMPLATE_INJECTION = "TEMPLATE_INJECTION"
    CODE_INJECTION = "CODE_INJECTION"
    NOSQL_INJECTION = "NOSQL_INJECTION"
    LDAP_INJECTION = "LDAP_INJECTION"
    XPATH_INJECTION = "XPATH_INJECTION"

    # Data Security
    UNSAFE_DESERIALIZATION = "UNSAFE_DESERIALIZATION"
    SECRET_EXPOSURE = "SECRET_EXPOSURE"
    TOKEN_LEAKAGE = "TOKEN_LEAKAGE"
    SENSITIVE_DATA_LOGGING = "SENSITIVE_DATA_LOGGING"
    PRIVACY_LEAK = "PRIVACY_LEAK"
    CRYPTO_MISUSE = "CRYPTO_MISUSE"
    KEY_HANDLING = "KEY_HANDLING"

    # Auth & Access
    AUTH_BYPASS = "AUTH_BYPASS"
    AUTHZ_REGRESSION = "AUTHZ_REGRESSION"
    PRIVILEGE_ESCALATION = "PRIVILEGE_ESCALATION"
    TRUST_BOUNDARY_VIOLATION = "TRUST_BOUNDARY_VIOLATION"
    MISSING_AUTH_CHECK = "MISSING_AUTH_CHECK"

    # Network & Web
    SSRF = "SSRF"
    OPEN_REDIRECT = "OPEN_REDIRECT"
    WEBHOOK_TRUST = "WEBHOOK_TRUST"
    PERMISSIVE_CORS = "PERMISSIVE_CORS"
    INSECURE_DEFAULT = "INSECURE_DEFAULT"
    DEBUG_ENABLED = "DEBUG_ENABLED"

    # Filesystem & Execution
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    FILE_PERMISSION = "FILE_PERMISSION"
    SANDBOX_ESCAPE = "SANDBOX_ESCAPE"
    ARBITRARY_CODE_EXEC = "ARBITRARY_CODE_EXEC"

    # Supply Chain
    DEPENDENCY_RISK = "DEPENDENCY_RISK"
    SUPPLY_CHAIN = "SUPPLY_CHAIN"

    # Infrastructure
    CI_WORKFLOW_RISK = "CI_WORKFLOW_RISK"
    INFRA_MISCONFIG = "INFRA_MISCONFIG"
    CONTAINER_ESCAPE = "CONTAINER_ESCAPE"

    # Dangerous Bugs (non-security but catastrophic)
    RACE_CONDITION = "RACE_CONDITION"
    DATA_CORRUPTION = "DATA_CORRUPTION"
    DESTRUCTIVE_OP_NO_SAFEGUARD = "DESTRUCTIVE_OP_NO_SAFEGUARD"
    MISSING_VALIDATION = "MISSING_VALIDATION"
    NULL_HANDLING_CRASH = "NULL_HANDLING_CRASH"
    RETRY_STORM = "RETRY_STORM"
    INFINITE_LOOP = "INFINITE_LOOP"
    RESOURCE_EXHAUSTION = "RESOURCE_EXHAUSTION"
    BROKEN_ROLLBACK = "BROKEN_ROLLBACK"
    MIGRATION_HAZARD = "MIGRATION_HAZARD"
    MISSING_RATE_LIMIT = "MISSING_RATE_LIMIT"
    MISSING_INPUT_VALIDATION = "MISSING_INPUT_VALIDATION"
    MISSING_SIGNATURE_VERIFICATION = "MISSING_SIGNATURE_VERIFICATION"
    ERROR_SWALLOWING = "ERROR_SWALLOWING"
    PARTIAL_STATE_UPDATE = "PARTIAL_STATE_UPDATE"
    CONNECTION_LEAK = "CONNECTION_LEAK"


class Severity(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class Exploitability(str, Enum):
    PROVEN = "Proven"
    LIKELY = "Likely"
    POSSIBLE = "Possible"
    UNLIKELY = "Unlikely"


class BlastRadius(str, Enum):
    SYSTEM = "System"
    SERVICE = "Service"
    COMPONENT = "Component"
    LIMITED = "Limited"


class FindingStatus(str, Enum):
    CONFIRMED = "Confirmed"
    LIKELY = "Likely"
    UNCLEAR = "Unclear"
    REJECTED = "Rejected"


class ConsensusOutcome(str, Enum):
    CONFIRMED = "Confirmed"
    LIKELY = "Likely"
    UNCLEAR = "Unclear"
    REJECTED = "Rejected"


class DebateTag(str, Enum):
    """Tags applied to findings after synthesis to determine debate routing."""

    NEEDS_DEBATE = "needs_debate"
    AUTO_CONFIRMED = "auto_confirmed"
    INFORMATIONAL = "informational"


# ---------------------------------------------------------------------------
# Supporting Models
# ---------------------------------------------------------------------------


class LineRange(BaseModel):
    """A range of lines in a file."""

    file_path: str
    start_line: int
    end_line: int


class RelatedFile(BaseModel):
    """A file related to a changed file, with relationship type."""

    path: str
    relationship: str  # "imports", "imported_by", "calls", "called_by", "tests", "config_for"
    content: str | None = None
    relevance: str  # why this file matters for security review


class DiffHunk(BaseModel):
    """A single hunk from a unified diff."""

    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    content: str  # raw hunk text
    added_lines: list[str] = Field(default_factory=list)
    removed_lines: list[str] = Field(default_factory=list)


class FileContext(BaseModel):
    """Full context for a file involved in the PR."""

    path: str
    language: str | None = None
    content: str | None = None  # full file content (head version)
    base_content: str | None = None  # full file content (base version)
    diff_hunks: list[DiffHunk] = Field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False
    is_renamed: bool = False
    old_path: str | None = None
    related_files: list[RelatedFile] = Field(default_factory=list)
    git_blame_summary: dict[str, Any] | None = None
    test_files: list[str] = Field(default_factory=list)


class SecurityControl(BaseModel):
    """A security control detected in the codebase."""

    control_type: str  # "auth", "sandbox", "rate_limit", "input_validation", etc.
    location: str
    description: str
    covers: list[str] = Field(default_factory=list)


class TrustBoundary(BaseModel):
    """A trust boundary in the system."""

    name: str
    description: str
    untrusted_inputs: list[str] = Field(default_factory=list)
    controls: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# PR / Intent Context
# ---------------------------------------------------------------------------


class PRContext(BaseModel):
    """Complete PR context for analysis."""

    repo_name: str
    pr_number: int | None = None
    pr_title: str
    pr_description: str = ""
    author: str = ""
    base_branch: str = "main"
    head_branch: str = ""
    files: list[FileContext] = Field(default_factory=list)
    commit_messages: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    readme_content: str | None = None
    repo_description: str | None = None
    ci_config_files: dict[str, str] = Field(default_factory=dict)
    config_files: dict[str, str] = Field(default_factory=dict)
    directory_structure: str = ""


class IntentProfile(BaseModel):
    """Inferred purpose and trust model of the repo/PR."""

    repo_purpose: str = ""
    intended_capabilities: list[str] = Field(default_factory=list)
    trust_boundaries: list[TrustBoundary] = Field(default_factory=list)
    security_controls_detected: list[SecurityControl] = Field(default_factory=list)
    deployment_context: str | None = None
    pr_intent: str = ""
    risk_surface_change: str = ""
    sensitive_paths: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """Evidence supporting a finding."""

    source: str  # agent name that found this
    evidence_type: str  # "code_reading", "data_flow_trace", "diff_regression", etc.
    description: str
    file_path: str | None = None
    line_range: LineRange | None = None
    code_snippet: str | None = None
    context_snippet: str | None = None
    confidence: float = 0.5


class PurposeAssessment(BaseModel):
    """Purpose-aware evaluation of whether a finding is a real issue."""

    is_intended_capability: bool = False
    capability_description: str | None = None
    trust_boundary_violated: bool = False
    untrusted_input_reaches_sink: bool = False
    isolation_controls_present: bool = False
    policy_checks_present: bool = False
    audit_logging_present: bool = False
    enabled_by_default: bool = True
    remotely_triggerable: bool = False
    assessment: str = ""


class CitedEvidence(BaseModel):
    """A specific piece of evidence cited in a debate argument."""

    file_path: str
    line_range: str | None = None
    code_snippet: str = ""
    explanation: str = ""


class Finding(BaseModel):
    """A security or dangerous-bug finding."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    category: FindingCategory
    severity: Severity
    confidence: float = 0.5  # 0.0-1.0
    exploitability: Exploitability = Exploitability.POSSIBLE
    blast_radius: BlastRadius = BlastRadius.COMPONENT
    status: FindingStatus = FindingStatus.UNCLEAR
    purpose_aware_assessment: PurposeAssessment = Field(default_factory=PurposeAssessment)
    affected_files: list[str] = Field(default_factory=list)
    line_ranges: list[LineRange] = Field(default_factory=list)
    diff_hunks: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data_flow_trace: str | None = None
    reproduction_risk_notes: str = ""
    mitigations: list[str] = Field(default_factory=list)
    rationale_summary: str = ""
    reviewing_agents: list[str] = Field(default_factory=list)
    debate_summary: str | None = None
    consensus_outcome: str | None = None
    debate_tag: DebateTag = DebateTag.NEEDS_DEBATE


# ---------------------------------------------------------------------------
# Agent Reviews
# ---------------------------------------------------------------------------


class AgentReview(BaseModel):
    """Complete independent review from one agent."""

    agent_name: str
    findings: list[Finding] = Field(default_factory=list)
    overall_risk_assessment: str = ""
    review_methodology: str = ""
    files_analyzed: list[str] = Field(default_factory=list)
    skills_used: list[str] = Field(default_factory=list)
    review_duration_seconds: float | None = None


# ---------------------------------------------------------------------------
# Debate
# ---------------------------------------------------------------------------


class AgentArgument(BaseModel):
    """A single agent's argument in the debate."""

    agent_name: str
    role: str  # "prosecutor", "defense", "judge"
    position: str  # "real_issue", "false_positive", "needs_context"
    argument: str = ""
    cited_evidence: list[CitedEvidence] = Field(default_factory=list)
    confidence: float = 0.5


class DebateRecord(BaseModel):
    """Record of the adversarial debate for a finding."""

    finding_id: str
    prosecutor_argument: AgentArgument
    defense_argument: AgentArgument
    judge_ruling: AgentArgument
    rebuttal: AgentArgument | None = None
    consensus: ConsensusOutcome = ConsensusOutcome.UNCLEAR
    final_severity: Severity = Severity.MEDIUM
    final_confidence: float = 0.5
    evidence_quality: str = ""


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class CrossFireReport(BaseModel):
    """Complete CrossFire analysis report."""

    repo_name: str
    pr_number: int | None = None
    pr_title: str = ""
    context: PRContext
    intent: IntentProfile
    agent_reviews: list[AgentReview] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    debates: list[DebateRecord] = Field(default_factory=list)
    overall_risk: str = "none"
    summary: str = ""
    agents_used: list[str] = Field(default_factory=list)
    review_duration_seconds: float | None = None
