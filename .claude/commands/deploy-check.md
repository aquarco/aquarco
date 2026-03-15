---
description: Pre-deployment checklist. Runs security, QA, and production agent checks before any deployment.
---

Run a pre-deployment validation. This must pass before any production deployment.

Invoke these subagents sequentially (order matters — block on failures):

**Step 1** — `security` agent:
"Run a full security audit of the codebase. Check for: hardcoded secrets, missing auth on endpoints, vulnerable dependencies (check package.json/lockfile), improper CORS config, missing rate limiting. Return PASS or FAIL with details."

**Step 2** — `qa` agent (only if security passes):
"Review the codebase for any blocking quality issues that would affect production stability. Focus on: error handling, null checks, missing environment variable validation, and any TODOs marked as blocking."

**Step 3** — `dev-infra` agent:
"Review Docker Compose configuration for: health checks configured, secrets not hardcoded, volumes properly mounted, port conflicts checked, resource limits set. Return a deployment readiness report."

**Step 4** — `solution-architect` agent:
"Synthesize the deployment check results. If all pass, confirm deployment readiness. If any fail, list blocking items. Record the deployment check result in prd.json via ralph."

Output final status: ✅ READY TO DEPLOY or ❌ BLOCKED (with reasons).
