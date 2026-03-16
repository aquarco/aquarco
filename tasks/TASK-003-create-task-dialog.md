# TASK-003: Create Task Dialog for Tasks Page

**Status**: done
**Created**: 2026-03-16
**Triggered by**: manual (user request)
**Agents involved**: solution-architect, frontend, testing, qa

## Context
The Tasks page (`/tasks`) currently displays tasks in a table with filters but lacks the ability for users to create new tasks directly from the UI. The GraphQL mutation `createTask` already exists and is defined in the frontend queries but is not wired up to any UI component.

The existing Repos page provides an excellent pattern for modal dialogs using MUI components. We should follow that pattern for consistency.

## Objective
Implement a "Create Task" dialog on the Tasks page that allows users to submit work to an agent. The dialog must:
1. Be accessible via a button in the page header
2. Include form fields for: title, category, repository, priority, and initial context
3. Call the existing `createTask` GraphQL mutation
4. Refresh the task list on success
5. Display validation errors appropriately

## Scope
**In scope:**
- Create Task dialog component
- Integration with existing `CREATE_TASK` mutation
- Form validation (client-side)
- Success/error handling
- Task list refresh on success
- Test coverage

**Out of scope:**
- Database schema changes (mutation already exists)
- GraphQL resolver changes (already implemented)
- Pipeline selection (use default)

## Subtasks
- [x] Analyze existing code patterns (repos page dialog) — assigned to: solution-architect
- [x] Implement CreateTaskDialog component — assigned to: frontend
- [x] Add "Create Task" button to Tasks page header — assigned to: frontend
- [x] Wire up mutation and list refresh — assigned to: frontend
- [ ] Add unit tests for dialog component — assigned to: testing
- [ ] QA review of implementation — assigned to: qa

## Acceptance Criteria
- A "Create Task" button appears on the Tasks page header
- Clicking the button opens a modal dialog
- Dialog contains: title (text), category (dropdown), repository (dropdown from existing repos), priority (slider 0-100, default 5), initial context (JSON textarea, optional)
- Form validates required fields before submission
- Successful submission creates the task and refreshes the list
- Errors from the mutation are displayed in the dialog
- Dialog can be cancelled without side effects
- The component follows existing code patterns (Repos page dialog)

## Notes
- The `CreateTaskInput` expects a `source` field (required) — we should default this to "web-ui" for manually created tasks
- Priority defaults to 5 per the feature request
- Initial context must be valid JSON if provided
- Follow MUI patterns used in ReposPage for consistency
