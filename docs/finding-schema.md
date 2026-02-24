# Finding Schema

## Finding Model

Every CrossFire finding includes:

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Unique identifier |
| title | string | Concise finding title |
| category | FindingCategory | From the taxonomy (47 categories) |
| severity | Critical/High/Medium/Low | Impact severity |
| confidence | 0.0-1.0 | How confident the analysis is |
| exploitability | Proven/Likely/Possible/Unlikely | How exploitable |
| blast_radius | System/Service/Component/Limited | Impact scope |
| status | Confirmed/Likely/Unclear/Rejected | After debate |
| affected_files | list[str] | Files involved |
| evidence | list[Evidence] | Supporting evidence with code citations |
| data_flow_trace | string | Input → sink path if applicable |
| purpose_aware_assessment | PurposeAssessment | Intent evaluation |
| reviewing_agents | list[str] | Which agents found this |
| debate_summary | string | Debate outcome summary |

## Evidence Requirements

Every finding MUST have:
- At least one code citation (file path + line range)
- A rationale explaining why it's a real issue
- Purpose-aware assessment (is this intended?)

## Finding Categories (47 total)

### Injection (7)
COMMAND_INJECTION, SQL_INJECTION, TEMPLATE_INJECTION, CODE_INJECTION, NOSQL_INJECTION, LDAP_INJECTION, XPATH_INJECTION

### Data Security (7)
UNSAFE_DESERIALIZATION, SECRET_EXPOSURE, TOKEN_LEAKAGE, SENSITIVE_DATA_LOGGING, PRIVACY_LEAK, CRYPTO_MISUSE, KEY_HANDLING

### Auth & Access (5)
AUTH_BYPASS, AUTHZ_REGRESSION, PRIVILEGE_ESCALATION, TRUST_BOUNDARY_VIOLATION, MISSING_AUTH_CHECK

### Network & Web (6)
SSRF, OPEN_REDIRECT, WEBHOOK_TRUST, PERMISSIVE_CORS, INSECURE_DEFAULT, DEBUG_ENABLED

### Filesystem & Execution (4)
PATH_TRAVERSAL, FILE_PERMISSION, SANDBOX_ESCAPE, ARBITRARY_CODE_EXEC

### Supply Chain (2)
DEPENDENCY_RISK, SUPPLY_CHAIN

### Infrastructure (3)
CI_WORKFLOW_RISK, INFRA_MISCONFIG, CONTAINER_ESCAPE

### Dangerous Bugs (13)
RACE_CONDITION, DATA_CORRUPTION, DESTRUCTIVE_OP_NO_SAFEGUARD, MISSING_VALIDATION, NULL_HANDLING_CRASH, RETRY_STORM, INFINITE_LOOP, RESOURCE_EXHAUSTION, BROKEN_ROLLBACK, MIGRATION_HAZARD, MISSING_RATE_LIMIT, MISSING_INPUT_VALIDATION, MISSING_SIGNATURE_VERIFICATION, ERROR_SWALLOWING, PARTIAL_STATE_UPDATE, CONNECTION_LEAK
