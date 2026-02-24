# Evaluation Plan

## Metrics

### Precision
- What percentage of flagged findings are real issues?
- Target: >80% for Confirmed findings

### Recall
- What percentage of real issues are caught?
- Measured against fixture PRs with known issues

### False Positive Rate
- Especially for intended capabilities (the purpose-aware test)
- Target: 0 false positives on `intended_exec_with_sandbox` and `safe_refactor_no_issues`

### Cross-Validation Rate
- How often do multiple agents agree?
- Higher agreement = higher confidence in the finding

## Test Fixtures

7 scenarios covering the spectrum:

| Fixture | Expected Findings | Purpose |
|---------|------------------|---------|
| auth_bypass_regression | 1 Critical AUTH_BYPASS | True positive |
| command_injection_exposure | 1 Critical COMMAND_INJECTION | True positive |
| intended_exec_with_sandbox | 0 | False positive test |
| secret_logging | 1 High SENSITIVE_DATA_LOGGING | True positive |
| destructive_migration | 1 High DESTRUCTIVE_OP_NO_SAFEGUARD | True positive |
| race_condition_data_corruption | 1 Medium RACE_CONDITION | True positive |
| safe_refactor_no_issues | 0 | False positive test |

## Evaluation Process

1. Run each fixture through the full pipeline
2. Compare findings against expected.json
3. Track precision/recall per agent and overall
4. Track which agents have best precision/recall
5. Use results to refine prompts and role assignment
