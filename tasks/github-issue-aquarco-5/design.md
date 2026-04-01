# Design: Aquarco Host-Side CLI

**Task:** github-issue-aquarco-5
**Date:** 2026-03-31
**Status:** Design Complete

## 1. Overview

Create a Python CLI tool (`aquarco`) that runs on the macOS host and provides subcommands to manage the Aquarco VM environment. The CLI communicates with the VM via `vagrant ssh` for shell operations and HTTP to the port-forwarded GraphQL API (`localhost:8080/api/graphql`) for data operations.

**Language decision:** Python + Typer, consistent with the existing supervisor CLI (`supervisor/python/`). This allows sharing patterns (Typer conventions, Pydantic models) and keeps the toolchain unified.

## 2. Package Structure

```
cli/
  pyproject.toml
  src/
    aquarco_cli/
      __init__.py
      main.py                  # Typer app entry point, registers all subcommands
      config.py                # CLI configuration (API URL, Vagrant dir, timeouts)
      console.py               # Rich console helpers for formatted output
      graphql_client.py        # Sync HTTP client for GraphQL API
      vagrant.py               # Vagrant subprocess helpers (ssh, up, status, halt)
      health.py                # Stack health check logic
      commands/
        __init__.py
        install.py             # aquarco install
        update.py              # aquarco update
        auth.py                # aquarco auth {claude,github,status}
        watch.py               # aquarco watch <url>, aquarco watch list
        run.py                 # aquarco run
        status.py              # aquarco status [task-id]
        ui.py                  # aquarco ui [start|stop]
  tests/
    __init__.py
    conftest.py
    test_graphql_client.py
    test_vagrant.py
    test_health.py
    test_commands/
      __init__.py
      test_install.py
      test_update.py
      test_auth.py
      test_watch.py
      test_run.py
      test_status.py
      test_ui.py
```

## 3. Core Infrastructure Modules

### 3.1 `main.py` — Entry Point

```python
import typer
from aquarco_cli.commands import install, update, auth, watch, run, status, ui

app = typer.Typer(
    name="aquarco",
    help="Aquarco CLI — manage your autonomous AI agent environment.",
    no_args_is_help=True,
)

# Register top-level commands
app.command("install")(install.install)
app.command("update")(update.update)
app.command("run")(run.run_task)
app.command("status")(status.status)
app.command("ui")(ui.ui)

# Register sub-apps (nested commands)
app.add_typer(auth.app, name="auth", help="Manage Claude and GitHub authentication.")
app.add_typer(watch.app, name="watch", help="Manage watched repositories.")
```

Entry point in `pyproject.toml`:
```toml
[project.scripts]
aquarco = "aquarco_cli.main:app"
```

### 3.2 `config.py` — CLI Configuration

Resolves configuration from environment variables with sensible defaults:

```python
from dataclasses import dataclass, field

@dataclass
class CliConfig:
    api_url: str = "http://localhost:8080/api/graphql"   # Caddy-proxied GraphQL
    vagrant_dir: str = ""          # Auto-detected: find vagrant/Vagrantfile relative to CLI install or CWD
    request_timeout: int = 30      # seconds for GraphQL calls
    ssh_timeout: int = 60          # seconds for vagrant ssh commands
    health_timeout: int = 10       # seconds per health probe
```

- `AQUARCO_API_URL` env var overrides `api_url`
- `AQUARCO_VAGRANT_DIR` env var overrides `vagrant_dir`
- Auto-detection: walk up from CWD looking for `vagrant/Vagrantfile`

### 3.3 `graphql_client.py` — GraphQL HTTP Client

Synchronous client using `httpx` (chosen over `requests` for HTTP/2 support and cleaner API):

```python
class GraphQLClient:
    def __init__(self, url: str, timeout: int = 30):
        self.url = url
        self.client = httpx.Client(timeout=timeout)

    def execute(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query/mutation. Raises GraphQLError on errors."""
        response = self.client.post(self.url, json={"query": query, "variables": variables or {}})
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            raise GraphQLError(data["errors"])
        return data["data"]

    def close(self):
        self.client.close()

class GraphQLError(Exception):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__(errors[0].get("message", "GraphQL error"))
```

