# Review Methodology

## How xFire Reviews Code

xFire's agents review code like senior security engineers — by reading and reasoning, not pattern matching.

### 1. Understand Context
- What does the repository do?
- What is the PR trying to accomplish?
- What capabilities are intentionally part of this software?
- Where are the trust boundaries?

### 2. Read the Diff
- What was added, removed, modified?
- Were security controls changed?
- Was the attack surface expanded?

### 3. Trace Data Flows
- Can untrusted input reach a dangerous operation?
- Is there validation/sanitization along the path?
- Are there cross-file data flows to consider?

### 4. Check for Missing Controls
- Authentication on sensitive endpoints
- Input validation on user-facing data
- Rate limiting on resource-intensive operations
- Audit logging for sensitive operations
- Rollback mechanisms for destructive operations

### 5. Assess Dangerous Bugs
- Race conditions
- Missing error handling
- Resource exhaustion
- Broken error recovery
- Partial state updates

## Purpose-Aware Thinking

The key differentiator: xFire asks "Is this INTENDED?" before flagging.

A subprocess.run() in a coding agent's sandbox executor is different from a subprocess.run() in a web API endpoint. xFire understands this.

### Decision Framework

Before flagging, agents check:
1. Is this capability intended for this product?
2. What is the trust boundary? Who can trigger this?
3. Can untrusted input reach this code path?
4. Are there isolation controls?
5. Are there policy/allowlist checks?
6. Is this enabled by default or opt-in?
7. Can this be triggered remotely?

Only flag when: **exposure + missing controls + viable abuse path** ALL exist.
