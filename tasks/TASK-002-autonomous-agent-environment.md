# TASK-002: Autonomous Agent Execution Environment

**Status**: open
**Created**: 2026-03-14
**Triggered by**: manual (architecture design request)
**Agents involved**: solution-architect, scripting, dev-infra, database, frontend, graphql

## Context

TASK-001 defines the VirtualBox sandbox architecture with a basic agent supervisor concept. This task elaborates on the **modular, configurable agent execution environment** that runs inside the VM. The key insight is that the supervisor is fixed infrastructure, while agents are pluggable definitions that Claude Code discovers and executes at runtime.

The system must support:
- Dynamic agent discovery (drop a file, agent becomes available)
- Task category routing (not hardcoded to specific tasks)
- Multiple polling loops for different event sources
- Pipeline orchestration (analyze -> design -> implement)

## Objective

Design a complete, production-ready autonomous agent execution environment that:
1. Defines a declarative agent definition format
2. Implements runtime agent discovery
3. Provides a robust supervisor with multiple polling loops
4. Routes tasks through category-based pipelines
5. Is extensible for future event sources and agent types

---

## 1. Agent Definition Format

Agents are defined in YAML files located in a dedicated directory. Each file describes one agent's capabilities, categories, resource requirements, and behavioral constraints.

### Directory Structure (Host)

```
aquarco/
├── agents/
│   ├── definitions/           # Agent definition files (YAML)
│   │   ├── review-agent.yaml
│   │   ├── implementation-agent.yaml
│   │   ├── test-agent.yaml
│   │   ├── design-agent.yaml
│   │   ├── docs-agent.yaml
│   │   └── analyze-agent.yaml
│   └── prompts/               # System prompts for agents (Markdown)
│       ├── review-agent.md
│       ├── implementation-agent.md
│       └── ...
```

### Agent Definition Schema

```yaml
# agents/definitions/review-agent.yaml
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: review-agent
  version: "1.0.0"
  description: "Reviews code changes, PRs, and commits for quality and correctness"

spec:
  # Categories this agent can handle
  categories:
    - review

  # Priority within category (lower = higher priority)
  priority: 10

  # System prompt file (relative to agents/prompts/)
  promptFile: review-agent.md

  # Tools this agent is allowed to use
  tools:
    allowed:
      - Read
      - Bash
      - Grep
      - Glob
    denied:
      - Write
      - Edit  # Review agent cannot modify files

  # Resource limits
  resources:
    maxTokens: 100000
    timeoutMinutes: 30
    maxConcurrent: 2  # Max concurrent instances of this agent

  # Environment variables passed to agent
  environment:
    AGENT_MODE: "review"
    STRICT_MODE: "true"

  # Capabilities / permissions
  capabilities:
    canPush: false
    canCreatePR: true
    canCommentOnPR: true
    canApprove: true
    canMerge: false
    canAccessDocker: false
    canAccessK8s: false

  # Output behavior
  output:
    format: "github-pr-comment"  # or "task-file", "commit", "issue"
    mustInclude:
      - summary
      - findings
      - recommendation

  # Output schema for context contracts (see Section 4)
  outputSchema:
    type: object
    required: [summary, findings, recommendation, severity]
    properties:
      summary:
        type: string
        description: "Brief summary of the review"
      findings:
        type: array
        items:
          type: object
          properties:
            file: { type: string }
            line: { type: integer }
            severity: { type: string, enum: [info, warning, error, critical] }
            message: { type: string }
      recommendation:
        type: string
        enum: [approve, request_changes, comment]
      severity:
        type: string
        enum: [clean, minor_issues, major_issues, blocking]

  # Dependencies on other agents (for pipelines)
  triggers:
    # What this agent produces
    produces:
      - review-complete
    # What this agent consumes (prerequisites)
    consumes: []

  # Health check
  healthCheck:
    enabled: true
    intervalSeconds: 300
```

### Full Schema Reference

```yaml
# Complete agent definition schema
apiVersion: aquarco.agents/v1   # Required, API version
kind: AgentDefinition          # Required, must be "AgentDefinition"

metadata:
  name: string                 # Required, unique identifier (kebab-case)
  version: string              # Required, semver
  description: string          # Required, human-readable description
  labels:                      # Optional, for filtering/selection
    team: string
    domain: string
  annotations:                 # Optional, arbitrary metadata
    key: value

spec:
  categories:                  # Required, list of task categories
    - review | implementation | test | design | docs | analyze

  priority: integer            # Optional, default 50, lower = higher priority

  promptFile: string           # Required, path to system prompt markdown

  tools:
    allowed: [string]          # Optional, whitelist (default: all)
    denied: [string]           # Optional, blacklist (takes precedence)

  resources:
    maxTokens: integer         # Optional, default 100000
    timeoutMinutes: integer    # Optional, default 30
    maxConcurrent: integer     # Optional, default 1
    memoryMB: integer          # Optional, memory limit for agent process

  environment:                 # Optional, env vars for agent
    KEY: value

  capabilities:
    canPush: boolean           # Default: true
    canCreatePR: boolean       # Default: true
    canCommentOnPR: boolean    # Default: true
    canApprove: boolean        # Default: false
    canMerge: boolean          # Default: false
    canAccessDocker: boolean   # Default: false
    canAccessK8s: boolean      # Default: false
    canCreateIssues: boolean   # Default: true
    canCloseIssues: boolean    # Default: false

  output:
    format: string             # task-file | github-pr-comment | commit | issue | none
    mustInclude: [string]      # Required fields in output

  outputSchema:                # JSON Schema for structured output validation
    type: object
    required: [string]
    properties: {}

  triggers:
    produces: [string]         # Events this agent emits
    consumes: [string]         # Events this agent requires

  healthCheck:
    enabled: boolean           # Default: true
    intervalSeconds: integer   # Default: 300

  # Advanced: conditional activation
  conditions:
    filePatterns: [string]     # Only activate for matching files
    branchPatterns: [string]   # Only activate for matching branches
    labels: [string]           # Only activate if issue/PR has these labels
```

### Example Agents

```yaml
# agents/definitions/implementation-agent.yaml
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: implementation-agent
  version: "1.0.0"
  description: "Implements features, fixes bugs, writes code"

spec:
  categories:
    - implementation

  priority: 10
  promptFile: implementation-agent.md

  tools:
    allowed:
      - Read
      - Write
      - Edit
      - Bash
      - Grep
      - Glob

  resources:
    maxTokens: 200000
    timeoutMinutes: 60
    maxConcurrent: 1

  capabilities:
    canPush: true
    canCreatePR: true
    canAccessDocker: true
    canAccessK8s: false

  output:
    format: commit
    mustInclude:
      - summary
      - files_changed
      - test_status

  outputSchema:
    type: object
    required: [summary, files_changed, test_status, commit_sha]
    properties:
      summary:
        type: string
        description: "What was implemented"
      files_changed:
        type: array
        items: { type: string }
      test_status:
        type: string
        enum: [passed, failed, skipped, not_run]
      commit_sha:
        type: string
      generated_diff:
        type: string
        description: "ref:blobs/<hash>.patch for large diffs"

  triggers:
    produces:
      - implementation-complete
    consumes:
      - design-complete  # Requires design to be done first

  conditions:
    filePatterns:
      - "src/**"
      - "lib/**"
      - "packages/**"
```

```yaml
# agents/definitions/analyze-agent.yaml
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: analyze-agent
  version: "1.0.0"
  description: "Triages issues, analyzes codebase, determines required work"

spec:
  categories:
    - analyze

  priority: 1  # Highest priority - runs first in pipelines
  promptFile: analyze-agent.md

  tools:
    allowed:
      - Read
      - Grep
      - Glob
      - Bash
    denied:
      - Write
      - Edit

  resources:
    maxTokens: 50000
    timeoutMinutes: 15
    maxConcurrent: 3

  capabilities:
    canPush: false
    canCreatePR: false
    canCommentOnPR: true
    canCreateIssues: true

  output:
    format: task-file
    mustInclude:
      - issue_summary
      - affected_components
      - recommended_pipeline
      - estimated_complexity

  outputSchema:
    type: object
    required: [issue_summary, affected_components, recommended_pipeline, estimated_complexity]
    properties:
      issue_summary:
        type: string
      affected_components:
        type: array
        items: { type: string }
      recommended_pipeline:
        type: string
        enum: [feature-pipeline, bugfix-pipeline, docs-pipeline, hotfix-pipeline]
      estimated_complexity:
        type: string
        enum: [trivial, low, medium, high, epic]
      files_to_modify:
        type: array
        items: { type: string }
      risks:
        type: array
        items: { type: string }

  triggers:
    produces:
      - analysis-complete
    consumes: []  # First in pipeline, no dependencies
```

```yaml
# agents/definitions/design-agent.yaml
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: design-agent
  version: "1.0.0"
  description: "Creates technical designs, architectural decisions, implementation plans"

spec:
  categories:
    - design

  priority: 10
  promptFile: design-agent.md

  tools:
    allowed:
      - Read
      - Write  # Can write design docs
      - Grep
      - Glob
      - Bash
    denied:
      - Edit  # Cannot modify existing code

  resources:
    maxTokens: 150000
    timeoutMinutes: 45
    maxConcurrent: 1

  capabilities:
    canPush: true
    canCreatePR: false
    canCommentOnPR: true
    canCreateIssues: true

  output:
    format: task-file
    mustInclude:
      - design_summary
      - components_affected
      - implementation_steps
      - acceptance_criteria

  outputSchema:
    type: object
    required: [design_summary, components_affected, implementation_steps, acceptance_criteria]
    properties:
      design_summary:
        type: string
      components_affected:
        type: array
        items: { type: string }
      implementation_steps:
        type: array
        items:
          type: object
          properties:
            step: { type: integer }
            description: { type: string }
            files: { type: array, items: { type: string } }
      acceptance_criteria:
        type: array
        items: { type: string }
      api_changes:
        type: object
        description: "ref:blobs/<hash>.json for large API specs"
      database_migrations:
        type: array
        items: { type: string }

  triggers:
    produces:
      - design-complete
    consumes:
      - analysis-complete

  conditions:
    labels:
      - needs-design
      - architecture
      - feature
```

```yaml
# agents/definitions/test-agent.yaml
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "Writes and runs tests, validates implementation"

spec:
  categories:
    - test

  priority: 10
  promptFile: test-agent.md

  tools:
    allowed:
      - Read
      - Write
      - Edit
      - Bash
      - Grep
      - Glob

  resources:
    maxTokens: 100000
    timeoutMinutes: 45
    maxConcurrent: 2

  capabilities:
    canPush: true
    canCreatePR: false
    canCommentOnPR: true
    canAccessDocker: true

  output:
    format: task-file
    mustInclude:
      - tests_written
      - tests_passed
      - coverage_delta

  outputSchema:
    type: object
    required: [tests_written, tests_run, tests_passed, tests_failed]
    properties:
      tests_written:
        type: array
        items: { type: string }
      tests_run:
        type: integer
      tests_passed:
        type: integer
      tests_failed:
        type: integer
      coverage_delta:
        type: number
      failures:
        type: array
        items:
          type: object
          properties:
            test: { type: string }
            error: { type: string }

  triggers:
    produces:
      - test-complete
    consumes:
      - implementation-complete
```

