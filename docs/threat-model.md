# Threat Model

## What xFire Detects

xFire is designed to catch security issues and dangerous bugs that traditional SAST tools miss:

### Security Issues
- Injection vulnerabilities with actual data flow traces
- Auth/authz regressions (removed middleware, weakened checks)
- Secret exposure in logs, responses, or config
- SSRF, open redirects, and webhook trust issues
- Supply chain risks from dependency changes
- CI/CD workflow vulnerabilities

### Dangerous Bugs
- Race conditions that corrupt data
- Destructive operations without safeguards
- Resource exhaustion paths
- Broken error recovery
- Partial state updates

## What xFire Does NOT Do

- It does not run code or exploit vulnerabilities
- It does not provide exploit code or attack instructions
- It does not replace penetration testing
- It does not scan for known CVEs (use Dependabot/Snyk for that)
- It does not provide compliance certification

## Prompt Injection Risks

Since xFire includes PR descriptions, commit messages, and code in agent prompts, there is a risk of prompt injection:

### Mitigations
- PR descriptions are included as data, not instructions
- Agent prompts explicitly instruct to review code, not follow embedded instructions
- Output is parsed as structured JSON, limiting execution scope
- `xfire/agents/prompts/guardrails.py` — structural prompt injection protection:
  - `inject_guard_preamble()` appended to all 5 system prompts (review, prosecutor, defense, judge ×2)
  - `wrap_external(text, source)` wraps GitHub/user content in `<external_data source="...">` tags
  - `wrap_agent_output(text, agent)` wraps prior LLM outputs in `<agent_output agent="...">` tags

## Trust Model

- **Trusted**: xFire configuration, prompt templates, skill implementations
- **Semi-trusted**: Repository code (may contain intentional dangerous capabilities)
- **Untrusted**: PR descriptions, commit messages, code comments, external input
