---
description: Initialize a new project with the solution architect. Sets up prd.json, asks for project goals, and creates initial architecture decisions.
---

You are starting a new project initialization session.

Invoke the `solution-architect` subagent with this task:

"Initialize this project by:
1. Asking the user for: project name, description, main goals, and tech stack confirmation
2. Updating prd.json project section with the answers
3. Creating initial architecture decisions for the confirmed tech stack choices
4. Identifying the top 3 open questions that need to be resolved
5. Invoke the ralph agent to write all of this to prd.json
6. Report a summary of what was recorded"