```yaml
# agents/definitions/docs-agent.yaml
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: docs-agent
  version: "1.0.0"
  description: "Updates documentation, README, changelogs"

spec:
  categories:
    - docs

  priority: 20
  promptFile: docs-agent.md

  tools:
    allowed:
      - Read
      - Write
      - Edit
      - Grep
      - Glob

  resources:
    maxTokens: 50000
    timeoutMinutes: 20
    maxConcurrent: 1

  capabilities:
    canPush: true
    canCreatePR: false

  output:
    format: commit
    mustInclude:
      - files_updated
      - summary

  outputSchema:
    type: object
    required: [files_updated, summary]
    properties:
      files_updated:
        type: array
        items: { type: string }
      summary:
        type: string
      sections_added:
        type: array
        items: { type: string }

  triggers:
    produces:
      - docs-complete
    consumes:
      - implementation-complete
```

---

## 2. Agent Discovery Mechanism

The agent discovery system runs at supervisor startup and watches for changes to the agent definitions directory.

### Discovery Flow

```
+---------------------------------------------------------------------+
|                      AGENT DISCOVERY                                 |
+---------------------------------------------------------------------+
|                                                                     |
|  1. On Startup                                                      |
|     +-------------------------------------------------------------+ |
|     |  Scan /home/agent/aquarco/agents/definitions/*.yaml         | |
|     |           |                                                 | |
|     |           v                                                 | |
|     |  Parse and validate each YAML against schema                | |
|     |           |                                                 | |
|     |           v                                                 | |
|     |  Build agent registry (in-memory map)                       | |
|     |           |                                                 | |
|     |           v                                                 | |
|     |  Build category index (category -> [agents])                | |
|     +-------------------------------------------------------------+ |
|                                                                     |
|  2. On File Change (inotify/fswatch)                               |
|     +-------------------------------------------------------------+ |
|     |  Detect added/modified/deleted YAML files                   | |
|     |           |                                                 | |
|     |           v                                                 | |
|     |  Re-validate changed definitions                            | |
|     |           |                                                 | |
|     |           v                                                 | |
|     |  Update registry (hot reload, no restart needed)            | |
|     +-------------------------------------------------------------+ |
|                                                                     |
|  3. Runtime Query                                                   |
|     +-------------------------------------------------------------+ |
|     |  get_agents_for_category("review")                          | |
|     |           |                                                 | |
|     |           v                                                 | |
|     |  Returns: [review-agent] sorted by priority                 | |
|     +-------------------------------------------------------------+ |
|                                                                     |
+---------------------------------------------------------------------+
```

### Agent Registry Data Structure

```python
# Conceptual data structure (actual implementation in shell/Python)
agent_registry = {
    "review-agent": {
        "definition": <parsed YAML>,
        "status": "available",
        "active_instances": 0,
        "last_used": timestamp,
        "prompt_content": <loaded from .md file>
    },
    "implementation-agent": { ... },
    ...
}

category_index = {
    "review": ["review-agent"],
    "implementation": ["implementation-agent"],
    "test": ["test-agent"],
    "design": ["design-agent"],
    "docs": ["docs-agent"],
    "analyze": ["analyze-agent"]
}
```

### Discovery Script

```bash
#!/bin/bash
# /usr/local/bin/aquarco-discover-agents
# Discovers and validates agent definitions

set -euo pipefail

AGENTS_DIR="/home/agent/aquarco/agents/definitions"
REGISTRY_FILE="/var/lib/aquarco/agent-registry.json"

discover_agents() {
    local agents=()

    for yaml_file in "$AGENTS_DIR"/*.yaml; do
        [[ -f "$yaml_file" ]] || continue

        # Validate YAML structure
        if ! yq eval '.' "$yaml_file" > /dev/null 2>&1; then
            echo "WARN: Invalid YAML in $yaml_file" >&2
            continue
        fi

        # Extract agent metadata
        local name=$(yq eval '.metadata.name' "$yaml_file")
        local version=$(yq eval '.metadata.version' "$yaml_file")
        local categories=$(yq eval '.spec.categories | join(",")' "$yaml_file")
        local priority=$(yq eval '.spec.priority // 50' "$yaml_file")
        local prompt_file=$(yq eval '.spec.promptFile' "$yaml_file")

        # Verify prompt file exists
        local prompt_path="/home/agent/aquarco/agents/prompts/$prompt_file"
        if [[ ! -f "$prompt_path" ]]; then
            echo "WARN: Prompt file not found: $prompt_path" >&2
            continue
        fi

        # Add to registry
        agents+=("{\"name\":\"$name\",\"version\":\"$version\",\"categories\":\"$categories\",\"priority\":$priority,\"file\":\"$yaml_file\"}")
    done

    # Build JSON registry
    printf '{"agents":[%s],"discovered_at":"%s"}' \
        "$(IFS=,; echo "${agents[*]}")" \
        "$(date -Iseconds)" > "$REGISTRY_FILE"

    echo "Discovered $(echo ${#agents[@]}) agents"
}

discover_agents
```

### Agent Registry JSON Format

```json
{
  "agents": [
    {
      "name": "review-agent",
      "version": "1.0.0",
      "categories": ["review"],
      "priority": 10,
      "status": "available",
      "file": "/home/agent/aquarco/agents/definitions/review-agent.yaml",
      "prompt_file": "/home/agent/aquarco/agents/prompts/review-agent.md",
      "active_instances": 0
    },
    {
      "name": "implementation-agent",
      "version": "1.0.0",
      "categories": ["implementation"],
      "priority": 10,
      "status": "available",
      "file": "/home/agent/aquarco/agents/definitions/implementation-agent.yaml",
      "prompt_file": "/home/agent/aquarco/agents/prompts/implementation-agent.md",
      "active_instances": 0
    }
  ],
  "category_index": {
    "review": ["review-agent"],
    "implementation": ["implementation-agent"],
    "test": ["test-agent"],
    "design": ["design-agent"],
    "docs": ["docs-agent"],
    "analyze": ["analyze-agent"]
  },
  "discovered_at": "2026-03-14T10:30:00Z",
  "schema_version": "v1"
}
```

---

## 3. Supervisor Architecture

The supervisor is a fixed systemd service that runs continuously, polling external sources and creating internal tasks for agents to process.

### Supervisor Components

```
+---------------------------------------------------------------------+
|                       SUPERVISOR SERVICE                             |
|                    (aquarco-supervisor.service)                       |
+---------------------------------------------------------------------+
|                                                                     |
|  +---------------------------------------------------------------+  |
|  |                    MAIN CONTROL LOOP                           |  |
|  |                                                                |  |
|  |   +-----------+  +-----------+  +-----------+                  |  |
|  |   |  GitHub   |  |  GitHub   |  | External  |                  |  |
|  |   |  Tasks    |  |  Source   |  | Trigger   |                  |  |
|  |   |  Poller   |  |  Poller   |  | Interface |                  |  |
|  |   +-----+-----+  +-----+-----+  +-----+-----+                  |  |
|  |         |              |              |                        |  |
|  |         v              v              v                        |  |
|  |   +-------------------------------------------------------+   |  |
|  |   |              TASK QUEUE (PostgreSQL)                   |   |  |
|  |   |   - Pending tasks                                      |   |  |
|  |   |   - In-progress tasks                                  |   |  |
|  |   |   - Completed tasks                                    |   |  |
|  |   |   - Stage context (structured output per stage)        |   |  |
|  |   +-------------------------------------------------------+   |  |
|  |                          |                                     |  |
|  |                          v                                     |  |
|  |   +-------------------------------------------------------+   |  |
|  |   |             TASK DISPATCHER                            |   |  |
|  |   |   - Routes tasks to appropriate category               |   |  |
|  |   |   - Selects agent based on priority                    |   |  |
|  |   |   - Spawns Claude Code with agent prompt               |   |  |
|  |   |   - Monitors execution, handles timeouts               |   |  |
|  |   +-------------------------------------------------------+   |  |
|  |                          |                                     |  |
|  |                          v                                     |  |
|  |   +-------------------------------------------------------+   |  |
|  |   |             AGENT EXECUTOR                             |   |  |
|  |   |   - Runs: claude --agent <agent-prompt>                |   |  |
|  |   |   - Validates output against schema                    |   |  |
|  |   |   - Stores structured context in PostgreSQL            |   |  |
|  |   |   - Updates task status                                |   |  |
|  |   |   - Triggers next pipeline stage                       |   |  |
|  |   +-------------------------------------------------------+   |  |
|  |                                                                |  |
|  +---------------------------------------------------------------+  |
|                                                                     |
+---------------------------------------------------------------------+
```

### Supervisor Configuration File

```yaml
# /etc/aquarco/supervisor.yaml
apiVersion: aquarco.supervisor/v1
kind: SupervisorConfig

metadata:
  name: aquarco-supervisor
  version: "1.0.0"

spec:
  # Working directory
  workdir: /home/agent/aquarco

  # Agent definitions location
  agentsDir: /home/agent/aquarco/agents/definitions
  promptsDir: /home/agent/aquarco/agents/prompts

  # Task queue database (PostgreSQL)
  # Supervisor uses direct connections (runs one pipeline at a time, 3-4 connections max)
  # No PgBouncer needed — Web UI uses its own pooled connection (Prisma built-in pool)
  taskQueue:
    driver: postgresql
    host: localhost
    port: 5432
    database: aquarco
    user: aquarco
    passwordFile: /home/agent/.postgres-password
    retentionDays: 30

  # Config hot-reload: Web UI writes validated YAML, sends SIGHUP to supervisor PID
  # Supervisor catches SIGHUP, re-reads config
  configReload:
    signal: SIGHUP
    validateBeforeWrite: true

  # Blob storage for large context items
  blobStorage:
    path: /var/lib/aquarco/blobs
    maxSizeMB: 50  # Max size per blob

  # Logging
  logging:
    level: info
    file: /var/log/aquarco/supervisor.log
    maxSizeMB: 100
    maxFiles: 5

  # Resource limits for all agents
  globalLimits:
    maxConcurrentAgents: 3
    maxTokensPerHour: 1000000
    cooldownBetweenTasksSeconds: 5

  # Polling loops configuration
  pollers:
    # GitHub Issues / Projects poller
    - name: github-tasks
      type: github-tasks
      enabled: true
      intervalSeconds: 60
      config:
        repository: "owner/repo"
        # Watch these sources for tasks
        sources:
          - type: issues
            labels:
              - agent-task
              - automated
            states:
              - open
          - type: project
            projectNumber: 1
            column: "To Do"
        # How to categorize tasks
        categorization:
          defaultCategory: analyze
          labelMapping:
            bug: implementation
            feature: analyze
            docs: docs
            test: test
            review-needed: review
            design-needed: design

    # GitHub Source (commits, PRs) poller
    - name: github-source
      type: github-source
      enabled: true
      intervalSeconds: 30
      config:
        repository: "owner/repo"
        # Watch for these events
        watch:
          - type: pull_request
            states:
              - open
            actions:
              - opened
              - synchronize
          - type: push
            branches:
              - main
              - "feature/*"
        # What to trigger
        triggers:
          on_pr_opened:
            - category: review
            - category: test
          on_pr_updated:
            - category: review
          on_push_main:
            - category: docs
            - category: test

    # External trigger interface (file-based)
    - name: external-triggers
      type: file-watch
      enabled: true
      intervalSeconds: 10
      config:
        watchDir: /var/lib/aquarco/triggers
        processedDir: /var/lib/aquarco/triggers/processed

  # Pipeline definitions
  pipelines:
    # Feature implementation pipeline
    - name: feature-pipeline
      trigger:
        labels:
          - feature
          - enhancement
      stages:
        - category: analyze
          required: true
        - category: design
          required: true
          conditions:
            - "analysis.complexity >= medium"
        - category: implementation
          required: true
        - category: test
          required: true
        - category: docs
          required: false
        - category: review
          required: true

    # Bug fix pipeline
    - name: bugfix-pipeline
      trigger:
        labels:
          - bug
      stages:
        - category: analyze
          required: true
        - category: implementation
          required: true
        - category: test
          required: true
        - category: review
          required: true

    # PR review pipeline
    - name: pr-review-pipeline
      trigger:
        events:
          - pr_opened
          - pr_updated
      stages:
        - category: review
          required: true
        - category: test
          required: true

  # Health monitoring
  health:
    enabled: true
    reportIntervalMinutes: 30
    reportDestination: github-issue
    issueNumber: 1  # Status issue

  # Secrets reference (loaded from files)
  secrets:
    githubTokenFile: /home/agent/.github-token
    anthropicKeyFile: /home/agent/.anthropic-key
```

