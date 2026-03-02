# Finding Schema

## Finding Model

Every xFire finding includes:

| Field | Type | Description |
|-------|------|-------------|
| id | str (UUID) | Unique identifier |
| title | str | Concise finding title |
| category | FindingCategory | From the taxonomy (50 categories) |
| severity | Critical/High/Medium/Low | Impact severity |
| confidence | float 0.0–1.0 | How confident the analysis is |
| exploitability | Proven/Likely/Possible/Unlikely | How exploitable |
| blast_radius | System/Service/Component/Limited | Impact scope |
| status | Confirmed/Likely/Unclear/Rejected | After debate |
| affected_files | list[str] | Files involved |
| line_ranges | list[LineRange] | Specific line ranges in affected files |
| diff_hunks | list[str] | Raw diff hunks related to the finding |
| evidence | list[Evidence] | Supporting evidence with code citations |
| data_flow_trace | str \| None | Input → sink path if applicable |
| reproduction_risk_notes | str | Notes on reproduction risk |
| mitigations | list[str] | Suggested mitigations |
| rationale_summary | str | Summary of why this is a real finding |
| purpose_aware_assessment | PurposeAssessment | Intent evaluation |
| reviewing_agents | list[str] | Which agents found this |
| debate_summary | str \| None | Debate outcome summary |
| consensus_outcome | str \| None | Final consensus after debate |
| debate_tag | DebateTag | Routing tag: needs_debate / auto_confirmed / informational |

## Evidence Requirements

Every finding MUST have:
- At least one code citation (file path + line range)
- A rationale explaining why it's a real issue
- Purpose-aware assessment (is this intended?)

## Finding Categories (50 total)

### Injection (7)
`COMMAND_INJECTION`, `SQL_INJECTION`, `TEMPLATE_INJECTION`, `CODE_INJECTION`, `NOSQL_INJECTION`, `LDAP_INJECTION`, `XPATH_INJECTION`

### Data Security (7)
`UNSAFE_DESERIALIZATION`, `SECRET_EXPOSURE`, `TOKEN_LEAKAGE`, `SENSITIVE_DATA_LOGGING`, `PRIVACY_LEAK`, `CRYPTO_MISUSE`, `KEY_HANDLING`

### Auth & Access (5)
`AUTH_BYPASS`, `AUTHZ_REGRESSION`, `PRIVILEGE_ESCALATION`, `TRUST_BOUNDARY_VIOLATION`, `MISSING_AUTH_CHECK`

### Network & Web (6)
`SSRF`, `OPEN_REDIRECT`, `WEBHOOK_TRUST`, `PERMISSIVE_CORS`, `INSECURE_DEFAULT`, `DEBUG_ENABLED`

### Filesystem & Execution (4)
`PATH_TRAVERSAL`, `FILE_PERMISSION`, `SANDBOX_ESCAPE`, `ARBITRARY_CODE_EXEC`

### Supply Chain (2)
`DEPENDENCY_RISK`, `SUPPLY_CHAIN`

### Infrastructure (3)
`CI_WORKFLOW_RISK`, `INFRA_MISCONFIG`, `CONTAINER_ESCAPE`

### Dangerous Bugs (16)
`RACE_CONDITION`, `DATA_CORRUPTION`, `DESTRUCTIVE_OP_NO_SAFEGUARD`, `MISSING_VALIDATION`, `NULL_HANDLING_CRASH`, `RETRY_STORM`, `INFINITE_LOOP`, `RESOURCE_EXHAUSTION`, `BROKEN_ROLLBACK`, `MIGRATION_HAZARD`, `MISSING_RATE_LIMIT`, `MISSING_INPUT_VALIDATION`, `MISSING_SIGNATURE_VERIFICATION`, `ERROR_SWALLOWING`, `PARTIAL_STATE_UPDATE`, `CONNECTION_LEAK`

## Debate Routing

After synthesis, each finding is tagged with a `DebateTag`:

| Tag | Meaning |
|-----|---------|
| `needs_debate` | Finding goes through the full adversarial debate |
| `auto_confirmed` | High cross-validation agreement — confirmed without debate |
| `informational` | Low severity / informational only — no debate required |

## DebateRecord Model

Each debated finding produces a `DebateRecord`:

| Field | Description |
|-------|-------------|
| finding_id | Links back to the Finding |
| prosecutor_argument | Round 1 prosecution argument |
| defense_argument | Round 1 defense response |
| judge_ruling | Final judge ruling |
| judge_questions | Judge's clarifying questions (Round 2, if triggered) |
| round_2_prosecution | Round 2 prosecution response to judge questions |
| round_2_defense | Round 2 defense response to judge questions |
| rounds_used | 1 or 2 |
| consensus | Final ConsensusOutcome enum value |
| final_severity | Severity after debate (may differ from initial) |
| final_confidence | Confidence after debate |
| evidence_quality | Judge's assessment of evidence quality |
