---
name: architecture
description: |
  Loads current project architecture context from prd.json.
  Auto-loaded when Claude is making design decisions, discussing system structure,
  evaluating trade-offs, or when prd.json is referenced.
---

# Project Architecture Context

Before making any architectural decision, load and read `prd.json`.

## Key Principles for This Project

### API Layer (GraphQL)
- Schema-first design: define GraphQL types before implementing resolvers
- All mutations return payload types with `errors` field
- Relay-style pagination for all list queries
- DataLoader mandatory for all related-entity fetching

### Frontend (Next.js + React + MUI)
- App Router with Server Components as default
- Client components only where interactivity requires it
- MUI theme customization over inline styles
- `react-hook-form` + `zod` for all forms
- Generated GraphQL types via `graphql-codegen`

### Database (PostgreSQL)
- Schema changes always through migrations
- UUID primary keys for public entities
- All timestamps with timezone (`TIMESTAMPTZ`)
- Read replicas for heavy reporting queries

### Infrastructure
- Runtime: Docker Compose with source mounts and hot reload (no k3s/Kubernetes)
- Secrets: never in code, use environment variables

## When to Update prd.json
Invoke the `ralph` agent whenever:
- A new architectural pattern is established
- A technology choice is made or changed
- A significant trade-off is accepted
- An open question is resolved