### Poller Implementations

#### GitHub Tasks Poller

```bash
#!/bin/bash
# /usr/local/lib/aquarco/pollers/github-tasks.sh
# Polls GitHub Issues and Project Board for new tasks

poll_github_tasks() {
    local repo="$1"
    local labels="$2"

    # Fetch open issues with agent-task label
    gh issue list \
        --repo "$repo" \
        --label "$labels" \
        --state open \
        --json number,title,labels,body,createdAt \
        --limit 50
}

process_issue() {
    local issue_json="$1"
    local issue_number=$(echo "$issue_json" | jq -r '.number')
    local issue_title=$(echo "$issue_json" | jq -r '.title')
    local labels=$(echo "$issue_json" | jq -r '.labels[].name' | tr '\n' ',')

    # Check if already processed
    if task_exists "github-issue-$issue_number"; then
        return 0
    fi

    # Determine category from labels
    local category="analyze"  # Default
    if [[ "$labels" == *"bug"* ]]; then
        category="implementation"
    elif [[ "$labels" == *"docs"* ]]; then
        category="docs"
    elif [[ "$labels" == *"test"* ]]; then
        category="test"
    fi

    # Create internal task
    create_task \
        --id "github-issue-$issue_number" \
        --title "$issue_title" \
        --category "$category" \
        --source "github-issue" \
        --source-ref "$issue_number" \
        --pipeline "feature-pipeline"
}
```

#### GitHub Source Poller

```bash
#!/bin/bash
# /usr/local/lib/aquarco/pollers/github-source.sh
# Polls for new commits and PRs

poll_github_prs() {
    local repo="$1"

    # Get open PRs
    gh pr list \
        --repo "$repo" \
        --state open \
        --json number,title,headRefName,updatedAt,additions,deletions \
        --limit 20
}

poll_recent_commits() {
    local repo="$1"
    local branch="$2"
    local since="$3"  # ISO timestamp

    git log \
        --since="$since" \
        --format='{"sha":"%H","message":"%s","author":"%an","date":"%aI"}' \
        "origin/$branch"
}

process_pr() {
    local pr_json="$1"
    local pr_number=$(echo "$pr_json" | jq -r '.number')
    local updated_at=$(echo "$pr_json" | jq -r '.updatedAt')

    # Check if we've already processed this version
    local last_processed=$(get_last_processed "github-pr-$pr_number")
    if [[ "$updated_at" == "$last_processed" ]]; then
        return 0
    fi

    # Create review task
    create_task \
        --id "github-pr-$pr_number-$(date +%s)" \
        --title "Review PR #$pr_number" \
        --category "review" \
        --source "github-pr" \
        --source-ref "$pr_number" \
        --pipeline "pr-review-pipeline"

    # Create test task
    create_task \
        --id "github-pr-test-$pr_number-$(date +%s)" \
        --title "Test PR #$pr_number" \
        --category "test" \
        --source "github-pr" \
        --source-ref "$pr_number"

    mark_processed "github-pr-$pr_number" "$updated_at"
}
```

#### External Trigger Interface

```bash
#!/bin/bash
# /usr/local/lib/aquarco/pollers/external-triggers.sh
# Processes trigger files dropped into a directory

WATCH_DIR="/var/lib/aquarco/triggers"
PROCESSED_DIR="/var/lib/aquarco/triggers/processed"

process_trigger_files() {
    for trigger_file in "$WATCH_DIR"/*.yaml "$WATCH_DIR"/*.json; do
        [[ -f "$trigger_file" ]] || continue

        local filename=$(basename "$trigger_file")
        local task_id="external-${filename%.*}-$(date +%s)"

        # Parse trigger file
        if [[ "$trigger_file" == *.yaml ]]; then
            local category=$(yq eval '.category' "$trigger_file")
            local title=$(yq eval '.title' "$trigger_file")
            local context=$(yq eval '.context' "$trigger_file")
        else
            local category=$(jq -r '.category' "$trigger_file")
            local title=$(jq -r '.title' "$trigger_file")
            local context=$(jq -r '.context' "$trigger_file")
        fi

        # Create task
        create_task \
            --id "$task_id" \
            --title "$title" \
            --category "$category" \
            --source "external" \
            --context "$context"

        # Move to processed
        mv "$trigger_file" "$PROCESSED_DIR/"
    done
}
```

### External Trigger File Format

```yaml
# /var/lib/aquarco/triggers/custom-task-001.yaml
category: implementation
title: "Implement caching layer for API"
priority: high
context: |
  We need to add Redis caching to the GraphQL API.

  Requirements:
  - Cache frequently accessed queries
  - TTL of 5 minutes for user data
  - TTL of 1 hour for static data
  - Invalidation on mutations

labels:
  - performance
  - backend

files:
  - api/src/resolvers/*.ts
  - api/src/cache/*.ts
```

---

## 4. Agent Context Isolation and Pipeline Flow

This section addresses the critical challenge of passing context between agents in a pipeline while maintaining isolation and reliability. We use BOTH structured contracts AND accumulated context.

### The Problem

When agents execute in sequence (analyze -> design -> implement -> test -> review), each agent needs context from previous stages. However:
- Agents are stateless — they cannot share memory
- Output can be noisy — not all output is useful for the next stage
- Context accumulates — later stages need earlier context
- Token limits exist — we cannot pass infinite context

### Solution: Two Complementary Approaches

#### Approach A: Structured Context Contracts

Each agent category defines an **output schema** in its YAML definition. The supervisor validates agent output against this schema before passing to the next stage. This ensures reliable, parseable data.

**Output Schemas by Category:**

```yaml
# analyze category output
outputSchema:
  type: object
  required: [issue_summary, affected_components, recommended_pipeline, estimated_complexity]
  properties:
    issue_summary: { type: string }
    affected_components: { type: array, items: { type: string } }
    recommended_pipeline: { type: string }
    estimated_complexity: { type: string, enum: [trivial, low, medium, high, epic] }
    files_to_modify: { type: array, items: { type: string } }
    risks: { type: array, items: { type: string } }

# design category output
outputSchema:
  type: object
  required: [design_summary, components_affected, implementation_steps, acceptance_criteria]
  properties:
    design_summary: { type: string }
    components_affected: { type: array, items: { type: string } }
    implementation_steps:
      type: array
      items:
        type: object
        properties:
          step: { type: integer }
          description: { type: string }
          files: { type: array, items: { type: string } }
    acceptance_criteria: { type: array, items: { type: string } }
    api_changes: { type: string, description: "ref:blobs/<hash>.json for large specs" }
    database_migrations: { type: array, items: { type: string } }

# implementation category output
outputSchema:
  type: object
  required: [summary, files_changed, test_status, commit_sha]
  properties:
    summary: { type: string }
    files_changed: { type: array, items: { type: string } }
    test_status: { type: string, enum: [passed, failed, skipped, not_run] }
    commit_sha: { type: string }
    generated_diff: { type: string, description: "ref:blobs/<hash>.patch for large diffs" }

# test category output
outputSchema:
  type: object
  required: [tests_written, tests_run, tests_passed, tests_failed]
  properties:
    tests_written: { type: array, items: { type: string } }
    tests_run: { type: integer }
    tests_passed: { type: integer }
    tests_failed: { type: integer }
    coverage_delta: { type: number }
    failures:
      type: array
      items:
        type: object
        properties:
          test: { type: string }
          error: { type: string }

# review category output
outputSchema:
  type: object
  required: [summary, findings, recommendation, severity]
  properties:
    summary: { type: string }
    findings:
      type: array
      items:
        type: object
        properties:
          file: { type: string }
          line: { type: integer }
          severity: { type: string, enum: [info, warning, error, critical] }
          message: { type: string }
    recommendation: { type: string, enum: [approve, request_changes, comment] }
    severity: { type: string, enum: [clean, minor_issues, major_issues, blocking] }

# docs category output
outputSchema:
  type: object
  required: [files_updated, summary]
  properties:
    files_updated: { type: array, items: { type: string } }
    summary: { type: string }
    sections_added: { type: array, items: { type: string } }
```

**Validation Flow:**

```
Agent completes execution
         |
         v
+---------------------+
| Parse agent output  |
| (extract JSON block)|
+---------------------+
         |
         v
+-------------------------+
| Validate against schema |
| defined in agent YAML   |
+-------------------------+
         |
    +----+----+
    |         |
  Valid    Invalid
    |         |
    v         v
+-------+  +------------------+
| Store |  | Retry agent (1x) |
| in DB |  | or FAIL stage    |
+-------+  +------------------+
```

#### Approach B: Accumulated Context Bundle

Each agent receives the **full chain**: original issue/PR + ALL previous stage outputs + current repo state summary. Context grows as it flows through pipeline stages.

**Context Bundle Structure:**

```json
{
  "task_id": "github-issue-42",
  "pipeline": "feature-pipeline",
  "current_stage": 3,

  "original_trigger": {
    "source": "github-issue",
    "ref": 42,
    "title": "Add dark mode support",
    "body": "User-submitted issue description...",
    "labels": ["feature", "ui"],
    "created_at": "2026-03-14T10:00:00Z"
  },

  "repo_state": {
    "branch": "feature/dark-mode-42",
    "base_branch": "main",
    "head_sha": "abc123",
    "files_changed_from_base": ["src/theme.ts", "src/components/App.tsx"]
  },

  "stages": [
    {
      "stage": 1,
      "category": "analyze",
      "agent": "analyze-agent",
      "completed_at": "2026-03-14T10:05:00Z",
      "output": {
        "issue_summary": "Add dark mode toggle to settings",
        "affected_components": ["ThemeProvider", "SettingsPage"],
        "estimated_complexity": "medium",
        "files_to_modify": ["src/theme.ts", "src/pages/Settings.tsx"]
      }
    },
    {
      "stage": 2,
      "category": "design",
      "agent": "design-agent",
      "completed_at": "2026-03-14T10:15:00Z",
      "output": {
        "design_summary": "Implement CSS variables-based theming",
        "implementation_steps": [
          {"step": 1, "description": "Add theme context", "files": ["src/ThemeContext.tsx"]},
          {"step": 2, "description": "Create dark theme variables", "files": ["src/styles/dark.css"]}
        ],
        "acceptance_criteria": ["Toggle works", "Persists to localStorage", "No flash on reload"]
      }
    }
  ],

  "current_instruction": "Implement the feature according to the design."
}
```

### How They Work Together

| Aspect | Structured Output | Accumulated Context |
|--------|-------------------|---------------------|
| Purpose | Reliable data extraction | Full picture/nuance |
| Enforcement | Required, validated | Optional, available |
| Storage | PostgreSQL `context` table | Built from DB at runtime |
| Token cost | Minimal (just schema fields) | Grows with pipeline depth |
| Use case | Programmatic decisions | Agent reasoning |

**Agent Behavior:**

