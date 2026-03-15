# Changelog

## [2026-03-06] — E2E agent for mission-critical flows

### Added
- `e2e` agent (`bright_magenta`) — owns Playwright end-to-end tests for three mission-critical areas:
  - **User registration**: full signup flow, validation errors, duplicate email handling
  - **User portfolio management**: create/view/edit/delete, auth guard redirect, empty state
  - **Public pages smoke tests**: all public routes render without JS console errors or failed requests
- `e2e/` directory structure convention: `fixtures/`, `pages/` (Page Objects), `tests/`
- Hook routing: `e2e` category triggered by changes to `e2e/`, `playwright.config`, `register`, `portfolio`, or `middleware.ts`
- Mission-critical section added to CLAUDE.md

### Changed
- `solution-architect` delegation list updated — auth/portfolio/layout/middleware changes now route to `e2e` agent
- README architecture diagram and file structure updated

## [2026-03-06] — Docs agent + agent system refinements

### Added
- `docs` agent (`bright_cyan`) — keeps CLAUDE.md, README.md, and CHANGELOG.md up to date after significant changes
- Colour assigned to every agent in frontmatter for visual identification in Claude Code
- `tasks/` folder convention — solution-architect now writes a detailed `TASK-NNN-<slug>.md` file before delegating work
- Hook skip rule for CLAUDE.md, README.md, CHANGELOG.md to prevent re-triggering when docs agent writes

### Changed
- `ralph` agent now writes to `prd.json` **only on explicit request** — no longer invoked automatically after every change
- `solution-architect` delegation list updated to include `docs` agent
- `orchestrate-on-change.sh` — added `docs` category routing for `.claude/` config file changes; added ralph suppression note in context message
- CLAUDE.md and README.md updated to reflect new agent roster and revised ralph behaviour
