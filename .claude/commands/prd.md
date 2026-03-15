---
description: Show a human-readable summary of the current prd.json — architecture decisions, requirements, open questions, and risks.
---

Read `prd.json` and produce a clean, human-readable summary:

## Project: [name] — v[version]
**Status**: [status] | **Last updated**: [date]

**Description**: [description]

---

### Architecture Decisions ([count])
For each ADR, show: ID | Title | Status | One-line summary of decision

### Requirements
**Functional** ([open count] open / [total] total):
List open functional requirements

**Non-Functional** ([open count] open / [total] total):
List open NFRs

### Open Questions ([count])
List all open questions with who raised them

### Risks ([count])
List risks by severity (high → low)

### Component Status
Show a table: Component | Stack | Status

---

If prd.json doesn't exist yet, suggest running `/architect-init`.