1. Agent receives accumulated context bundle as input
2. Agent reads structured fields for reliable data (e.g., `stages[1].output.estimated_complexity`)
3. Agent references accumulated context for nuance (e.g., reading full issue description)
4. Agent produces output matching its `outputSchema`
5. Supervisor validates output, stores in DB, builds next context bundle

### Large Blob Handling

For large outputs (diffs, generated code, API specs), use file references:

```json
{
  "generated_diff": "ref:blobs/abc123.patch",
  "api_spec": "ref:blobs/def456.json"
}
```

The supervisor:
1. Detects `ref:blobs/*` values
2. Stores the actual content in `/var/lib/aquarco/blobs/`
3. Stores the reference in PostgreSQL
4. When building context bundle, either:
   - Includes file content if small enough
   - Keeps reference and tells agent how to read it

### Token Budget Strategy

Context is minimized but complete. The strategy prioritizes giving agents what they need without bloat:

1. **Original issue**: ALWAYS full (this is what we're solving)
2. **All intermediate stages (0 to N-2)**: Summaries only (structured output fields serve as implicit summaries — no separate Claude call needed)
3. **Previous stage (N-1)**: Full output (most relevant context for current work)
4. **Large blobs**: Always referenced, never inlined

The supervisor generates summaries by extracting structured output fields from each stage. Since agents must produce validated structured output (e.g., `issue_summary`, `design_summary`, `files_changed`), these fields serve as natural summaries without requiring a separate summarization pass.

```python
def build_context_bundle(task_id, current_stage, max_tokens=50000):
    stages = get_all_stages(task_id)

    # Always include: original trigger + current repo state
    base_context = {
        "original_trigger": get_trigger(task_id),  # ALWAYS full
        "repo_state": get_repo_state(task_id),
        "stages": []
    }

    for stage in stages:
        if stage.num == current_stage - 1:
            # Previous stage: full output
            base_context["stages"].append({
                "stage": stage.num,
                "category": stage.category,
                "output": stage.structured_output,  # Full structured output
                "raw_output": stage.raw_output  # Include raw for nuance
            })
        else:
            # Earlier stages: summaries only (structured fields ARE the summary)
            base_context["stages"].append({
                "stage": stage.num,
                "category": stage.category,
                "output": stage.structured_output  # Structured fields only
            })

    return base_context
```

### Task Pipeline Architecture

Tasks flow through pipelines that define the sequence of agent categories needed to complete work.

### Pipeline Flow Diagram

```
+-----------------------------------------------------------------------------+
|                    TASK PIPELINE: feature-pipeline                           |
+-----------------------------------------------------------------------------+
|                                                                             |
|  GitHub Issue                                                               |
|  (label: feature)                                                           |
|        |                                                                    |
|        v                                                                    |
|  +-------------------+                                                      |
|  |  1. ANALYZE       |  analyze-agent                                       |
|  |  -----------------|  - Reads issue description                           |
|  |  Input: Issue     |  - Analyzes codebase impact                          |
|  |  Output: Analysis |  - Produces: analysis-complete event                 |
|  |  (validated JSON) |  - Stores structured output in PostgreSQL            |
|  +--------+----------+                                                      |
|           |                                                                 |
|           v                                                                 |
|  +-------------------+                                                      |
|  |  2. DESIGN        |  design-agent                                        |
|  |  -----------------|  - Receives: issue + analysis output                 |
|  |  Input: Context   |  - Creates technical design                          |
|  |  Bundle           |  - Produces: design-complete event                   |
|  |  Output: Design   |  - Stores structured output in PostgreSQL            |
|  +--------+----------+                                                      |
|           |                                                                 |
|           v                                                                 |
|  +-------------------+                                                      |
|  |  3. IMPLEMENT     |  implementation-agent                                |
|  |  -----------------|  - Receives: issue + analysis + design               |
|  |  Input: Context   |  - Writes code changes                               |
|  |  Bundle           |  - Produces: implementation-complete event           |
|  |  Output: Code +   |  - Commits changes, stores diff ref in PostgreSQL    |
|  |  Commit SHA       |                                                      |
|  +--------+----------+                                                      |
|           |                                                                 |
|           v                                                                 |
|  +-------------------+                                                      |
|  |  4. TEST          |  test-agent                                          |
|  |  -----------------|  - Receives: full context chain                      |
|  |  Input: Context   |  - Writes/runs tests                                 |
|  |  Bundle           |  - Produces: test-complete event                     |
|  |  Output: Results  |  - Stores test results in PostgreSQL                 |
|  +--------+----------+                                                      |
|           |                                                                 |
|           v                                                                 |
|  +-------------------+                                                      |
|  |  5. DOCS          |  docs-agent (optional)                               |
|  |  -----------------|  - Receives: full context chain                      |
|  |  Input: Context   |  - Updates documentation                             |
|  |  Bundle           |  - Produces: docs-complete event                     |
|  |  Output: Files    |  - Commits doc changes                               |
|  +--------+----------+                                                      |
|           |                                                                 |
|           v                                                                 |
|  +-------------------+                                                      |
|  |  6. REVIEW        |  review-agent                                        |
|  |  -----------------|  - Receives: full context chain                      |
|  |  Input: Context   |  - Reviews all changes against design                |
|  |  Bundle + PR      |  - Posts review comments                             |
|  |  Output: Review   |  - Approves or requests changes                      |
|  +--------+----------+                                                      |
|           |                                                                 |
|           v                                                                 |
|  +-------------------+                                                      |
|  |  PIPELINE         |                                                      |
|  |  COMPLETE         |  -> Close issue, merge PR (if approved)              |
|  +-------------------+                                                      |
|                                                                             |
+-----------------------------------------------------------------------------+
```

### PR Review Pipeline

```
+-----------------------------------------------------------------------------+
|                    TASK PIPELINE: pr-review-pipeline                         |
+-----------------------------------------------------------------------------+
|                                                                             |
|  Pull Request Opened                                                        |
|        |                                                                    |
|        +----------------------------+-------------------------------+       |
|        |                            |                               |       |
|        v                            v                               |       |
|  +---------------+           +---------------+                      |       |
|  |  REVIEW       |           |  TEST         |   (parallel)         |       |
|  |  -------------|           |  -------------|                      |       |
|  |  review-agent |           |  test-agent   |                      |       |
|  +-------+-------+           +-------+-------+                      |       |
|          |                           |                              |       |
|          +--------------+------------+                              |       |
|                         |                                           |       |
|                         v                                           |       |
|               +-------------------+                                 |       |
|               |  AGGREGATE        |                                 |       |
|               |  RESULTS          |  Post combined review + test    |       |
|               +-------------------+                                 |       |
|                                                                             |
+-----------------------------------------------------------------------------+
```

### Task State Machine

```
                    +-----------+
                    |  CREATED  |
                    +-----+-----+
                          |
                          v
                    +-----------+
         +--------->|  PENDING  |<------------+
         |          +-----+-----+             |
         |                |                   |
         |                v                   |
         |          +-----------+             |
         |          |  QUEUED   |             |
         |          +-----+-----+             |
         |                |                   |
         |                v                   |
         |          +-----------+             |
         |          | EXECUTING |-------------+
         |          +-----+-----+             |
         |                |                   |
         |    +-----------+-----------+       |
         |    |           |           |       |
         |    v           v           v       |
     +-----------+  +-----------+  +-------+  |
     | COMPLETED |  |  FAILED   |  |TIMEOUT|--+
     +-----------+  +-----+-----+  +-------+
                          |          (retry up to N times)
                          v
                    +-----------+
                    |  BLOCKED  |  (needs human intervention)
                    +-----------+
```

### Pipeline Execution Engine

```bash
#!/bin/bash
# /usr/local/lib/aquarco/pipeline-executor.sh

execute_pipeline() {
    local pipeline_name="$1"
    local task_id="$2"
    local context="$3"

    # Load pipeline definition
    local pipeline=$(get_pipeline_config "$pipeline_name")
    local stages=$(echo "$pipeline" | jq -r '.stages[]')

    local stage_num=0
    local previous_output=""

    for stage in $stages; do
        stage_num=$((stage_num + 1))
        local category=$(echo "$stage" | jq -r '.category')
        local required=$(echo "$stage" | jq -r '.required')
        local conditions=$(echo "$stage" | jq -r '.conditions // []')

        # Check conditions
        if ! check_conditions "$conditions" "$previous_output"; then
            log "Skipping stage $stage_num ($category) - conditions not met"
            continue
        fi

        # Select agent for category
        local agent=$(select_agent_for_category "$category")
        if [[ -z "$agent" ]]; then
            if [[ "$required" == "true" ]]; then
                fail_task "$task_id" "No agent available for required category: $category"
                return 1
            fi
            continue
        fi

        # Execute agent with retry
        log "Executing stage $stage_num: $category (agent: $agent)"
        update_task_status "$task_id" "executing" "Stage $stage_num: $category"

        local output
        local retries=0
        local max_retries=2

        while [[ $retries -le $max_retries ]]; do
            # Build accumulated context bundle from PostgreSQL
            local context_bundle=$(build_context_bundle "$task_id" "$stage_num")

            if output=$(execute_agent "$agent" "$task_id" "$context_bundle"); then
                # Validate output against agent's schema
                local agent_def=$(get_agent_definition "$agent")
                local schema=$(echo "$agent_def" | jq '.spec.outputSchema')

                if validate_output "$output" "$schema"; then
                    # Store validated output in PostgreSQL
                    store_stage_context "$task_id" "$stage_num" "$category" "$output"
                    break
                else
                    log "Output validation failed for stage $stage_num"
                    retries=$((retries + 1))
                fi
            else
                log "Agent execution failed for stage $stage_num"
                retries=$((retries + 1))
            fi

            if [[ $retries -gt $max_retries ]]; then
                if [[ "$required" == "true" ]]; then
                    # Checkpoint current state and mark BLOCKED
                    checkpoint_pipeline "$task_id" "$stage_num"
                    block_task "$task_id" "Stage $category failed after $max_retries retries"
                    return 1
                fi
                log "Optional stage $category failed, continuing..."
                break
            fi
        done

        previous_output="$output"
    done

    complete_task "$task_id"
}

select_agent_for_category() {
    local category="$1"

    # Get agents for category, sorted by priority
    local agents=$(jq -r \
        ".category_index[\"$category\"][]" \
        /var/lib/aquarco/agent-registry.json)

    for agent in $agents; do
        # Check if agent is available (not at max concurrent)
        if agent_is_available "$agent"; then
            echo "$agent"
            return 0
        fi
    done

    return 1
}

execute_agent() {
    local agent_name="$1"
    local task_id="$2"
    local context_bundle="$3"

    # Load agent definition
    local agent_def=$(get_agent_definition "$agent_name")
    local prompt_file=$(echo "$agent_def" | jq -r '.spec.promptFile')
    local timeout=$(echo "$agent_def" | jq -r '.spec.resources.timeoutMinutes // 30')
    local max_tokens=$(echo "$agent_def" | jq -r '.spec.resources.maxTokens // 100000')

    # Build context for agent
    local context_file=$(mktemp)
    cat > "$context_file" << EOF
# Task Context

## Task ID
$task_id

## Context Bundle
$context_bundle

## Instructions
Execute your role as defined in your system prompt.
Output your results as a JSON block matching your output schema.
Wrap the JSON in \`\`\`json ... \`\`\` markers.
EOF

    # Increment active instances
    increment_agent_instances "$agent_name"

    # Execute Claude Code with agent prompt
    local output
    timeout "${timeout}m" claude \
        --agent "/home/agent/aquarco/agents/prompts/$prompt_file" \
        --max-tokens "$max_tokens" \
        --print \
        < "$context_file" \
        > "$output" 2>&1

    local exit_code=$?

    # Decrement active instances
    decrement_agent_instances "$agent_name"

    rm -f "$context_file"

    if [[ $exit_code -ne 0 ]]; then
        log "Agent $agent_name failed with exit code $exit_code"
        return 1
    fi

    cat "$output"
}
```

---

## 5. Directory Structure

### Host Machine (Development)

The host machine contains only the aquarco repo (the system). Target repos are cloned inside the VM at runtime.

```
aquarco/                                    # Aquarco repo (the system)
├── agents/
│   ├── definitions/                        # Agent definition YAML files
│   │   ├── analyze-agent.yaml
│   │   ├── design-agent.yaml
│   │   ├── implementation-agent.yaml
│   │   ├── review-agent.yaml
│   │   ├── test-agent.yaml
│   │   └── docs-agent.yaml
│   ├── prompts/                            # System prompts for agents
│   │   ├── analyze-agent.md
│   │   ├── design-agent.md
│   │   ├── implementation-agent.md
│   │   ├── review-agent.md
│   │   ├── test-agent.md
│   │   └── docs-agent.md
│   └── schemas/                            # JSON Schema for validation
│       ├── agent-definition-v1.json
│       └── repo-config-v1.json             # Schema for .aquarco.yaml
├── supervisor/
│   ├── config/
│   │   └── supervisor.yaml                 # Supervisor configuration
│   ├── pollers/                            # Poller implementations
│   │   ├── github-tasks.sh
│   │   ├── github-source.sh
│   │   └── external-triggers.sh
│   ├── lib/                                # Shared libraries
│   │   ├── task-queue.sh
│   │   ├── agent-registry.sh
│   │   ├── context-builder.sh
│   │   ├── repo-manager.sh                 # Repo clone/update management
│   │   └── pipeline-executor.sh
│   └── scripts/
│       └── supervisor.sh                   # Main supervisor script
├── web-ui/                                 # Monitoring Web UI (Next.js)
│   ├── src/
│   │   ├── app/                            # Next.js App Router
│   │   ├── components/                     # React + MUI components
│   │   └── lib/                            # API clients, utilities
│   ├── package.json
│   └── ...
├── tasks/                                  # Task files (generated by agents)
│   ├── TASK-001-*.md
│   ├── TASK-002-*.md
│   └── ...
├── prd.json                                # Project requirements document
├── CLAUDE.md                               # Project instructions
└── vagrant/                                # VM provisioning
    ├── Vagrantfile
    ├── ansible/
    │   └── roles/
    │       └── aquarco-supervisor/
    └── cloud-init.yaml
```

### VM Internal Structure

The VM contains both the aquarco repo (system) and target repos (work).

```
/home/agent/
├── aquarco/                                # Aquarco repo (the system)
│   ├── agents/
│   │   ├── definitions/                    # Agent definitions
│   │   └── prompts/                        # Agent prompts
│   ├── supervisor/
│   ├── web-ui/                             # Built web UI
│   ├── tasks/
│   └── ...
│
├── repos/                                  # Target repos (cloned by supervisor)
│   ├── my-saas-app/                        # Each repo in its own directory
│   │   ├── .aquarco.yaml                    # Optional per-repo config
│   │   └── ...                             # Actual repo contents
│   ├── internal-api/
│   │   └── ...
│   └── mobile-backend/
│       └── ...
│
├── .anthropic-key                          # API key
├── .github-token                           # GitHub PAT
├── .postgres-password                      # PostgreSQL password
└── .gitconfig                              # Git configuration

/var/lib/aquarco/
├── agent-registry.json                     # Discovered agents
├── blobs/                                  # Large context blobs
│   ├── abc123.patch
│   ├── def456.json
│   └── ...
├── triggers/                               # External trigger drop directory
│   └── processed/                          # Processed triggers
├── state/                                  # Persistent state
│   ├── last-poll-github-tasks.txt
│   ├── last-poll-github-source.txt
│   └── checkpoints/                        # Pipeline checkpoints
│       └── <task-id>/
│           └── stage-N-checkpoint.json
└── cache/                                  # Temporary files

/var/log/aquarco/
├── supervisor.log                          # Main supervisor log
├── agents/                                 # Per-agent logs
│   ├── analyze-agent/
│   │   └── 2026-03-14-task-001.log
│   ├── implementation-agent/
│   │   └── ...
│   └── ...
├── pollers/                                # Poller logs
│   ├── github-tasks.log
│   ├── github-source.log
│   └── external-triggers.log
└── pipelines/                              # Pipeline execution logs
    └── <task-id>.log

/etc/aquarco/
├── supervisor.yaml                         # Supervisor config (symlink to repo)
└── secrets.env                             # Environment secrets

/usr/local/bin/
├── aquarco-supervisor                       # Main supervisor binary/script
├── aquarco-discover-agents                  # Agent discovery script
├── aquarco-create-task                      # Task creation utility
└── aquarco-status                           # Status reporting utility

/usr/local/lib/aquarco/
├── pollers/                                # Poller scripts
├── pipeline-executor.sh                    # Pipeline engine
├── context-builder.sh                      # Builds accumulated context
├── schema-validator.sh                     # Validates agent output
└── agent-executor.sh                       # Agent execution wrapper
```

---

## 6. Configuration Files

### Main Supervisor Configuration

```yaml
# /etc/aquarco/supervisor.yaml (or supervisor/config/supervisor.yaml)
apiVersion: aquarco.supervisor/v1
kind: SupervisorConfig

metadata:
  name: aquarco-supervisor
  version: "1.0.0"

spec:
  workdir: /home/agent/aquarco
  agentsDir: /home/agent/aquarco/agents/definitions
  promptsDir: /home/agent/aquarco/agents/prompts

  # Supervisor uses direct connections (runs one pipeline at a time, 3-4 connections max)
  # No PgBouncer needed — Web UI uses its own pooled connection (Prisma built-in pool)
  taskQueue:
    driver: postgresql
    host: localhost
    port: 5432
    database: aquarco
    user: aquarco
    passwordFile: /home/agent/.postgres-password
    retentionDays: 30

  # Config hot-reload: Web UI writes validated YAML, sends SIGHUP to supervisor PID
  # Supervisor catches SIGHUP, re-reads config
  configReload:
    signal: SIGHUP
    validateBeforeWrite: true

  blobStorage:
    path: /var/lib/aquarco/blobs
    maxSizeMB: 50

  logging:
    level: info
    file: /var/log/aquarco/supervisor.log
    maxSizeMB: 100
    maxFiles: 5
    format: json

  globalLimits:
    maxConcurrentAgents: 3
    maxTokensPerHour: 1000000
    cooldownBetweenTasksSeconds: 5
    maxRetries: 3
    retryDelaySeconds: 60

  # Repository registration
  # Default auth: GitHub PAT (loaded from secrets.githubTokenFile)
  # Clone URL: https://<token>@github.com/owner/repo.git
  repositories:
    - name: my-saas-app
      url: https://github.com/owner/my-saas-app.git
      branch: main
      cloneDir: /home/agent/repos/my-saas-app
      pollers: [github-tasks, github-source]
      # auth: pat  # default, uses PAT from secrets.githubTokenFile
    - name: internal-api
      url: https://github.com/owner/internal-api.git
      branch: main
      cloneDir: /home/agent/repos/internal-api
      pollers: [github-tasks]
    - name: legacy-system
      url: git@github.com:owner/legacy-system.git
      branch: main
      cloneDir: /home/agent/repos/legacy-system
      pollers: [github-tasks]
      auth: deploy-key                           # optional override: use deploy key instead of PAT
      deployKeyFile: ~/.ssh/deploy_legacy_system # path to repo-specific deploy key

  # Per-repo config auto-reload
  repoConfig:
    autoReload: true           # Re-read .aquarco.yaml after every git pull
    configFile: .aquarco.yaml   # Config file name in target repo root

  pollers:
    - name: github-tasks
      type: github-tasks
      enabled: true
      intervalSeconds: 60
      config:
        repository: "owner/repo"
        sources:
          - type: issues
            labels: [agent-task]
            states: [open]
        categorization:
          defaultCategory: analyze
          labelMapping:
            bug: implementation
            feature: analyze
            docs: docs
            test: test

    - name: github-source
      type: github-source
      enabled: true
      intervalSeconds: 30
      config:
        repository: "owner/repo"
        watch:
          - type: pull_request
            states: [open]
        triggers:
          on_pr_opened: [review, test]
          on_pr_updated: [review]

    - name: external-triggers
      type: file-watch
      enabled: true
      intervalSeconds: 10
      config:
        watchDir: /var/lib/aquarco/triggers
        processedDir: /var/lib/aquarco/triggers/processed

  pipelines:
    - name: feature-pipeline
      trigger:
        labels: [feature, enhancement]
      stages:
        - { category: analyze, required: true }
        - { category: design, required: true, conditions: ["analysis.complexity >= medium"] }
        - { category: implementation, required: true }
        - { category: test, required: true }
        - { category: docs, required: false }
        - { category: review, required: true }

    - name: bugfix-pipeline
      trigger:
        labels: [bug]
      stages:
        - { category: analyze, required: true }
        - { category: implementation, required: true }
        - { category: test, required: true }
        - { category: review, required: true }

    - name: pr-review-pipeline
      trigger:
        events: [pr_opened, pr_updated]
      stages:
        - { category: review, required: true }
        - { category: test, required: true }

  health:
    enabled: true
    reportIntervalMinutes: 30
    reportDestination: github-issue
    issueNumber: 1

  secrets:
    githubTokenFile: /home/agent/.github-token
    anthropicKeyFile: /home/agent/.anthropic-key
```

### Agent Definition Schema (JSON Schema)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://aquarco.local/schemas/agent-definition-v1.json",
  "title": "Agent Definition",
  "description": "Schema for Aquarco agent definition files",
  "type": "object",
  "required": ["apiVersion", "kind", "metadata", "spec"],
  "properties": {
    "apiVersion": {
      "type": "string",
      "const": "aquarco.agents/v1"
    },
    "kind": {
      "type": "string",
      "const": "AgentDefinition"
    },
    "metadata": {
      "type": "object",
      "required": ["name", "version", "description"],
      "properties": {
        "name": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9-]*$"
        },
        "version": {
          "type": "string",
          "pattern": "^\\d+\\.\\d+\\.\\d+$"
        },
        "description": {
          "type": "string",
          "minLength": 10
        },
        "labels": {
          "type": "object",
          "additionalProperties": { "type": "string" }
        },
        "annotations": {
          "type": "object",
          "additionalProperties": { "type": "string" }
        }
      }
    },
    "spec": {
      "type": "object",
      "required": ["categories", "promptFile"],
      "properties": {
        "categories": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": ["review", "implementation", "test", "design", "docs", "analyze"]
          },
          "minItems": 1
        },
        "priority": {
          "type": "integer",
          "minimum": 1,
          "maximum": 100,
          "default": 50
        },
        "promptFile": {
          "type": "string",
          "pattern": "^[a-z0-9-]+\\.md$"
        },
        "tools": {
          "type": "object",
          "properties": {
            "allowed": {
              "type": "array",
              "items": { "type": "string" }
            },
            "denied": {
              "type": "array",
              "items": { "type": "string" }
            }
          }
        },
        "resources": {
          "type": "object",
          "properties": {
            "maxTokens": { "type": "integer", "default": 100000 },
            "timeoutMinutes": { "type": "integer", "default": 30 },
            "maxConcurrent": { "type": "integer", "default": 1 },
            "memoryMB": { "type": "integer" }
          }
        },
        "capabilities": {
          "type": "object",
          "properties": {
            "canPush": { "type": "boolean", "default": true },
            "canCreatePR": { "type": "boolean", "default": true },
            "canCommentOnPR": { "type": "boolean", "default": true },
            "canApprove": { "type": "boolean", "default": false },
            "canMerge": { "type": "boolean", "default": false },
            "canAccessDocker": { "type": "boolean", "default": false },
            "canAccessK8s": { "type": "boolean", "default": false },
            "canCreateIssues": { "type": "boolean", "default": true },
            "canCloseIssues": { "type": "boolean", "default": false }
          }
        },
        "output": {
          "type": "object",
          "properties": {
            "format": {
              "type": "string",
              "enum": ["task-file", "github-pr-comment", "commit", "issue", "none"]
            },
            "mustInclude": {
              "type": "array",
              "items": { "type": "string" }
            }
          }
        },
        "outputSchema": {
          "type": "object",
          "description": "JSON Schema for validating agent structured output"
        },
        "triggers": {
          "type": "object",
          "properties": {
            "produces": {
              "type": "array",
              "items": { "type": "string" }
            },
            "consumes": {
              "type": "array",
              "items": { "type": "string" }
            }
          }
        },
        "conditions": {
          "type": "object",
          "properties": {
            "filePatterns": {
              "type": "array",
              "items": { "type": "string" }
            },
            "branchPatterns": {
              "type": "array",
              "items": { "type": "string" }
            },
            "labels": {
              "type": "array",
              "items": { "type": "string" }
            }
          }
        },
        "healthCheck": {
          "type": "object",
          "properties": {
            "enabled": { "type": "boolean", "default": true },
            "intervalSeconds": { "type": "integer", "default": 300 }
          }
        }
      }
    }
  }
}
```

### Task Queue Schema (PostgreSQL)

```sql
-- PostgreSQL schema for Aquarco task queue and context storage

