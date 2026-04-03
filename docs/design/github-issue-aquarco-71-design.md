# Design: Aquarco CLI Enhancements (Issue #71)

**Task ID:** github-issue-aquarco-71  
**Date:** 2026-04-03  
**Status:** Draft  

---

## Overview

This document provides the complete technical design for all 13 CLI enhancement items in
GitHub issue #71. Items are grouped by complexity and implementation order: trivial/low
first, then medium, then the high-complexity drain-mode feature (item #8) last.

The CLI is a Typer-based Python package at `cli/src/aquarco_cli/`. Commands are registered
in `main.py` and implemented per-file under `commands/`. Tests live in
`cli/tests/test_commands/`.

---

## Item 1 — -h alias for --help (Trivial)

**Affected files:** `cli/src/aquarco_cli/main.py` and all `commands/*.py` that define a
`typer.Typer()` instance.

### Design

Typer delegates `context_settings` directly to Click. Adding
`context_settings={"help_option_names": ["-h", "--help"]}` to every `Typer()` constructor
enables `-h` as an alias.

**Instances to update:**

| File | Variable |
|------|----------|
| `main.py` | `app = typer.Typer(...)` |
| `commands/init.py` (renamed from install) | `app = typer.Typer()` |
| `commands/update.py` | `app = typer.Typer()` |
| `commands/auth.py` | `app = typer.Typer(...)` |
| `commands/repos.py` (renamed from watch) | `app = typer.Typer(...)` |
| `commands/run.py` | `app = typer.Typer()` |
| `commands/status.py` | `app = typer.Typer()` |
| `commands/ui.py` | `app = typer.Typer(...)` |

**Pattern (apply to every instance):**
```python
app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    ...existing kwargs...,
)
```

**Risk note:** Sub-apps that already set `no_args_is_help=True` (auth, ui) also need
`context_settings`. There is no conflict — Typer merges these kwargs cleanly.

---

## Item 7 — Remove git pull from update (Trivial)

**Affected files:** `cli/src/aquarco_cli/commands/update.py`

### Design

Remove the first entry from `STEPS`:
```python
# DELETE this entry:
("Pull latest source code", "git pull --ff-only"),
```

Remove the host-side git pull block (lines 56–66 in current source):
```python
# DELETE from update():
repo_root = get_config().resolve_vagrant_dir().parent
print_info("Pulling latest source code on host...")
try:
    subprocess.run(["git", "pull", "--ff-only"], ...)
except subprocess.CalledProcessError as exc:
    print_warning(f"git pull failed: {exc.stderr.strip()}")
```

Remove the `import subprocess` if it becomes unused after this removal and after removing
the git-step skip logic. (**Note:** `subprocess` is still needed via `VagrantError` catch
in the SSH loop — keep the import.)

Remove the dead SSH skip guard:
```python
# DELETE:
if cmd.startswith("git"):
    continue  # already done on host
```

---

## Item 10 — Fix status --help documentation (Trivial)

**Affected files:** `cli/src/aquarco_cli/commands/status.py`

### Design

Update the callback docstring and option `help=` strings:

```python
@app.callback(invoke_without_command=True)
def status(
    task_id: Optional[str] = typer.Argument(
        None,
        help="Optional task ID for a detailed single-task view. Omit to show dashboard.",
    ),
    follow: bool = typer.Option(
        False, "--follow", "-f",
        help="Poll until the task reaches a terminal state (requires TASK_ID).",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Output raw JSON instead of formatted tables.",
    ),
    limit: int = typer.Option(
        10, "--limit", "-l",
        help="Number of recent tasks to show in the dashboard view.",
    ),
) -> None:
    """Show task overview dashboard, or detailed status for a specific task.

    Without TASK_ID: shows a summary table of recent tasks and system stats.
    With TASK_ID: shows full task detail including all pipeline stages, cost, and timing.

    Examples:
      aquarco status                    # dashboard
      aquarco status abc123             # task detail
      aquarco status abc123 --follow    # live-follow until terminal state
      aquarco status abc123 --json      # raw JSON
    """
```

---

## Item 12 — Make --open default for ui (Trivial)

**Affected files:** `cli/src/aquarco_cli/commands/ui.py`

### Design

Change the `open_browser` option from `--open` (default False) to `--no-open` (default True):

```python
@app.callback(invoke_without_command=True)
def ui(
    ctx: typer.Context,
    no_open: bool = typer.Option(False, "--no-open", help="Do not open browser after starting"),
) -> None:
```

Replace all `if open_browser:` with `if not no_open:`. This change also applies to the
new subcommands designed in item #13.

---

## Item 6 — Rename watch → repos (Low)

**Affected files:**
- `cli/src/aquarco_cli/commands/watch.py` → renamed to `repos.py`
- `cli/src/aquarco_cli/main.py`
- `cli/tests/test_commands/test_watch.py` → renamed to `test_repos.py`

### Design

1. **File rename:** `watch.py` → `repos.py`. No content changes inside the file are
   strictly required beyond updating the module docstring from "aquarco watch" to
   "aquarco repos". The `app` variable name stays `app`.

2. **`main.py`** changes:
   ```python
   # Old:
   from aquarco_cli.commands import auth, install, run, status, ui, update, watch
   app.add_typer(watch.app, name="watch", help="Manage watched repositories.")

   # New:
   from aquarco_cli.commands import auth, init, run, status, ui, update, repos
   app.add_typer(repos.app, name="repos", help="Manage watched repositories.")
   ```

3. **Test rename:** `test_watch.py` → `test_repos.py`. Update all `runner.invoke(app, ["watch", ...])` calls to use `["repos", ...]`. Update `test_main.py` to assert `repos` appears in the help output instead of `watch`.

---

## Item 9 — Rename install → init (Low)

**Affected files:**
- `cli/src/aquarco_cli/commands/install.py` → renamed to `init.py`
- `cli/src/aquarco_cli/main.py`
- `cli/src/aquarco_cli/commands/update.py` (error message)
- `cli/src/aquarco_cli/commands/ui.py` (error message)
- `cli/src/aquarco_cli/console.py` (error message in `handle_api_error`)
- `cli/tests/test_commands/test_install.py` → renamed to `test_init.py`

### Design

1. **File rename:** `install.py` → `init.py`. Update module docstring.

2. **`main.py`** changes:
   ```python
   # Old:
   from aquarco_cli.commands import auth, install, run, status, ui, update, watch
   app.add_typer(install.app, name="install", help="Bootstrap the Aquarco VM.")

   # New:
   from aquarco_cli.commands import auth, init, repos, run, status, ui, update
   app.add_typer(init.app, name="init", help="Bootstrap the Aquarco VM.")
   ```

3. **`update.py`** — update error message:
   ```python
   # Old:
   print_error("VM is not running. Start it with 'aquarco install' first.")
   # New:
   print_error("VM is not running. Start it with 'aquarco init' first.")
   ```

4. **`ui.py`** — update error message:
   ```python
   # Old:
   print_error("VM is not running. Start it with 'aquarco install' first.")
   # New:
   print_error("VM is not running. Start it with 'aquarco init' first.")
   ```

5. **`console.py`** — update `handle_api_error`:
   ```python
   # Old:
   "Cannot reach the Aquarco API. Is the VM running? Try 'aquarco install' or 'aquarco ui'."
   # New:
   "Cannot reach the Aquarco API. Is the VM running? Try 'aquarco init' or 'aquarco ui'."
   ```

6. **Test rename:** `test_install.py` → `test_init.py`. Update all
   `runner.invoke(app, ["install", ...])` to `["init", ...]`. Update `test_main.py` to
   assert `init` appears in help instead of `install`.

---

## Item 3 — Docker healthchecks for web and api (Low)

**Affected files:** `docker/compose.yml`

### Design

The `node:20-alpine` image has `wget` but not `curl`. Use `wget` for all healthcheck
commands.

**Add to `api` service:**
```yaml
healthcheck:
  test: ["CMD-SHELL", "wget -qO- 'http://localhost:4000/api/graphql?query=%7B__typename%7D' || exit 1"]
  interval: 10s
  timeout: 5s
  retries: 10
  start_period: 60s
```

**Add to `web` service:**
```yaml
healthcheck:
  test: ["CMD-SHELL", "wget -qO- http://localhost:3000/ || exit 1"]
  interval: 10s
  timeout: 5s
  retries: 10
  start_period: 60s
```

**Update `depends_on` chain:**

`web` currently depends on `api` (plain). Change to condition:
```yaml
web:
  depends_on:
    api:
      condition: service_healthy
```

`caddy` currently depends on `web` and `api` (plain). Change to:
```yaml
caddy:
  depends_on:
    web:
      condition: service_healthy
    api:
      condition: service_healthy
```

**Note:** The `start_period: 60s` is required because Next.js dev mode takes 30–60s to
complete `npm install` before the HTTP server is ready. Healthcheck failures during
`start_period` do not count against `retries`.

---

## Item 4 — Graceful error when VM is not running (Low-Medium)

**Affected files:** `cli/src/aquarco_cli/commands/run.py`

### Design

The `handle_api_error()` function in `console.py` already converts `httpx.ConnectError`
and `httpx.TimeoutException` to friendly messages. All command-level API calls already use
it. The one gap is the polling loop in `run.py` (lines 94–131), which calls `print_warning`
on poll errors and only stops after `MAX_FOLLOW_ERRORS` consecutive failures.

**Change in `run.py` follow loop:**

Add a specific guard for `httpx.ConnectError` inside the follow loop so that a VM-down
event immediately aborts rather than retrying 5 times:

```python
except httpx.ConnectError as conn_exc:
    handle_api_error(conn_exc)
    raise typer.Exit(code=1) from conn_exc
except Exception as poll_exc:
    consecutive_errors += 1
    print_warning(f"Poll error: {poll_exc}")
    if consecutive_errors >= MAX_FOLLOW_ERRORS:
        ...
```

Also add `import httpx` to `run.py` (currently not imported directly).

The same fix applies to the follow loop in `status.py` — identical change.

**`console.py` `handle_api_error`** already handles `ConnectError` and `TimeoutException`.
After renaming `install` → `init`, update the message (covered in item #9 above).

---

## Item 11 — --json on repos list and auth status (Low)

**Affected files:**
- `cli/src/aquarco_cli/commands/repos.py` (renamed from watch.py)
- `cli/src/aquarco_cli/commands/auth.py`

### Design

**repos.py — `list_repos` command:**
```python
@app.command("list")
def list_repos(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """List all watched repositories."""
    client = GraphQLClient()
    try:
        data = client.execute(QUERY_REPOSITORIES)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    if json_output:
        console.print_json(json.dumps(data))
        return

    repos = data["repositories"]
    ... # existing table rendering
```

Add `import json` to `repos.py`.

**auth.py — `status` command:**
```python
@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
) -> None:
    """Check authentication status for Claude and GitHub."""
    client = GraphQLClient()
    try:
        claude = client.execute(QUERY_CLAUDE_AUTH_STATUS)
        github = client.execute(QUERY_GITHUB_AUTH_STATUS)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    if json_output:
        console.print_json(json.dumps({"claudeAuthStatus": claude["claudeAuthStatus"],
                                        "githubAuthStatus": github["githubAuthStatus"]}))
        return

    ... # existing table rendering
```

Add `import json` to `auth.py`.

---

## Item 5 — Smart aquarco auth (Medium)

**Affected files:** `cli/src/aquarco_cli/commands/auth.py`

### Design

Add `invoke_without_command=True` to the auth app callback. The callback checks
`ctx.invoked_subcommand` — if a subcommand was given, return immediately (normal dispatch).
If no subcommand, run the auto-detect flow.

```python
app = typer.Typer(
    help="Manage authentication for Claude and GitHub.",
    context_settings={"help_option_names": ["-h", "--help"]},
)

@app.callback(invoke_without_command=True)
def auth_callback(ctx: typer.Context) -> None:
    """Auto-detect and fix authentication for Claude and GitHub.

    Without a subcommand: checks both services and authenticates any that are missing.
    Subcommands: status, claude, github.
    """
    if ctx.invoked_subcommand is not None:
        return  # Let subcommand handle it

    client = GraphQLClient()

    # 1. Check Claude
    try:
        claude_data = client.execute(QUERY_CLAUDE_AUTH_STATUS)
        claude_ok = claude_data["claudeAuthStatus"]["authenticated"]
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    if not claude_ok:
        print_info("Claude not authenticated — starting OAuth flow...")
        ctx.invoke(claude)  # reuse existing claude() command logic

    # 2. Check GitHub
    try:
        github_data = client.execute(QUERY_GITHUB_AUTH_STATUS)
        github_ok = github_data["githubAuthStatus"]["authenticated"]
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    if not github_ok:
        print_info("GitHub not authenticated — starting device flow...")
        ctx.invoke(github)  # reuse existing github() command logic

    # 3. Show final status
    ctx.invoke(status)
```

**Key design constraint:** Using `ctx.invoke()` to call the existing `claude()`, `github()`,
and `status()` functions means all auth logic stays in one place and the callback does not
duplicate code. Typer's `ctx.invoke` calls the decorated function directly.

---

## Item 2 — --port option for aquarco init (Medium)

**Affected files:**
- `cli/src/aquarco_cli/commands/init.py`
- `cli/src/aquarco_cli/config.py`
- `cli/src/aquarco_cli/vagrant.py`
- `vagrant/Vagrantfile`

### Design

**Storage:** Port is stored in `~/.aquarco.json` (user home directory), making it persist
across CLI invocations without requiring environment variables:
```json
{"port": 9090}
```

**`config.py` changes:**
- Add `port` field to `CliConfig` (default 8080)
- On creation, load `~/.aquarco.json` if it exists and read `port`
- Override `api_url` construction to use the loaded port

```python
import json as _json
from pathlib import Path as _Path

@dataclass
class CliConfig:
    api_url: str = field(default_factory=lambda: os.environ.get("AQUARCO_API_URL", ""))
    port: int = field(default_factory=lambda: _load_saved_port())
    ...

    def __post_init__(self) -> None:
        if not self.api_url:
            self.api_url = f"http://localhost:{self.port}/api/graphql"

def _load_saved_port() -> int:
    cfg_file = _Path.home() / ".aquarco.json"
    if cfg_file.exists():
        try:
            data = _json.loads(cfg_file.read_text())
            return int(data.get("port", 8080))
        except Exception:
            pass
    return 8080
```

**`vagrant.py` changes:**
- `VagrantHelper._run()` passes `AQUARCO_PORT` env var to all subprocess calls:
```python
env = os.environ.copy()
env["AQUARCO_PORT"] = str(get_config().port)
kwargs["env"] = env
```

**`vagrant/Vagrantfile` changes:**
```ruby
# Replace hardcoded 8080 with ENV-based port
host_port = ENV.fetch("AQUARCO_PORT", "8080").to_i
config.vm.network "forwarded_port", guest: 8080, host: host_port, id: "proxy"
```
Guest port stays 8080 (Caddy always listens on 8080 inside the VM). Only the host-side
port changes.

**`init.py` changes:**
```python
@app.callback(invoke_without_command=True)
def init(
    port: int = typer.Option(8080, "--port", help="Host port to forward to Aquarco (default 8080)."),
) -> None:
    """One-command bootstrap of a working Aquarco environment."""
    # Save port to ~/.aquarco.json
    cfg_file = Path.home() / ".aquarco.json"
    cfg_file.write_text(json.dumps({"port": port}))
    reset_config()  # Force CliConfig reload with new port

    # ... rest of existing install logic unchanged
```

**Documentation assumption:** Port changes only take effect for new VMs (`vagrant up`).
Changing `--port` on an existing running VM requires `vagrant reload` (disruptive). The
CLI will print a warning note if the VM is already running when `init` is invoked.

---

## Item 13 — Add subcommands to aquarco ui (Medium)

**Affected files:**
- `cli/src/aquarco_cli/commands/ui.py`
- `docker/compose.yml` (add adminer service)

### Design

**Adminer service in compose.yml:**

The Caddyfile already has `/adminer/*` routed to `adminer:8080`. Add the service:
```yaml
adminer:
  image: adminer:4
  restart: unless-stopped
  networks:
    - aquarco
  environment:
    ADMINER_DEFAULT_SERVER: postgres
  depends_on:
    - postgres
```

**ui.py restructuring:**

```
aquarco ui           → starts web+api+postgres+caddy, opens http://localhost:<port>/
aquarco ui web       → same as bare ui (default subcommand behavior)
aquarco ui db        → starts adminer+postgres, opens http://localhost:<port>/adminer/
aquarco ui api       → starts api+postgres, shows http://localhost:<port>/api/graphql (no browser)
aquarco ui stop      → stops web+adminer (api is NOT stopped)
```

Constants:
```python
COMPOSE_DIR = "/home/agent/aquarco/docker"

WEB_SERVICES = "web api postgres caddy"
DB_SERVICES = "adminer postgres"
API_SERVICES = "api postgres"
STOP_SERVICES = "web adminer"  # api intentionally excluded per spec

def _docker_up(services: str) -> str:
    return f"cd {COMPOSE_DIR} && sudo docker compose up -d {services}"

def _docker_stop(services: str) -> str:
    return f"cd {COMPOSE_DIR} && sudo docker compose stop {services}"
```

**Callback (default — same as `web`):**
```python
@app.callback(invoke_without_command=True)
def ui(ctx: typer.Context, no_open: bool = typer.Option(False, "--no-open", help="Do not open browser")) -> None:
    """Start the Aquarco web UI (default: web + API + Postgres + Caddy)."""
    if ctx.invoked_subcommand is not None:
        return
    ctx.invoke(web, no_open=no_open)
```

**Subcommands:**
```python
@app.command()
def web(no_open: bool = typer.Option(False, "--no-open", help="Do not open browser")) -> None:
    """Start web UI (web + API + Postgres + Caddy) and open browser."""
    _ensure_running()
    _ssh_up(WEB_SERVICES)
    url = f"http://localhost:{get_config().port}/"
    print_success(f"Web UI running at {url}")
    if not no_open:
        webbrowser.open(url)

@app.command()
def db(no_open: bool = typer.Option(False, "--no-open", help="Do not open browser")) -> None:
    """Start Adminer database UI and open browser."""
    _ensure_running()
    _ssh_up(DB_SERVICES)
    url = f"http://localhost:{get_config().port}/adminer/"
    print_success(f"Adminer running at {url}")
    if not no_open:
        webbrowser.open(url)

@app.command()
def api(no_open: bool = typer.Option(False, "--no-open", help="Do not open browser")) -> None:
    """Start API service and show GraphQL playground URL."""
    _ensure_running()
    _ssh_up(API_SERVICES)
    url = f"http://localhost:{get_config().port}/api/graphql"
    print_success(f"GraphQL API running at {url}")
    if not no_open:
        webbrowser.open(url)

@app.command()
def stop() -> None:
    """Stop UI services (web + adminer). API is not stopped."""
    _ensure_running()
    _ssh_stop(STOP_SERVICES)
    print_success("UI services stopped.")
```

Where `_ensure_running()` checks `vagrant.is_running()` and exits on failure, and
`_ssh_up/_ssh_stop` are helper functions wrapping `vagrant.ssh(...)`.

---

## Item 8 — Graceful supervisor restart with drain mode (High)

This is the most complex item. It spans 4 layers: database, GraphQL API, supervisor, and CLI.

### 8a. Database Migration

**File:** `db/migrations/036_supervisor_state.sql` (and `.rollback.sql`)

```sql
-- 036_supervisor_state.sql
CREATE TABLE supervisor_state (
    key   VARCHAR(255) PRIMARY KEY,
    value TEXT         NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO supervisor_state (key, value) VALUES ('drain_mode', 'false');
```

```sql
-- 036_supervisor_state.rollback.sql
DROP TABLE IF EXISTS supervisor_state;
```

### 8b. GraphQL Schema Additions

**File:** `api/src/schema.graphql`

Add to `type Query`:
```graphql
drainStatus: DrainStatus!
```

Add to `type Mutation`:
```graphql
setDrainMode(enabled: Boolean!): DrainStatus!
```

Add new type:
```graphql
type DrainStatus {
  enabled: Boolean!
  activeAgents: Int!
  activeTasks: Int!
}
```

### 8c. GraphQL Resolvers

**File:** `api/src/resolvers/queries.ts` — add `drainStatus` resolver:
```typescript
drainStatus: async (_: unknown, __: unknown, ctx: Context) => {
  const stateRow = await ctx.db.query(
    "SELECT value FROM supervisor_state WHERE key = 'drain_mode'"
  )
  const enabled = stateRow.rows[0]?.value === 'true'

  const agentRow = await ctx.db.query(
    "SELECT COALESCE(SUM(active_count), 0) as count FROM agent_instances"
  )
  const activeAgents = Number(agentRow.rows[0].count)

  const taskRow = await ctx.db.query(
    "SELECT COUNT(*) as count FROM tasks WHERE status IN ('executing', 'queued', 'planning')"
  )
  const activeTasks = Number(taskRow.rows[0].count)

  return { enabled, activeAgents, activeTasks }
},
```

**File:** `api/src/resolvers/mutations.ts` — add `setDrainMode` resolver:
```typescript
setDrainMode: async (_: unknown, { enabled }: { enabled: boolean }, ctx: Context) => {
  await ctx.db.query(
    `INSERT INTO supervisor_state (key, value, updated_at)
     VALUES ('drain_mode', $1, NOW())
     ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = NOW()`,
    [enabled ? 'true' : 'false']
  )
  // Return current status
  const agentRow = await ctx.db.query(
    "SELECT COALESCE(SUM(active_count), 0) as count FROM agent_instances"
  )
  const activeAgents = Number(agentRow.rows[0].count)
  const taskRow = await ctx.db.query(
    "SELECT COUNT(*) as count FROM tasks WHERE status IN ('executing', 'queued', 'planning')"
  )
  const activeTasks = Number(taskRow.rows[0].count)
  return { enabled, activeAgents, activeTasks }
},
```

Also add `DrainStatus` return-type mapping in `api/src/resolvers/types.ts` if needed.

### 8d. GraphQL Client Constants

**File:** `cli/src/aquarco_cli/graphql_client.py` — add two new constants:
```python
QUERY_DRAIN_STATUS = """
query {
  drainStatus {
    enabled
    activeAgents
    activeTasks
  }
}
"""

MUTATION_SET_DRAIN_MODE = """
mutation SetDrainMode($enabled: Boolean!) {
  setDrainMode(enabled: $enabled) {
    enabled
    activeAgents
    activeTasks
  }
}
"""
```

### 8e. Supervisor Drain Mode Integration

**File:** `supervisor/python/src/aquarco_supervisor/main.py`

Add drain-mode check in `_dispatch_pending_tasks()`:
```python
async def _dispatch_pending_tasks(self) -> None:
    """Dispatch pending tasks to agents (skipped when drain mode is active)."""
    if not self._tq or not self._registry or not self._executor or not self._db:
        return

    # Check drain mode flag
    drain_row = await self._db.fetch_val(
        "SELECT value FROM supervisor_state WHERE key = 'drain_mode'"
    )
    if drain_row == 'true':
        log.info("drain_mode_active_skipping_dispatch")
        # Auto-restart when all work is complete
        active = await self._db.fetch_val(
            "SELECT COALESCE(SUM(active_count), 0) FROM agent_instances"
        )
        executing = await self._db.fetch_val(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('executing', 'queued', 'planning')"
        )
        if (active or 0) == 0 and (executing or 0) == 0:
            log.info("drain_complete_restarting")
            # Clear drain flag before restart to avoid infinite restart loop
            await self._db.execute(
                "UPDATE supervisor_state SET value = 'false', updated_at = NOW() WHERE key = 'drain_mode'"
            )
            self._handle_shutdown()  # Triggers clean exit; systemd Restart=always handles restart
        return

    # ... existing capacity/dispatch logic
```

**Race condition mitigation:** The drain flag check and the dispatch are both within the
same main loop iteration (single-threaded coroutine). No new tasks are dispatched between
the drain check and the guard return. The only window is: a task is dispatched in iteration
N, and drain is set by the user between iteration N and N+1. In that case at most one
additional task starts — acceptable for a graceful drain.

**Self-restart mechanism:** `_handle_shutdown()` sets `self._shutdown = True` and signals
`self._shutdown_event`. The main loop exits cleanly. systemd must have `Restart=always`
(or `Restart=on-success`) in the service unit to restart automatically.

### 8f. CLI update.py Changes

**File:** `cli/src/aquarco_cli/commands/update.py`

**Imports to add:**
```python
from aquarco_cli.graphql_client import (
    GraphQLClient,
    QUERY_DRAIN_STATUS,
    MUTATION_SET_DRAIN_MODE,
)
from aquarco_cli.console import handle_api_error
from rich.prompt import Prompt
```

**Full update flow:**

```python
@app.callback(invoke_without_command=True)
def update(
    dry_run: bool = typer.Option(False, "--dry-run", ...),
    skip_migrations: bool = typer.Option(False, "--skip-migrations", ...),
    skip_provision: bool = typer.Option(False, "--skip-provision", ...),
) -> None:
    """Update the VM to the latest version including Docker images."""
    vagrant = VagrantHelper()
    if not vagrant.is_running():
        print_error("VM is not running. Start it with 'aquarco init' first.")
        raise typer.Exit(code=1)

    # ... dry_run handling unchanged ...

    # Query drain status
    client = GraphQLClient()
    try:
        drain_data = client.execute(QUERY_DRAIN_STATUS)
        drain = drain_data["drainStatus"]
    except Exception as exc:
        handle_api_error(exc)
        # If API unreachable (e.g. containers down), proceed with update
        drain = None

    if drain and drain["enabled"]:
        # --- Pending restart state ---
        active = drain["activeAgents"]
        executing = drain["activeTasks"]
        if active == 0 and executing == 0:
            print_info("Planned restart is complete — no active agents. Proceeding with update...")
            # Clear drain mode
            client.execute(MUTATION_SET_DRAIN_MODE, {"enabled": False})
        else:
            console.print(
                f"[yellow]Planned restart is pending.[/yellow] "
                f"({active} agents, {executing} tasks still active)"
            )
            choice = Prompt.ask(
                "Choose action",
                choices=["keep", "now", "cancel"],
                default="keep",
                show_choices=True,
            )
            if choice == "keep":
                print_info("Keeping planned restart. Run 'aquarco update' again when idle.")
                return
            elif choice == "cancel":
                client.execute(MUTATION_SET_DRAIN_MODE, {"enabled": False})
                print_success("Planned restart cancelled. Supervisor resuming normal operation.")
                return
            # choice == "now": fall through to run update steps immediately

    elif drain and not drain["enabled"] and (drain["activeAgents"] > 0 or drain["activeTasks"] > 0):
        # --- Active work, no pending drain ---
        active = drain["activeAgents"]
        executing = drain["activeTasks"]
        print_warning(
            f"Supervisor has {active} active agent(s) working on {executing} task(s)."
        )
        choice = Prompt.ask(
            "Restart will interrupt active work. Choose",
            choices=["yes", "no", "plan"],
            default="no",
            show_choices=True,
        )
        if choice == "no":
            print_info("Update aborted.")
            return
        elif choice == "plan":
            client.execute(MUTATION_SET_DRAIN_MODE, {"enabled": True})
            print_success(
                "Drain mode enabled. Supervisor will restart automatically when all "
                "current work is complete. Run 'aquarco update' again to check status."
            )
            return
        # choice == "yes": fall through to immediate update

    # --- Run update steps ---
    _run_update_steps(vagrant, steps, skip_provision)
```

**Prompt choices:**
- **When drain is pending:** `keep` / `now` / `cancel`
- **When work is active (no drain):** `yes` / `no` / `plan`

---

## Assumptions

1. **systemd service restart policy:** The supervisor service unit already has
   `Restart=always` or `Restart=on-success`. If not, item #8's auto-restart will not work.
   The implementation agent should verify `aquarco-supervisor-python.service` and add
   `Restart=always` if missing.

2. **Adminer port conflict:** Adminer listens on port 8080 internally. The Caddyfile
   already routes `/adminer/*` to `adminer:8080`. No Caddyfile changes needed.

3. **`ctx.invoke` in auth callback:** Typer's `ctx.invoke` works for commands registered
   on the same app. The `claude()`, `github()`, and `status()` functions are
   `@app.command()` decorated and can be invoked this way.

4. **`supervisor_state` table size:** Only one row is needed for drain mode. The table
   design is generic enough to store other state flags in future without new migrations.

5. **port option and existing VM:** `aquarco init --port PORT` saves the port and starts
   a new VM. Users with running VMs who want to change the port must `vagrant halt` first.
   A warning will be printed if the VM is already running.

---

## File Change Summary

| File | Change Type | Items |
|------|-------------|-------|
| `cli/src/aquarco_cli/main.py` | Modify | 1, 6, 9 |
| `cli/src/aquarco_cli/commands/install.py` → `init.py` | Rename + modify | 1, 2, 9 |
| `cli/src/aquarco_cli/commands/update.py` | Modify | 1, 7, 8, 9 |
| `cli/src/aquarco_cli/commands/auth.py` | Modify | 1, 5, 11 |
| `cli/src/aquarco_cli/commands/watch.py` → `repos.py` | Rename + modify | 1, 6, 11 |
| `cli/src/aquarco_cli/commands/run.py` | Modify | 1, 4 |
| `cli/src/aquarco_cli/commands/status.py` | Modify | 1, 4, 10 |
| `cli/src/aquarco_cli/commands/ui.py` | Modify | 1, 12, 13 |
| `cli/src/aquarco_cli/config.py` | Modify | 2 |
| `cli/src/aquarco_cli/vagrant.py` | Modify | 2 |
| `cli/src/aquarco_cli/console.py` | Modify | 9 |
| `cli/src/aquarco_cli/graphql_client.py` | Modify | 8 |
| `cli/tests/test_commands/test_install.py` → `test_init.py` | Rename + modify | 9 |
| `cli/tests/test_commands/test_watch.py` → `test_repos.py` | Rename + modify | 6 |
| `cli/tests/test_commands/test_update.py` | Modify | 7, 8 |
| `cli/tests/test_commands/test_auth.py` | Modify | 5, 11 |
| `cli/tests/test_commands/test_ui.py` | Modify | 12, 13 |
| `cli/tests/test_main.py` | Modify | 6, 9 |
| `docker/compose.yml` | Modify | 3, 13 |
| `vagrant/Vagrantfile` | Modify | 2 |
| `api/src/schema.graphql` | Modify | 8 |
| `api/src/resolvers/queries.ts` | Modify | 8 |
| `api/src/resolvers/mutations.ts` | Modify | 8 |
| `db/migrations/036_supervisor_state.sql` | New | 8 |
| `db/migrations/036_supervisor_state.rollback.sql` | New | 8 |
| `supervisor/python/src/aquarco_supervisor/main.py` | Modify | 8 |
