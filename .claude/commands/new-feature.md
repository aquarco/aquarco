---
description: Implement a new feature with full agent coordination. Pass the feature description as argument.
---

Implement a new feature using the full agent pipeline.

Feature request: $ARGUMENTS

Invoke the `solution-architect` agent with this task:

"Implement the following feature: '$ARGUMENTS'

Follow this workflow:
1. Read prd.json to understand current architecture
2. Break the feature into sub-tasks by domain:
   - Database changes needed → delegate to `database` agent
   - GraphQL schema/resolver changes → delegate to `graphql` agent  
   - Frontend components needed → delegate to `frontend` agent
   - Security considerations → consult `security` agent
   - Tests needed → delegate to `testing` agent
   - Infrastructure changes → delegate to `dev-infra` or `production` agent
   - Scripts needed → delegate to `scripting` agent

3. Coordinate agent work (run independent tasks in parallel)
4. After implementation, run `qa` agent review on all new files
5. Record the feature and its architectural decisions in prd.json via ralph agent

Report: what was built, what agents were used, and what's left to do."