-- Registered repositories (populated from supervisor.yaml on startup/reload)
CREATE TABLE repositories (
    name TEXT PRIMARY KEY,              -- Matches repositories[].name in supervisor.yaml
    url TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT 'main',
    clone_dir TEXT NOT NULL,
    pollers TEXT[] NOT NULL DEFAULT '{}',
    last_cloned_at TIMESTAMPTZ,
    last_pulled_at TIMESTAMPTZ,
    clone_status TEXT DEFAULT 'pending',
    head_sha TEXT,

    CONSTRAINT valid_clone_status CHECK (clone_status IN ('pending', 'cloning', 'ready', 'error'))
);

-- Tasks table
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER DEFAULT 50,
    source TEXT NOT NULL,
    source_ref TEXT,
    pipeline TEXT,
    repository TEXT NOT NULL REFERENCES repositories(name),  -- Must match registered repo
    initial_context JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    assigned_agent TEXT,
    current_stage INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    error_message TEXT,

    CONSTRAINT valid_status CHECK (status IN ('pending', 'queued', 'executing', 'completed', 'failed', 'timeout', 'blocked'))
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_category ON tasks(category);
CREATE INDEX idx_tasks_pipeline ON tasks(pipeline);
CREATE INDEX idx_tasks_repository ON tasks(repository);  -- For filtering by repo
CREATE INDEX idx_tasks_created ON tasks(created_at);
CREATE INDEX idx_tasks_status_priority ON tasks(status, priority) WHERE status IN ('pending', 'queued');

