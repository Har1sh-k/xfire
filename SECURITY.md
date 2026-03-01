# Security Policy

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues privately via GitHub's [private vulnerability reporting](https://github.com/Har1sh-k/xfire/security/advisories/new), or by emailing the maintainer directly (contact via GitHub profile).

Please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce
- Affected versions
- Any suggested mitigations

You will receive acknowledgment within 48 hours and a resolution timeline within 7 days.

---

## Scope

Security issues relevant to CrossFire include:

- **Prompt injection** — attacks that cause agent outputs to be manipulated via crafted code or PR content
- **Credential leakage** — API keys, tokens, or auth credentials exposed in logs, output, or storage
- **Arbitrary code execution** — vulnerabilities that allow a malicious PR diff to execute code on the reviewer's machine
- **Path traversal** — file reads outside the intended repository scope
- **Subprocess injection** — malicious input reaching shell commands

---

## Security design notes

CrossFire is designed to analyse potentially malicious code. It implements several protections:

- **Prompt injection guards** — all external data (GitHub PR content, code files, agent outputs) is wrapped in tagged blocks before being included in prompts. See `xfire/agents/prompts/guardrails.py`.
- **Subprocess safety** — agent CLI calls use `asyncio.create_subprocess_exec()` with argument lists, never `shell=True`.
- **No credential storage in output** — API keys are read from environment variables and never written to reports, logs, or cached files.
- **Auth token isolation** — OAuth tokens stored in `.xfire/auth.json` are excluded from version control via `.gitignore`.

---

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅ Yes    |
