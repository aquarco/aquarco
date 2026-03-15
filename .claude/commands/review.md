---
description: Run a full multi-agent review of recent changes. QA, security, and testing agents all review the latest diff.
---

Run a comprehensive review of recent changes using the specialist agents.

Invoke these subagents in parallel:
1. `qa` agent: "Review all files modified in the last git commit or staging area for code quality issues"
2. `security` agent: "Audit all files modified in the last git commit or staging area for security vulnerabilities"
3. `testing` agent: "Identify which changed files lack test coverage and generate a list of missing tests"

Then invoke the `solution-architect` agent to:
- Synthesize the findings from all three reviews
- Prioritize issues by severity
- Recommend action items
- Record any architectural insights via the ralph agent

Output a consolidated review report.