-- Stages table (stores structured output per pipeline stage)
CREATE TABLE stages (
    id SERIAL PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stage_number INTEGER NOT NULL,
    category TEXT NOT NULL,
    agent TEXT,
    agent_version TEXT,  -- Track which agent version produced this output (for rollback/debugging)
    status TEXT DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    -- Structured output (validated against agent's outputSchema)
    structured_output JSONB,

    -- Raw agent output (for debugging)
    raw_output TEXT,

    -- Token usage metrics (observability only — Claude Code Max subscription, not API billing)
    -- Used for: understanding agent efficiency, comparing token usage, spotting bloated prompts
    tokens_input INTEGER,
    tokens_output INTEGER,

    error_message TEXT,
    retry_count INTEGER DEFAULT 0,

    UNIQUE(task_id, stage_number),
    CONSTRAINT valid_stage_status CHECK (status IN ('pending', 'executing', 'completed', 'failed', 'skipped'))
);

CREATE INDEX idx_stages_task ON stages(task_id);
CREATE INDEX idx_stages_status ON stages(status);

-- Context table (for large structured data, linked via task/stage)
CREATE TABLE context (
    id SERIAL PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stage_number INTEGER,
    key TEXT NOT NULL,
    value_type TEXT NOT NULL,  -- 'json', 'text', 'file_ref'
    value_json JSONB,
    value_text TEXT,
    value_file_ref TEXT,  -- Path like 'blobs/abc123.patch'
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT valid_value_type CHECK (value_type IN ('json', 'text', 'file_ref'))
);

CREATE INDEX idx_context_task ON context(task_id);
CREATE INDEX idx_context_task_stage ON context(task_id, stage_number);

-- Poll state (tracks last processed state per poller)
CREATE TABLE poll_state (
    poller_name TEXT PRIMARY KEY,
    last_poll_at TIMESTAMPTZ,
    last_successful_at TIMESTAMPTZ,
    cursor TEXT,
    state_data JSONB
);

-- Agent instances (tracks active agent executions)
CREATE TABLE agent_instances (
    agent_name TEXT PRIMARY KEY,
    active_count INTEGER DEFAULT 0,
    total_executions INTEGER DEFAULT 0,
    total_tokens_used BIGINT DEFAULT 0,
    last_execution_at TIMESTAMPTZ
);

-- Pipeline checkpoints (for resume after failure)
CREATE TABLE pipeline_checkpoints (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    last_completed_stage INTEGER NOT NULL,
    checkpoint_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- Function to get accumulated context for a task
CREATE OR REPLACE FUNCTION get_task_context(p_task_id TEXT)
RETURNS JSONB AS $$
DECLARE
    result JSONB;
BEGIN
    SELECT jsonb_build_object(
        'task_id', t.id,
        'pipeline', t.pipeline,
        'current_stage', t.current_stage,
        'initial_context', t.initial_context,
        'stages', COALESCE(
            (SELECT jsonb_agg(
                jsonb_build_object(
                    'stage', s.stage_number,
                    'category', s.category,
                    'agent', s.agent,
                    'status', s.status,
                    'completed_at', s.completed_at,
                    'output', s.structured_output
                ) ORDER BY s.stage_number
            )
            FROM stages s
            WHERE s.task_id = t.id AND s.status = 'completed'),
            '[]'::jsonb
        )
    ) INTO result
    FROM tasks t
    WHERE t.id = p_task_id;

    RETURN result;
END;
$$ LANGUAGE plpgsql;
```

---

## 7. Extension Points

### Adding a New Agent

1. Create definition file:
```yaml
# agents/definitions/my-new-agent.yaml
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: my-new-agent
  version: "1.0.0"
  description: "Description of what this agent does"
spec:
  categories:
    - implementation  # Or any existing category
  promptFile: my-new-agent.md
  outputSchema:
    type: object
    required: [summary, result]
    properties:
      summary: { type: string }
      result: { type: string }
  # ... rest of config
```

2. Create prompt file:
```markdown
# agents/prompts/my-new-agent.md
You are the My New Agent...

## Output Format
You MUST output a JSON block with your results:
\`\`\`json
{
  "summary": "...",
  "result": "..."
}
\`\`\`
```

3. Agent is automatically discovered on next poll cycle (or manually trigger: `aquarco-discover-agents`)

### Adding a New Task Category

1. Add category to allowed enum in schema
2. Update agents to handle new category
3. Add pipeline stages using new category

```yaml
# In supervisor.yaml, add to pipelines:
pipelines:
  - name: security-audit-pipeline
    trigger:
      labels: [security]
    stages:
      - { category: security-scan, required: true }  # New category
      - { category: review, required: true }
```

### Adding a New Poller

1. Create poller script:
```bash
# supervisor/pollers/my-new-poller.sh
#!/bin/bash
poll_my_source() {
    # Implementation
}
process_my_event() {
    local event="$1"
    create_task ...
}
```

2. Add to supervisor config:
```yaml
pollers:
  - name: my-new-poller
    type: custom
    enabled: true
    intervalSeconds: 120
    config:
      script: /usr/local/lib/aquarco/pollers/my-new-poller.sh
      # Custom config
```

### Adding a New Pipeline

```yaml
# In supervisor.yaml
pipelines:
  - name: security-response-pipeline
    trigger:
      labels: [security, vulnerability]
    stages:
      - category: analyze
        required: true
        timeout: 10  # Quick analysis
      - category: implementation
        required: true
        conditions:
          - "analysis.severity >= high"
      - category: test
        required: true
      - category: review
        required: true
        config:
          requireApproval: true
```

### Adding External Integrations

Create a trigger file programmatically:

```bash
# From any external system, create a YAML file:
cat > /var/lib/aquarco/triggers/slack-request-$(date +%s).yaml << 'EOF'
category: implementation
title: "Request from Slack: Add dark mode"
priority: medium
context: |
  User @john requested via Slack:
  "Can we add dark mode to the dashboard?"

  Channel: #feature-requests
  Timestamp: 2026-03-14T10:30:00Z
labels:
  - from-slack
  - ui
EOF
```

### Custom Agent Capabilities

Define custom capabilities:
```yaml
spec:
  capabilities:
    custom:
      canAccessSlack: true
      canSendEmails: false
      canAccessDatabase: read-only
```

Handle in executor:
```bash
# In agent-executor.sh
apply_custom_capabilities() {
    local agent_def="$1"
    local capabilities=$(echo "$agent_def" | jq -r '.spec.capabilities.custom // {}')

    if [[ $(echo "$capabilities" | jq -r '.canAccessSlack') == "true" ]]; then
        export SLACK_TOKEN="$(cat /home/agent/.slack-token)"
    fi

    # ... etc
}
```

---

## 8. Web UI for Monitoring and Intervention

A full-stack web application for monitoring the agent system and intervening when needed. Built with the same stack as the main project (dogfooding).

### Tech Stack

- **Framework**: Next.js 14+ (App Router)
- **UI Library**: React + MUI (Material UI)
- **State Management**: React Query for server state
- **Real-time**: WebSocket for live updates
- **Database**: Same PostgreSQL instance as task queue

### Access Model

The Web UI runs inside the VM and is accessed from the host via VirtualBox NAT port forwarding:

```
Host browser (localhost:8080)
    -> VirtualBox NAT
    -> VM:8080 (Next.js server)
    -> PostgreSQL (localhost:5432)
```

**Security**: No authentication required. This is a local dev machine environment — the UI is only accessible from the host machine via VirtualBox NAT. No internet exposure, no auth complexity.

### Features

#### Dashboard (`/`)
- Pipeline status overview (running, completed, failed, blocked)
- Agent activity feed (recent executions)
- Task queue depth (pending, queued, executing)
- Resource usage graphs (tokens/hour, concurrent agents)
- Quick actions (pause all, resume, emergency stop)

#### Task Explorer (`/tasks`)
- Browse all tasks with filtering:
  - By status (pending, executing, completed, failed, blocked)
  - By category (analyze, design, implementation, test, review, docs)
  - By pipeline (feature, bugfix, pr-review)
  - **By repository** (my-saas-app, internal-api, etc.)
  - By date range
- **Tasks grouped/filterable by repository**
- Drill into task details:
  - Original trigger (issue body, PR description)
  - Pipeline progress visualization
  - Per-stage timing and status
  - Full context chain view

#### Context Inspector (`/tasks/[id]/context`)
- For any task, view the full context chain
- See what each agent received as input
- See what each agent produced as output (structured + raw)
- Diff view between stages
- Blob viewer for large files (diffs, specs)

#### Agent Registry (`/agents`)
- View all discovered agents
- Agent status (available, busy, disabled)
- Active instances count
- Total executions and tokens used
- Enable/disable agents
- View agent definition and prompt

#### Repository Management (`/repos`)
- **Display registered repositories with status:**
  - Last polled timestamp
  - Clone status (pending, cloning, ready, error)
  - Current branch and HEAD SHA
- **Show per-repo config overrides** (from `.aquarco.yaml`):
  - Pipeline stage skips
  - Agent extra_context
  - Protected paths
- **Add/remove target repos via the UI** — Updates `supervisor.yaml` repositories section, triggers SIGHUP to reload
- Force re-clone or pull operations
- View repo clone logs

#### Pipeline Monitor (`/pipelines`)
- Visualize pipeline stages as a flow diagram
- See which stage is currently active
- Stage completion status (checkmarks, X marks)
- Estimated time remaining
- Pipeline throughput metrics

#### Configuration (`/config`)
- View and edit `supervisor.yaml` live
- Enable/disable pollers
- Adjust polling intervals
- Toggle agents on/off
- Changes take effect after supervisor reload

#### Intervention (`/intervention`)
- **Retry failed tasks**: Re-queue a failed task from its checkpoint
- **Unblock BLOCKED tasks**: Mark as resolved, continue pipeline
- **Cancel running agents**: Force-stop an agent execution
- **Manually trigger pipelines**: Create ad-hoc tasks
- **Manual task creation**: Submit custom tasks via form

#### Logs (`/logs`)
- Real-time log streaming from supervisor
- Filter by component (supervisor, agents, pollers)
- Search within logs
- Download log files

### Architecture

```
+------------------------------------------------------------------+
|                         Web UI (Next.js)                          |
+------------------------------------------------------------------+
|                                                                  |
|  +---------------------+  +---------------------+                |
|  |   App Router        |  |   API Routes        |                |
|  |   (React + MUI)     |  |   (/api/*)          |                |
|  +----------+----------+  +----------+----------+                |
|             |                        |                           |
|             v                        v                           |
|  +---------------------+  +---------------------+                |
|  |   React Query       |  |   PostgreSQL Client |                |
|  |   (client state)    |  |   (pg / Prisma)     |                |
|  +----------+----------+  +----------+----------+                |
|             |                        |                           |
|             +------------------------+                           |
|                        |                                         |
|                        v                                         |
|             +---------------------+                              |
|             |   WebSocket Server  |                              |
|             |   (real-time logs,  |                              |
|             |    status updates)  |                              |
|             +----------+----------+                              |
|                        |                                         |
+------------------------------------------------------------------+
                         |
                         v
              +---------------------+
              |     PostgreSQL      |
              |  (shared with       |
              |   supervisor)       |
              +---------------------+
```

### Key Pages

#### Dashboard Component
```tsx
// web-ui/src/app/page.tsx
export default function Dashboard() {
  return (
    <Grid container spacing={3}>
      <Grid item xs={12} md={4}>
        <PipelineStatusCard />
      </Grid>
      <Grid item xs={12} md={4}>
        <TaskQueueCard />
      </Grid>
      <Grid item xs={12} md={4}>
        <ResourceUsageCard />
      </Grid>
      <Grid item xs={12}>
        <AgentActivityFeed />
      </Grid>
    </Grid>
  );
}
```

#### API Routes
```
/api/tasks              GET (list), POST (create)
/api/tasks/[id]         GET, PATCH, DELETE
/api/tasks/[id]/context GET (full context chain)
/api/tasks/[id]/retry   POST (retry from checkpoint)
/api/tasks/[id]/cancel  POST (cancel running)
/api/tasks/[id]/unblock POST (unblock BLOCKED)

/api/agents             GET (list all)
/api/agents/[name]      GET, PATCH (enable/disable)

/api/repos              GET (list registered repos), POST (add new repo)
/api/repos/[name]       GET (repo details + .aquarco.yaml), PATCH, DELETE
/api/repos/[name]/pull  POST (force pull/re-clone)
/api/repos/[name]/logs  GET (clone/pull logs)

/api/pipelines          GET (list running)
/api/pipelines/[id]     GET (pipeline details)

/api/config             GET, PUT (supervisor.yaml)
/api/config/reload      POST (trigger reload)
/api/config/publish     POST (commit and push to aquarco repo)

/api/logs               GET (with streaming support)
/api/ws                 WebSocket endpoint
```

#### WebSocket Events
```typescript
// Events sent to clients
interface WSEvent {
  type: 'task_update' | 'agent_status' | 'log_line' | 'pipeline_progress';
  payload: any;
}

// task_update
{ type: 'task_update', payload: { id: 'task-123', status: 'executing', stage: 3 } }

// agent_status
{ type: 'agent_status', payload: { name: 'analyze-agent', active: 1, total: 5 } }

// log_line
{ type: 'log_line', payload: { timestamp: '...', level: 'info', message: '...' } }

// pipeline_progress
{ type: 'pipeline_progress', payload: { task_id: '...', stage: 3, total: 6 } }
```

### Database Queries (examples)

```typescript
// Get dashboard stats
const stats = await prisma.$queryRaw`
  SELECT
    COUNT(*) FILTER (WHERE status = 'pending') as pending,
    COUNT(*) FILTER (WHERE status = 'executing') as executing,
    COUNT(*) FILTER (WHERE status = 'completed' AND completed_at > NOW() - INTERVAL '1 hour') as completed_hour,
    COUNT(*) FILTER (WHERE status = 'blocked') as blocked
  FROM tasks
`;

// Get task with full context chain
const task = await prisma.task.findUnique({
  where: { id },
  include: {
    stages: {
      orderBy: { stage_number: 'asc' },
      select: {
        stage_number: true,
        category: true,
        agent: true,
        status: true,
        structured_output: true,
        completed_at: true
      }
    }
  }
});
```

---

## 9. Repo Topology

The Aquarco system distinguishes between two types of repositories with fundamentally different purposes.

### Two Types of Repos

**Aquarco repo** (`aquarco`) — the system itself:
- Supervisor code, pollers, pipeline executor
- Agent definitions (YAML) and prompts (MD)
- Web UI (Next.js monitoring dashboard)
- VM provisioning (Vagrant, Ansible)
- `supervisor.yaml`, `prd.json`
- This is what gets provisioned into the VM

**Target repos** (N repos) — the projects agents work on:
- Actual applications, libraries, services
- Agents clone these inside the VM, work on them (analyze, design, implement, review, test)
- Each target repo has its own GitHub issues, PRs, branches
- Agents push commits, open PRs, post reviews back to these repos

### VM Directory Layout

```
/home/agent/
├── aquarco/                    <- Aquarco repo (the system)
│   ├── supervisor/
│   ├── agents/definitions/
│   ├── agents/prompts/
│   ├── web-ui/
│   └── supervisor.yaml
│
└── repos/                      <- Target repos (cloned by supervisor)
    ├── my-saas-app/
    ├── internal-api/
    └── mobile-backend/
```

### Key Principles

1. **GitHub Tasks Poller polls issues from TARGET repos, not from aquarco** — The poller watches for issues tagged `agent-task` in the repos where actual work happens.

2. **GitHub Source Poller watches PRs/commits on TARGET repos** — When a PR is opened on `my-saas-app`, the review pipeline runs against that repo.

3. **The `repository` field in tasks table tracks which target repo a task belongs to** — Every task is associated with a specific target repo.

4. **"Publish changes" sync pushes config back to aquarco repo only** — When you edit `supervisor.yaml` or agent definitions via the Web UI, those changes are committed to the aquarco repo, not to target repos.

5. **Agent definitions in aquarco are UNIVERSAL** — The same `review-agent` reviews code across all target repos. Repo-specific customizations come from `.aquarco.yaml` in each target repo.

6. **Workspace isolation** — Each target repo gets its own clone directory. Agents working on one repo cannot accidentally modify another.

### Repo Registration in Supervisor Config

Target repos are registered in `supervisor.yaml`:

```yaml
# Repository registration
# Default auth: GitHub PAT (loaded from secrets.githubTokenFile)
# Clone URL: https://<token>@github.com/owner/repo.git
repositories:
  - name: my-saas-app
    url: https://github.com/owner/my-saas-app.git
    branch: main
    cloneDir: /home/agent/repos/my-saas-app
    pollers: [github-tasks, github-source]
    # auth: pat  # default, uses PAT from secrets.githubTokenFile
  - name: internal-api
    url: https://github.com/owner/internal-api.git
    branch: main
    cloneDir: /home/agent/repos/internal-api
    pollers: [github-tasks]
  - name: legacy-system
    url: git@github.com:owner/legacy-system.git
    branch: main
    cloneDir: /home/agent/repos/legacy-system
    pollers: [github-tasks]
    auth: deploy-key                           # optional override: use deploy key instead of PAT
    deployKeyFile: ~/.ssh/deploy_legacy_system # path to repo-specific deploy key

# Per-repo config auto-reload
repoConfig:
  autoReload: true           # Re-read .aquarco.yaml after every git pull
  configFile: .aquarco.yaml   # Config file name in target repo root
```

Pollers reference registered repo names (or "all") instead of hardcoded "owner/repo" strings:

```yaml
pollers:
  - name: github-tasks
    config:
      repositories: all  # or ["my-saas-app", "internal-api"]
```

### Per-Repo Agent Overrides (.aquarco.yaml)

Target repos can optionally include a `.aquarco.yaml` file in their root to customize agent behavior for that repo:

```yaml
# .aquarco.yaml (in target repo root)
apiVersion: aquarco.repo/v1
kind: RepoConfig

spec:
  # Override pipeline stages for this repo
  pipelines:
    feature-pipeline:
      skip_stages: [design, docs]
    bugfix-pipeline:
      skip_stages: [docs]

  # Per-agent overrides for this repo
  agents:
    review:
      extra_context: |
        This is a Go project. Focus on:
        - Error handling patterns (no naked returns)
        - Context propagation
        - Interface compliance
    implementation:
      extra_context: |
        Use Go 1.22+. Prefer stdlib over third-party libraries.
        Run `go vet` and `golangci-lint` before committing.
    test:
      extra_context: |
        Use table-driven tests. Test edge cases.
        Minimum 80% coverage for new code.

  # Labels that should auto-trigger specific pipelines
  label_overrides:
    urgent-bug: bugfix-pipeline
    needs-refactor: implementation

  # Files/directories to never touch
  protected_paths:
    - "vendor/"
    - "go.sum"
    - ".github/workflows/"
```

**Merge Behavior:**
- The supervisor loads `.aquarco.yaml` from each target repo after cloning
- Repo-level config merges with and overrides global config for that repo only
- If no `.aquarco.yaml` exists, global defaults apply

### Agent Executor Updates for Multi-Repo

When the supervisor executes an agent for a task, it must:

1. **Look up which repository the task belongs to** — Read `repository` field from task record
2. **`cd` into that repo's clone directory** — Set working directory to `/home/agent/repos/<repo-name>/`
3. **Load the repo's `.aquarco.yaml` if present** — Parse and validate against schema
4. **Merge repo-specific `extra_context` into the agent's prompt** — Append repo-level context to agent system prompt
5. **Apply repo-specific pipeline stage overrides** — Skip stages listed in `skip_stages`
6. **Run the agent in the context of the target repo, NOT in aquarco** — All file operations happen in the target repo

```bash
execute_agent() {
    local agent_name="$1"
    local task_id="$2"
    local context_bundle="$3"

    # NEW: Determine target repo and working directory
    local repository=$(get_task_repository "$task_id")
    local repo_config=$(get_repo_config "$repository")
    local workdir=$(echo "$repo_config" | jq -r '.cloneDir')

    # Load repo-specific overrides
    local aquarco_yaml="$workdir/.aquarco.yaml"
    local extra_context=""
    if [[ -f "$aquarco_yaml" ]]; then
        extra_context=$(yq eval ".spec.agents.$agent_name.extra_context // ''" "$aquarco_yaml")
    fi

    # Load agent definition
    local agent_def=$(get_agent_definition "$agent_name")
    local prompt_file=$(echo "$agent_def" | jq -r '.spec.promptFile')
    local timeout=$(echo "$agent_def" | jq -r '.spec.resources.timeoutMinutes // 30')
    local max_tokens=$(echo "$agent_def" | jq -r '.spec.resources.maxTokens // 100000')

    # Build context with repo-specific additions
    local context_file=$(mktemp)
    cat > "$context_file" << EOF
# Task Context

## Task ID
$task_id

## Repository
$repository

## Repo-Specific Context
$extra_context

## Context Bundle
$context_bundle

## Instructions
Execute your role as defined in your system prompt.
Output your results as a JSON block matching your output schema.
Wrap the JSON in \`\`\`json ... \`\`\` markers.
EOF

    # Execute Claude Code IN THE TARGET REPO DIRECTORY
    cd "$workdir"

    # ... rest of execution logic
}
```

---

## Subtasks

- [ ] Create agent definition schema (JSON Schema) — assigned to: scripting
- [ ] Create example agent definitions for all 6 categories — assigned to: scripting
- [ ] Create agent prompts for all 6 categories — assigned to: docs
- [ ] Implement agent discovery script — assigned to: scripting
- [ ] Implement supervisor main loop — assigned to: scripting
- [ ] Implement GitHub tasks poller — assigned to: scripting
- [ ] Implement GitHub source poller — assigned to: scripting
- [ ] Implement external trigger interface — assigned to: scripting
- [ ] Implement pipeline executor — assigned to: scripting
- [ ] Design PostgreSQL schema for task queue + context — assigned to: database
- [ ] Add agent_version tracking to database schema — assigned to: database
- [ ] Add token usage metrics columns to database schema — assigned to: database
- [ ] Add repository field to tasks schema (multi-repo prep) — assigned to: database
- [ ] Implement context contract validation in supervisor — assigned to: scripting
- [ ] Implement accumulated context builder — assigned to: scripting
- [ ] Build Web UI dashboard — assigned to: frontend
- [ ] Build Web UI API routes — assigned to: graphql
- [ ] Create Ansible role for supervisor deployment — assigned to: dev-infra
- [ ] Create systemd service file for supervisor — assigned to: dev-infra
- [ ] Implement VM state sync mechanism (web UI edits -> git) — assigned to: scripting
- [ ] Write integration tests for supervisor — assigned to: testing
- [ ] Security review of agent capability system — assigned to: security
- [ ] Document agent development guide — assigned to: docs
- [ ] Implement repo registration and clone management — assigned to: scripting
- [ ] Implement .aquarco.yaml loader and merger — assigned to: scripting
- [ ] Add repository management page to Web UI — assigned to: frontend

## Acceptance Criteria

- Agent definitions in YAML are automatically discovered without code changes
- Dropping a new agent definition file makes the agent available within one poll cycle
- GitHub issues with `agent-task` label are automatically picked up and processed
- Pull requests trigger review and test pipelines automatically
- External trigger files are processed and converted to tasks
- Tasks flow through pipelines with proper stage sequencing
- Agent output is validated against schema before passing to next stage
- Accumulated context is built correctly from PostgreSQL
- Large blobs are stored as file references, not inline
- Agent resource limits (concurrency, timeout, tokens) are enforced
- Task state is persisted across supervisor restarts
- Failed stages can be retried from checkpoint
- BLOCKED tasks can be manually unblocked via Web UI
- Web UI shows real-time pipeline and agent status
- Logs provide full traceability of task execution
- System works correctly with 2 agents or 20 agents (scalable)

## Notes

### Design Decisions

1. **YAML for agent definitions** — Human-readable, supports comments, widely understood
2. **PostgreSQL over SQLite** — Shared with future dev environment, queryable, transactional, supports concurrent access from supervisor + web UI
3. **Both context approaches** — Structured output for reliability (validated, stored in DB), accumulated context for nuance (full chain available to agents)
4. **Full-stack Web UI** — Dogfooding project tech stack (Next.js + React + MUI), provides rich monitoring and intervention capabilities
5. **Agents communicate only through task queue** — No direct spawning, all work goes through queue for visibility and control
6. **Per-stage retry + checkpoint/resume** — Failed stages retry up to N times, then checkpoint and mark BLOCKED for human intervention
7. **File-based external triggers** — Universal interface, works with any system that can write files
8. **Polling over webhooks** — Simpler security model (no inbound ports), acceptable latency for async work
9. **Category-based routing** — Decouples tasks from agents, allows hot-swapping agents
10. **Large blobs as file references** — Hybrid approach: structured context in PostgreSQL, large blobs stored as `{ key: "generated_diff", value: "ref:blobs/abc123.patch", type: "file_ref" }`
11. **Direct PostgreSQL connections for supervisor, pooled for web UI** — Supervisor runs one pipeline at a time (3-4 connections max), no PgBouncer needed. Web UI uses Prisma built-in pool.
12. **SIGHUP for config hot-reload** — Web UI writes validated YAML, sends SIGHUP to supervisor PID. Supervisor catches SIGHUP and re-reads config. Config validation before write.
13. **Structured fields as implicit summaries, full output only for N-1** — Original issue always full. Intermediate stages use structured output fields as summaries (no separate Claude call). Previous stage (N-1) gets full output for maximum context.
14. **No auth for web UI** — Local dev machine only, accessible only via VirtualBox NAT from host. No internet exposure, no auth complexity.
15. **Claude Code Max subscription, not API** — Token metrics are for observability only (understanding efficiency, comparing usage, spotting bloat), not billing.
16. **GitHub PAT for all target repos** — Single PAT with `repo` scope accesses all repos the user has access to. Reuses the same token used for `gh` CLI. Clone via HTTPS with token: `https://<token>@github.com/owner/repo.git`. Simpler than managing per-repo deploy keys. Deploy keys available as optional override in `supervisor.yaml` for tighter scoping.
17. **Auto-reload `.aquarco.yaml` on pull** — Supervisor re-reads `.aquarco.yaml` after every `git pull` during the poll cycle. If the file changed (compare hash/mtime), merge new config into running state. Part of the normal poll loop, no separate signal needed. If malformed, log warning and keep previous valid config.

### Open Questions

1. ~~PostgreSQL connection pooling strategy for supervisor + web UI~~ — **RESOLVED**: Supervisor uses direct connections (3-4 max, runs one pipeline at a time), Web UI uses Prisma built-in pool. No PgBouncer needed.
2. ~~How to handle `supervisor.yaml` hot-reload when edited via web UI~~ — **RESOLVED**: SIGHUP. Web UI writes validated YAML, sends SIGHUP to supervisor PID. Supervisor catches SIGHUP and re-reads config. Config validation happens before write.
3. ~~Token budget strategy for accumulated context~~ — **RESOLVED**: Original issue ALWAYS full. Intermediate stages (0 to N-2): summaries only (structured output fields serve as implicit summaries). Previous stage (N-1): full output. Supervisor extracts summaries from structured fields — no separate Claude call.
4. ~~WebSocket authentication for the web UI~~ — **RESOLVED**: No auth. Local dev machine only, accessible only via VirtualBox NAT from host. No auth complexity needed.
5. ~~Blob garbage collection~~ — **RESOLVED**: Daily cron/background loop with retention rules as designed. Keep as-is.
6. ~~How to sync VM state (config, agent definitions) with initial state so the VM can be destroyed and reprovisioned with latest configuration?~~ — **RESOLVED**: "Publish changes" button in web UI commits config changes back to the aquarco repo. Flow:
   1. User edits config via web UI
   2. Changes are written to local files in `/home/agent/aquarco/`
   3. User clicks "Publish changes"
   4. Web UI shows diff of what changed
   5. User confirms
   6. Web UI runs: `git add`, `git commit`, `git push` in the aquarco repo
   7. Next time VM is provisioned (`vagrant destroy` + `vagrant up`), it pulls latest aquarco with those changes
7. ~~How to handle deploy keys for multiple target repos?~~ — **RESOLVED**: Use GitHub PAT (Personal Access Token) with `repo` scope. Single PAT accesses all repos the user has access to. Already in the design for `gh` CLI — reuse the same token for git operations. Clone via HTTPS with token: `https://<token>@github.com/owner/repo.git`. Simpler than managing per-repo deploy keys. This is a local dev VM, not production — PAT scope is acceptable. Deploy keys remain available as an optional override in `supervisor.yaml` for anyone who wants tighter scoping per repo.
8. ~~Should `.aquarco.yaml` changes in target repos trigger supervisor reload?~~ — **RESOLVED**: Auto-reload on pull. Supervisor re-reads `.aquarco.yaml` after every `git pull` during the poll cycle. If the file changed (compare hash/mtime), merge new config into running state. No SIGHUP or signal mechanism needed — it's part of the normal poll loop. Nearly free: one file read + YAML parse per poll cycle per repo. If `.aquarco.yaml` is invalid/malformed, log a warning and keep using the previous valid config.

### Risks

- **PostgreSQL dependency** — Supervisor cannot start without DB (need health check + retry logic in systemd service)
- **Context token bloat** — Accumulated context may exceed model limits on deep pipelines (mitigated: structured fields as summaries, full output only for N-1 stage)
- **Token exhaustion** — Global token limits may throttle work during busy periods
- **Polling latency** — 60-second intervals mean up to 60s delay for new tasks
- **Agent prompt drift** — Prompts may become stale as codebase evolves
- **Pipeline deadlocks** — Circular dependencies in triggers could cause infinite loops
- ~~Config drift between web UI edits and git repo~~ — **Mitigated**: "Publish changes" button commits config back to aquarco repo. Users must click to sync; unpublished changes will be lost on VM reprovision.

### Dependencies

- TASK-001: VirtualBox Sandbox Architecture (for VM infrastructure)
- Claude Code CLI installed and configured
- PostgreSQL 15+ installed and configured
- GitHub repository with deploy key
- Anthropic API key

### Future Enhancements

**Planned (on roadmap):**
- Agent versioning and rollback — `agent_version` column added to stages table, tracks which version produced which output
- Metrics and token usage tracking per agent — `tokens_input`, `tokens_output` columns added to stages table. NOTE: This is a local dev environment using Claude Code Max subscription, NOT API token-based billing. Cost tracking is for observability only (understanding agent efficiency, comparing token usage across agents, spotting bloated prompts), not actual billing.
- Multi-repo support — `repository` field added to tasks table (schema ready, implementation deferred)
- Web UI: dark mode — MUI built-in dark mode support
- Web UI: mobile-responsive layout — MUI responsive grid

**Deferred (not on roadmap yet):**
- A/B testing of agent prompts — defer until there's volume to justify complexity