**Assumption:** The GraphQL API at `:8080/api/graphql` does not require authentication for local access (it's behind the Caddy proxy, accessed from localhost). If auth is needed, we'll add a bearer token header.

**Note on subscriptions:** For `--follow` flags on `run` and `status` commands, WebSocket subscription support is deferred to a follow-up iteration. Initial implementation will use polling (re-query every 2s) for simplicity.

### 3.4 `vagrant.py` — Vagrant Subprocess Helpers

```python
import subprocess
from pathlib import Path

class VagrantHelper:
    def __init__(self, vagrant_dir: Path, timeout: int = 60):
        self.vagrant_dir = vagrant_dir
        self.timeout = timeout

    def ssh(self, command: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        """Run a command inside the VM via vagrant ssh."""
        return subprocess.run(
            ["vagrant", "ssh", "-c", command],
            cwd=self.vagrant_dir,
            capture_output=True, text=True,
            timeout=timeout or self.timeout,
        )

    def up(self, provision: bool = True) -> subprocess.CompletedProcess:
        """Start the VM. Streams stdout/stderr to terminal."""
        cmd = ["vagrant", "up"]
        if provision:
            cmd.append("--provision")
        return subprocess.run(cmd, cwd=self.vagrant_dir, timeout=1800)  # 30 min for provisioning

    def status(self) -> str:
        """Return VM status: 'running', 'poweroff', 'not_created', etc."""
        result = subprocess.run(
            ["vagrant", "status", "--machine-readable"],
            cwd=self.vagrant_dir, capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            parts = line.split(",")
            if len(parts) >= 4 and parts[2] == "state":
                return parts[3]
        return "unknown"

    def halt(self) -> subprocess.CompletedProcess:
        return subprocess.run(["vagrant", "halt"], cwd=self.vagrant_dir, timeout=120)
```

### 3.5 `health.py` — Stack Health Checks

Probes the forwarded ports to verify services are running:

```python
def check_stack_health(config: CliConfig) -> dict[str, bool]:
    """Return health status for each core service."""
    checks = {
        "web":      ("http://localhost:8080", 200),
        "api":      ("http://localhost:8080/api/graphql", 200),  # POST with introspection
        "postgres": ("localhost", 15432),  # TCP connect
    }
    # Returns {"web": True, "api": True, "postgres": False}
```

### 3.6 `console.py` — Rich Output Helpers

Use the `rich` library for formatted terminal output (tables, panels, spinners):

```python
from rich.console import Console
from rich.table import Table

console = Console()

def print_task_table(tasks: list[dict]) -> None: ...
def print_dashboard(stats: dict) -> None: ...
def print_health(results: dict[str, bool]) -> None: ...
def print_error(message: str) -> None: ...
def print_success(message: str) -> None: ...
```

## 4. Command Designs

### 4.1 `aquarco install`

```
aquarco install [--no-provision] [--memory MB] [--cpus N]
```

**Steps:**
1. Check prerequisites: `VBoxManage --version` and `vagrant --version` must succeed
2. Locate `vagrant/Vagrantfile` (auto-detect or from config)
3. Run `vagrant up --provision` (streams output to terminal)
4. Wait for provisioning to complete
5. Run health checks (`check_stack_health`)
6. Print summary: VM IP, forwarded ports, service status

**Error handling:** If `vagrant up` fails, print the stderr and suggest `vagrant destroy && aquarco install` for a clean retry.

### 4.2 `aquarco update`

```
aquarco update [--dry-run] [--skip-migrations] [--skip-provision]
```

**Steps:**
1. Verify VM is running (`vagrant status`)
2. `git pull` on host (in the aquarco repo root)
3. SSH into VM: `cd /home/agent/aquarco/docker && sudo docker compose pull`
4. SSH: run migrations container `sudo docker compose run --rm migrations`  (unless `--skip-migrations`)
5. SSH: `sudo docker compose up -d --build`
6. SSH: `sudo -u agent pip install -e /home/agent/aquarco/supervisor/python`
7. SSH: `sudo systemctl restart aquarco-supervisor-python`
8. Optionally re-provision: `vagrant provision` (unless `--skip-provision`)
9. Run health checks
10. Print summary

**`--dry-run`:** Execute steps 1-2 (show current vs latest commit), then print what would happen for steps 3-8 without executing.

### 4.3 `aquarco auth`

Sub-app with three subcommands:

#### `aquarco auth claude`
1. Call `claudeLoginStart` mutation -> get `authorizeUrl`
2. Open URL in host browser (`webbrowser.open()`)
3. Prompt user: "Authorize in browser, then paste the redirect URL or code here:"
4. Call `claudeSubmitCode(code)` mutation
5. Poll `claudeLoginPoll` until success or timeout
6. Print auth status

#### `aquarco auth github`
1. Call `githubLoginStart` mutation -> get `userCode`, `verificationUri`
2. Print: "Enter code **XXXX-YYYY** at https://github.com/login/device"
3. Open URL in host browser
4. Poll `githubLoginPoll` mutation until success or timeout (respect `interval`)
5. Print auth status

#### `aquarco auth status`
1. Query `claudeAuthStatus` and `githubAuthStatus`
2. Print table:
   ```
   Service   Status          Details
   Claude    Authenticated   user@example.com
   GitHub    Not configured  -
   ```

### 4.4 `aquarco watch`

Sub-app:

#### `aquarco watch add <url>` (default when called as `aquarco watch <url>`)
```
aquarco watch add <url> [--name NAME] [--branch BRANCH] [--pollers github-tasks,github-source]
```

1. Parse repo URL to extract default name (e.g., `borissuska/myapp` -> `myapp`)
2. Call `registerRepository` mutation with `{name, url, branch, pollers}`
3. Poll `repository(name)` query until `cloneStatus` is `READY` or `ERROR` (with spinner)
4. Print result

#### `aquarco watch list`
1. Query `repositories`
2. Print table: name, URL, branch, clone status, pollers, task count

#### `aquarco watch remove <name>`
1. Call `removeRepository(name)` mutation
2. Print confirmation

### 4.5 `aquarco run`

```
aquarco run <title> --repo REPO [--pipeline PIPELINE] [--priority N] [--context FILE] [--follow]
```

1. Read `--context` from file or stdin if provided (JSON)
2. Call `createTask` mutation:
   ```graphql
   mutation($input: CreateTaskInput!) {
     createTask(input: $input) {
       task { id title status pipeline }
       errors { field message }
     }
   }
   ```
   Variables: `{title, repository: repo, source: "cli", pipeline, priority, initialContext}`
3. Print task ID and status
4. If `--follow`: poll `pipelineStatus(taskId)` every 2s, printing stage transitions until terminal status

### 4.6 `aquarco status`

```
aquarco status [TASK_ID] [--follow] [--json] [--limit N]
```

#### Dashboard mode (no task ID):
1. Query `dashboardStats`
2. Query `tasks(limit: 10)` for recent activity
3. Print formatted dashboard:
   ```
   Aquarco Status
   ──────────────
   Tasks: 5 pending | 2 executing | 42 completed | 1 failed
   Agents: 1 active
   Cost today: $12.34

   Recent Tasks:
   ID    Title              Status     Pipeline         Stage
   123   Fix auth bug       executing  bugfix-pipeline  2/5 (implement)
   122   Add dark mode      completed  feature-pipeline -
   ```

#### Detail mode (with task ID):
1. Query `task(id)` with stages
2. Print detailed view: title, status, pipeline, current stage, timestamps, error, stage history
3. If `--follow`: poll every 2s until terminal status

#### `--json` flag:
Print raw JSON response instead of formatted table.

### 4.7 `aquarco ui`

```
aquarco ui [start|stop] [--open]
```

- **start** (default): SSH -> `cd /home/agent/aquarco/docker && sudo docker compose up -d web api postgres caddy`; if `--open`, run `webbrowser.open("http://localhost:8080")`
- **stop**: SSH -> `cd /home/agent/aquarco/docker && sudo docker compose stop web api`

## 5. Dependencies

```toml
[project]
name = "aquarco-cli"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "typer>=0.9",
    "httpx>=0.25",
    "rich>=13.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4",
    "pytest-mock>=3.11",
    "mypy>=1.5",
    "ruff>=0.1",
]
```

**Why these dependencies:**
- `typer` — CLI framework, consistent with supervisor
- `httpx` — modern sync/async HTTP client (replaces `requests`), needed for GraphQL
- `rich` — terminal formatting (tables, spinners, colored output); Typer already uses Rich internally

**Not included:**
- `websockets` — deferred; `--follow` uses polling initially
- `gql` — overkill for our simple GraphQL needs; raw httpx POST is sufficient

## 6. GraphQL Queries & Mutations Reference

All queries the CLI will use, defined as constants in `graphql_client.py`:

```python
# Queries
DASHBOARD_STATS = "query { dashboardStats { totalTasks pendingTasks executingTasks completedTasks failedTasks blockedTasks activeAgents totalTokensToday totalCostToday } }"

TASKS_LIST = """query($status: TaskStatus, $limit: Int) {
  tasks(status: $status, limit: $limit) {
    nodes { id title status priority pipeline createdAt repository { name } }
    totalCount
  }
}"""

TASK_DETAIL = """query($id: ID!) {
  task(id: $id) {
    id title status priority pipeline source sourceRef
    repository { name }
    createdAt startedAt completedAt
    retryCount errorMessage branchName prNumber totalCostUsd
    stages { id stageNumber category agent status startedAt completedAt errorMessage }
  }
}"""

PIPELINE_STATUS = """query($taskId: ID!) {
  pipelineStatus(taskId: $taskId) {
    taskId pipeline lastCompletedStageId totalStages status
    stages { stageNumber category agent status startedAt completedAt }
  }
}"""

REPOSITORIES = "query { repositories { name url branch cloneStatus pollers taskCount errorMessage } }"
REPOSITORY = "query($name: String!) { repository(name: $name) { name url branch cloneDir cloneStatus pollers taskCount errorMessage } }"

CLAUDE_AUTH_STATUS = "query { claudeAuthStatus { authenticated email } }"
GITHUB_AUTH_STATUS = "query { githubAuthStatus { authenticated username } }"

# Mutations
CREATE_TASK = """mutation($input: CreateTaskInput!) {
  createTask(input: $input) { task { id title status pipeline } errors { field message } }
}"""

REGISTER_REPOSITORY = """mutation($input: RegisterRepositoryInput!) {
  registerRepository(input: $input) { repository { name cloneStatus } errors { field message } }
}"""

REMOVE_REPOSITORY = """mutation($name: String!) {
  removeRepository(name: $name) { repository { name } errors { field message } }
}"""

CLAUDE_LOGIN_START = "mutation { claudeLoginStart { authorizeUrl expiresIn } }"
CLAUDE_LOGIN_POLL = "mutation { claudeLoginPoll { success email error } }"
CLAUDE_SUBMIT_CODE = "mutation($code: String!) { claudeSubmitCode(code: $code) { success email error } }"

GITHUB_LOGIN_START = "mutation { githubLoginStart { userCode verificationUri expiresIn } }"
GITHUB_LOGIN_POLL = "mutation { githubLoginPoll { success username error } }"
```

## 7. Assumptions

1. **macOS host only** — the CLI targets macOS as the host OS. Linux host support is a stretch goal but not designed for in v1.
2. **No auth on GraphQL API** — the API at `:8080/api/graphql` is accessible without authentication from localhost.
3. **Vagrant in PATH** — both `vagrant` and `VBoxManage` are available in the host's PATH.
4. **Single VM** — only one Aquarco VM exists at a time. The CLI does not support multi-VM scenarios.
5. **Python 3.10+ on host** — macOS ships with no Python; users install via Homebrew. The CLI requires Python >= 3.10.
6. **Subscriptions deferred** — WebSocket-based `--follow` is replaced with polling in v1. Real subscriptions via `graphql-ws` protocol will be added in a follow-up.

## 8. Distribution (v1)

For v1, install via pip from the local checkout:
```bash
cd cli && pip install -e .
```

Future: publish to PyPI as `aquarco-cli`, or distribute via `pipx install aquarco-cli`.
