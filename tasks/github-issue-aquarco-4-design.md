# Design: Production Build (Issue #4)

**Task ID:** github-issue-aquarco-4  
**Stage:** Design  
**Date:** 2026-04-04  
**Status:** Complete

---

## 1. Problem Summary

Aquarco needs a production distribution path via Homebrew. The production binary differs from the dev CLI in several key ways:

- No source file sync / hot reload (no `vboxsf` synced folder, no volume mounts)
- No `aquarco update` command (self-update is disabled for public binaries)
- Docker services run from pinned pre-built images, not local `build:` contexts
- Update flow (when run manually by operators) must: back up credentials first, hard-fail on step errors, and trigger rollback on failure
- OS packages must be updated as part of an operator-initiated update
- Vagrant + VirtualBox are still required (installed as Homebrew cask dependencies)

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  macOS Host (Homebrew)                                      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  aquarco CLI binary                                  │   │
│  │  BUILD_TYPE = "production"  (patched at install)     │   │
│  │  aquarco update → disabled (exits 1 with message)   │   │
│  └──────────────────────────────────────────────────────┘   │
│  Dependencies: vagrant (cask), virtualbox (cask)            │
└─────────────────────────────────────────────────────────────┘
        │ vagrant up / vagrant ssh
        ▼
┌─────────────────────────────────────────────────────────────┐
│  VirtualBox VM (Ubuntu 24.04)                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  provision.sh (production mode)                      │   │
│  │  • No vboxsf sync, no source mounts                  │   │
│  │  • aquarco-stack uses compose.prod.yml overlay       │   │
│  └──────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Docker Compose (compose.yml + compose.prod.yml)     │   │
│  │  • postgres:16-alpine                                │   │
│  │  • ghcr.io/…/migrations:X.Y.Z  (pinned)             │   │
│  │  • ghcr.io/…/api:X.Y.Z         (pinned)             │   │
│  │  • ghcr.io/…/web:X.Y.Z         (pinned)             │   │
│  │  • caddy:2.8-alpine                                  │   │
│  │  • adminer disabled via profiles                     │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Design Decisions

### 3.1 Build-type constant

**Decision:** Add `cli/src/aquarco_cli/_build.py` containing `BUILD_TYPE = "development"`.  
The Homebrew formula patches this file with `inreplace` before `pip install`:
```ruby
inreplace "cli/src/aquarco_cli/_build.py",
          'BUILD_TYPE = "development"',
          'BUILD_TYPE = "production"'
```

**Rationale:** Using `inreplace` is the standard Homebrew pattern for baking constants. An environment variable approach would require end-users to set it to restore functionality. A pyproject extra would add tooling complexity. A plain string replace in a dedicated `_build.py` file is the most transparent.

**Assumption:** We do not need a runtime override (e.g., `AQUARCO_BUILD_TYPE=development aquarco update`) for production binaries.

### 3.2 Disabling `aquarco update` in production

In `update.py`'s `update()` callback, add a guard at the very top:
```python
from aquarco_cli._build import BUILD_TYPE

if BUILD_TYPE == "production":
    print_error("aquarco update is not available in the public release.")
    raise typer.Exit(code=1)
```

The command is still **registered** in `main.py` so `--help` remains informative. Only execution is blocked.

### 3.3 Credential backup

`vagrant/scripts/backup-credentials.sh`:
- **GitHub token**: read from `~/.config/gh/hosts.yml` (where `gh auth login` stores it)
- **Claude API key**: read from `~/.claude/.credentials.json` (Claude Code CLI default path)
- Backup destination: `/var/lib/aquarco/backups/YYYY-MM-DDTHH-MM-SS/`
- File permissions: `chmod 700` on backup dir, `chmod 600` on each file
- Writes `manifest.json` with `{"timestamp": "...", "files": [...], "missing": [...]}`
- Prints the backup directory path to stdout on success
- Exits non-zero only if **both** credentials are missing (partial backup still proceeds)
- Maximum 10 backups retained (oldest pruned automatically)

### 3.4 Hard-fail update flow with rollback

Replace the warn-and-continue behaviour in `_run_update_steps` with **hard-fail**:
```python
except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
    print_error(f"Step failed: {name} — {exc}")
    if backup_dir:
        print_warning(f"Initiating rollback from backup: {backup_dir}")
        _run_rollback(vagrant, backup_dir)
    raise typer.Exit(code=1) from exc
```

The `backup_dir` is captured from the backup script's stdout before any update steps begin.

**Rollback triggers** (per the issue spec):
- DB migration failure
- `docker compose up -d` failure  
- Provision script failure
- Systemd service restart failure

**Non-rollback steps:** `docker compose pull` failure is retryable and non-destructive; it logs an error and aborts but does **not** invoke the rollback script.

### 3.5 OS package update step

Prepend to `STEPS` list:
```python
("Update OS packages", "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq"),
```

This runs first so that Docker and other system dependencies are at their latest versions before container operations begin.

**Risk mitigation:** If Docker itself is upgraded, the daemon restarts automatically via the postinst hook. Compose services may briefly go down. This is acceptable because the update flow continues with `docker compose up -d` which re-starts services.

### 3.6 Production Docker Compose overlay

`docker/compose.prod.yml` is used as a second `-f` argument:
```
docker compose -f compose.yml -f compose.prod.yml up -d
```

The overlay:
- Replaces `build:` with explicit `image:` for `api`, `web`, and `migrations` services
- Removes dev-only volume mounts (source code, tsconfig, etc.)
- Changes commands: `web` → `npm run start`; `api` → `node dist/server.js`
- Sets `NODE_ENV: production` and removes `CHOKIDAR_USEPOLLING`, `WATCHPACK_POLLING`
- Disables `adminer` via `profiles: ["dev"]` (it is only activated if `--profile dev` is passed)

### 3.7 Pinned image versions (`docker/versions.env`)

Single source-of-truth file read by both `compose.prod.yml` and the release tooling:
```
AQUARCO_API_VERSION=0.1.0
AQUARCO_WEB_VERSION=0.1.0
AQUARCO_MIGRATIONS_VERSION=0.1.0
AQUARCO_CADDY_VERSION=2.8-alpine
AQUARCO_POSTGRES_VERSION=16-alpine
```

`compose.prod.yml` references `${AQUARCO_API_VERSION}` etc. The `aquarco-stack.service` systemd unit sources this file before invoking Compose.

**Note:** No CI/CD release tooling is implemented in this issue — `versions.env` establishes the convention; a release pipeline (future issue) will bump it.

### 3.8 Adminer conflict resolution

`compose.yml` has a minimal `adminer` definition; `compose.dev.yml` adds `ADMINER_DESIGN`. `compose.prod.yml` disables adminer via `profiles: ["dev"]`. No changes to `compose.yml` or `compose.dev.yml` are needed.

### 3.9 Homebrew formula structure

`homebrew/aquarco.rb`:
- `url` → GitHub release tarball of the repo (placeholder SHA during development)
- `depends_on cask: "vagrant"` and `depends_on cask: "virtualbox"`
- `depends_on "python@3.11"` (Python build tool)
- Uses `virtualenv_install_with_resources` to install the `cli/` Python package
- Before install, uses `inreplace` to patch `_build.py` to `"production"`
- Installs a single `aquarco` binary into `bin/`

### 3.10 Rollback script

`vagrant/scripts/rollback.sh`:
- Accepts `--backup-dir <path>`
- Stops running Docker services: `docker compose -f … down`
- Restores credentials from backup dir to their original locations
- Restarts Docker services: `docker compose up -d`
- Verifies that postgres and api health checks pass within 60 s
- Exits non-zero on verification failure (so the caller knows rollback also failed)

**DB restoration:** A pre-update DB dump is a future enhancement (noted as assumption below). The rollback script restores credentials and services but does **not** restore the database in this iteration.

**Assumption:** DB rollback (restoring from dump) is deferred. The migration step uses `yoyo`, which supports rollback natively, but wiring `yoyo rollback` into the automated flow adds significant complexity. For this iteration, the rollback script restores services/credentials only. A DB dump before migration is left for a future issue.

---

## 4. Files to Create / Modify

### New files
| Path | Purpose |
|------|---------|
| `cli/src/aquarco_cli/_build.py` | Build-type constant (`BUILD_TYPE = "development"`) |
| `homebrew/aquarco.rb` | Homebrew formula for macOS distribution |
| `vagrant/scripts/backup-credentials.sh` | Pre-update credential backup |
| `vagrant/scripts/rollback.sh` | Post-failure service + credential restore |
| `docker/compose.prod.yml` | Production Compose overlay (pinned images, no source mounts) |
| `docker/versions.env` | Single source of truth for pinned image versions |

### Modified files
| Path | Change |
|------|--------|
| `cli/src/aquarco_cli/__init__.py` | Import and re-export `BUILD_TYPE` from `_build.py` |
| `cli/src/aquarco_cli/commands/update.py` | Production guard, hard-fail, credential backup, rollback, OS update step |
| `vagrant/scripts/provision.sh` | Source `versions.env`; switch aquarco-stack service to use `compose.prod.yml` overlay |

---

## 5. Detailed Implementation Specifications

### 5.1 `cli/src/aquarco_cli/_build.py`

```python
"""Build-type constant — patched to 'production' by the Homebrew formula."""

BUILD_TYPE = "development"
```

### 5.2 `cli/src/aquarco_cli/__init__.py`

```python
"""Aquarco CLI — manage your Aquarco VM from the host."""

from aquarco_cli._build import BUILD_TYPE as BUILD_TYPE  # noqa: F401

__version__ = "0.1.0"
```

### 5.3 `cli/src/aquarco_cli/commands/update.py` — key changes

**At top of `update()` callback:**
```python
from aquarco_cli._build import BUILD_TYPE
if BUILD_TYPE == "production":
    print_error("aquarco update is not available for public Homebrew installs.")
    print_info("To update, reinstall via Homebrew: brew upgrade aquarco")
    raise typer.Exit(code=1)
```

**New STEPS prepended (OS update):**
```python
STEPS = [
    ("Update OS packages",
     "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq "
     "&& sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq"),
    # … existing steps …
]
```

**`_run_update_steps` signature change:**
```python
def _run_update_steps(
    vagrant: VagrantHelper,
    steps: list[tuple[str, str]],
    skip_provision: bool,
    backup_dir: str | None = None,   # NEW
) -> None:
```

**Step error handling (hard-fail):**
```python
except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
    print_error(f"Step failed: {name} — {exc}")
    if backup_dir:
        print_warning(f"Initiating rollback from backup: {backup_dir}")
        _run_rollback(vagrant, backup_dir)
    raise typer.Exit(code=1)
```

**New `_run_rollback` helper:**
```python
def _run_rollback(vagrant: VagrantHelper, backup_dir: str) -> None:
    try:
        vagrant.ssh(
            f"bash /home/agent/aquarco/vagrant/scripts/rollback.sh --backup-dir {backup_dir}",
            stream=True,
        )
        print_success("Rollback completed.")
    except Exception as exc:
        print_error(f"Rollback also failed: {exc}. Manual intervention required.")
```

**Credential backup before steps:**
```python
print_info("Backing up credentials...")
backup_dir: str | None = None
try:
    result = vagrant.ssh(
        "bash /home/agent/aquarco/vagrant/scripts/backup-credentials.sh",
        stream=False,
    )
    backup_dir = result.stdout.strip().splitlines()[-1] if result.stdout else None
    if backup_dir:
        print_success(f"Credentials backed up to {backup_dir}")
except Exception as exc:
    print_warning(f"Credential backup failed: {exc}. Proceeding without backup.")
```

### 5.4 `vagrant/scripts/backup-credentials.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

BACKUP_ROOT="/var/lib/aquarco/backups"
TIMESTAMP="$(date -u +%Y-%m-%dT%H-%M-%S)"
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

BACKED_UP=()
MISSING=()

# Back up GitHub token
GH_HOSTS="${HOME}/.config/gh/hosts.yml"
if [[ -f "${GH_HOSTS}" ]]; then
    cp "${GH_HOSTS}" "${BACKUP_DIR}/gh-hosts.yml"
    chmod 600 "${BACKUP_DIR}/gh-hosts.yml"
    BACKED_UP+=("gh-hosts.yml")
else
    MISSING+=("GitHub token (~/.config/gh/hosts.yml not found)")
fi

# Back up Claude credentials
CLAUDE_CREDS="${HOME}/.claude/.credentials.json"
if [[ -f "${CLAUDE_CREDS}" ]]; then
    cp "${CLAUDE_CREDS}" "${BACKUP_DIR}/claude-credentials.json"
    chmod 600 "${BACKUP_DIR}/claude-credentials.json"
    BACKED_UP+=("claude-credentials.json")
else
    MISSING+=("Claude API key (~/.claude/.credentials.json not found)")
fi

# Write manifest
cat > "${BACKUP_DIR}/manifest.json" <<MANIFEST
{
  "timestamp": "${TIMESTAMP}",
  "backed_up": $(printf '%s\n' "${BACKED_UP[@]}" | jq -R . | jq -s .),
  "missing": $(printf '%s\n' "${MISSING[@]}" | jq -R . | jq -s .)
}
MANIFEST
chmod 600 "${BACKUP_DIR}/manifest.json"

# Prune old backups (keep 10)
find "${BACKUP_ROOT}" -maxdepth 1 -mindepth 1 -type d \
    | sort | head -n -10 | xargs -r rm -rf

# Fail only if BOTH are missing
if [[ ${#MISSING[@]} -ge 2 ]]; then
    echo "ERROR: No credentials found to back up." >&2
    exit 1
fi

echo "${BACKUP_DIR}"
```

### 5.5 `vagrant/scripts/rollback.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "${BACKUP_DIR}" || ! -d "${BACKUP_DIR}" ]]; then
    echo "ERROR: --backup-dir must point to a valid backup directory" >&2
    exit 1
fi

COMPOSE_DIR="/home/agent/aquarco/docker"

echo "[rollback] Stopping Docker services..."
cd "${COMPOSE_DIR}"
sudo docker compose down || true

echo "[rollback] Restoring credentials..."

GH_HOSTS_BACKUP="${BACKUP_DIR}/gh-hosts.yml"
if [[ -f "${GH_HOSTS_BACKUP}" ]]; then
    mkdir -p "${HOME}/.config/gh"
    cp "${GH_HOSTS_BACKUP}" "${HOME}/.config/gh/hosts.yml"
    chmod 600 "${HOME}/.config/gh/hosts.yml"
    echo "[rollback] GitHub token restored."
fi

CLAUDE_BACKUP="${BACKUP_DIR}/claude-credentials.json"
if [[ -f "${CLAUDE_BACKUP}" ]]; then
    mkdir -p "${HOME}/.claude"
    cp "${CLAUDE_BACKUP}" "${HOME}/.claude/.credentials.json"
    chmod 600 "${HOME}/.claude/.credentials.json"
    echo "[rollback] Claude credentials restored."
fi

echo "[rollback] Restarting Docker services..."
sudo docker compose up -d

echo "[rollback] Waiting for health checks (60 s)..."
WAIT=0
until sudo docker compose ps --format json \
      | jq -e '[.[] | select(.Health != "" and .Health != "healthy")] | length == 0' \
      > /dev/null 2>&1; do
    sleep 5
    WAIT=$((WAIT + 5))
    if [[ ${WAIT} -ge 60 ]]; then
        echo "[rollback] ERROR: Services did not become healthy within 60 s" >&2
        exit 1
    fi
done

echo "[rollback] Rollback complete."
```

### 5.6 `docker/versions.env`

```dotenv
# Pinned image versions — single source of truth for production builds
# Bump these when releasing a new version

AQUARCO_POSTGRES_VERSION=16-alpine
AQUARCO_CADDY_VERSION=2.8-alpine
AQUARCO_API_VERSION=0.1.0
AQUARCO_WEB_VERSION=0.1.0
AQUARCO_MIGRATIONS_VERSION=0.1.0

# Registry prefix for aquarco images
AQUARCO_REGISTRY=ghcr.io/borissuska/aquarco
```

### 5.7 `docker/compose.prod.yml`

```yaml
# Production overlay — use alongside compose.yml:
#   docker compose -f compose.yml -f compose.prod.yml up -d
#
# Assumptions:
#   - docker/versions.env is sourced before running compose (via systemd EnvironmentFile)
#   - Pre-built images exist in AQUARCO_REGISTRY for the current versions

name: aquarco

services:

  # Migrations: use pre-built image, no source volume mount
  migrations:
    build: !reset null
    image: ${AQUARCO_REGISTRY}/migrations:${AQUARCO_MIGRATIONS_VERSION}
    volumes: []

  # API: pre-built image, production command, no source mounts, no debug env
  api:
    build: !reset null
    image: ${AQUARCO_REGISTRY}/api:${AQUARCO_API_VERSION}
    command: ["node", "dist/server.js"]
    volumes:
      - ${AGENT_SSH_DIR:-/home/agent/.ssh}:/agent-ssh
      - ${REPOS_BASE:-/home/agent/repos}:/repos
      - /var/lib/aquarco/claude-ipc:/claude-ipc
    environment:
      NODE_ENV: production
      CHOKIDAR_USEPOLLING: ""
      DEBUG: ""

  # Web: pre-built image, production Next.js start, no source mounts
  web:
    build: !reset null
    image: ${AQUARCO_REGISTRY}/web:${AQUARCO_WEB_VERSION}
    command: ["npm", "run", "start"]
    volumes: []
    environment:
      NODE_ENV: production
      WATCHPACK_POLLING: ""

  # Disable adminer in production (enable via: docker compose --profile dev up adminer)
  adminer:
    profiles:
      - dev
```

### 5.8 `vagrant/scripts/provision.sh` — changes required

Two targeted changes (minimal diff):

1. **After the `aquarco-stack.service` unit is written** (around line 297), update the `ExecStart` to source `versions.env` and use the production compose overlay:

```bash
# Replace the ExecStart line in the heredoc:
ExecStart=/bin/sh -c 'set -a && . /home/agent/aquarco/docker/versions.env && set +a \
  && /usr/bin/docker compose -f compose.yml -f compose.prod.yml up -d'
ExecStop=/usr/bin/docker compose down
```

This change means the dev workflow (Vagrant `vagrant up`) automatically uses the production overlay. Developers who want the dev compose should use `compose.dev.yml` manually.

**Assumption:** Vagrant dev environments also use `compose.prod.yml` because source code is synced via `vboxsf` at the host level — hot reload still works through the sync, not Docker volume mounts. The production overlay removes internal Docker volume source mounts, but the source files are present on the VM filesystem (at `/home/agent/aquarco/`). Therefore, in production, there are no source files at all; in dev, they exist via `vboxsf` but are not mounted into containers.

**Correction to assumption:** The production binary ships with pre-built images. Hot reload in development relies on Docker volume source mounts. If we use `compose.prod.yml` in development, hot reload is broken. Therefore, `provision.sh` should continue to use only `compose.yml` for development, and `compose.prod.yml` is only activated in a true production install.

**Revised decision:** Introduce a `AQUARCO_ENV` environment variable (`development` | `production`) to `provision.sh`. The Vagrantfile passes no override (defaults to `development`). A future production provision mechanism sets `AQUARCO_ENV=production`. The `aquarco-stack.service` heredoc becomes:

```bash
ExecStart=/bin/sh -c 'COMPOSE_FILES="-f compose.yml"; \
  [ "$(cat /etc/aquarco/env 2>/dev/null)" = "production" ] && \
  { set -a && . /home/agent/aquarco/docker/versions.env && set +a && \
    COMPOSE_FILES="$COMPOSE_FILES -f compose.prod.yml"; }; \
  /usr/bin/docker compose $COMPOSE_FILES up -d'
```

Where `/etc/aquarco/env` contains `development` (default) or `production` (written by the production provisioner).

### 5.9 `homebrew/aquarco.rb`

```ruby
class Aquarco < Formula
  desc "Aquarco CLI — manage your Aquarco VM from the host"
  homepage "https://github.com/borissuska/aquarco"
  url "https://github.com/borissuska/aquarco/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256"
  license "MIT"
  version "0.1.0"

  # Aquarco requires Vagrant and VirtualBox on the host
  depends_on "python@3.11"
  depends_on cask: "vagrant"
  depends_on cask: "virtualbox"

  def install
    # Patch the build-type constant before installation
    inreplace "cli/src/aquarco_cli/_build.py",
              'BUILD_TYPE = "development"',
              'BUILD_TYPE = "production"'

    # Install the CLI Python package into a virtualenv
    venv = virtualenv_create(libexec, "python3.11")
    venv.pip_install_and_link buildpath/"cli"
  end

  test do
    assert_match "aquarco", shell_output("#{bin}/aquarco --version")
    # Verify update command is disabled in production builds
    output = shell_output("#{bin}/aquarco update 2>&1", 1)
    assert_match "not available", output
  end
end
```

---

## 6. Open Questions / Assumptions

| # | Assumption | Impact if wrong |
|---|------------|-----------------|
| A1 | DB rollback (yoyo rollback) is out of scope for this iteration | Partial rollback — services/credentials restored but DB state may be post-migration |
| A2 | Dev Vagrant environments use `compose.yml` only; production uses `compose.yml + compose.prod.yml` discriminated by `/etc/aquarco/env` | If not, hot reload breaks in dev OR production uses dev compose |
| A3 | Pre-built production images are published to `ghcr.io/borissuska/aquarco/{api,web,migrations}` before `compose.prod.yml` is used in a real deployment | `docker compose up` fails on image pull |
| A4 | The production CLI tarball URL and SHA256 in the formula are placeholders; real values filled at release time | Formula cannot be installed until real release exists |
| A5 | Claude credentials live at `~/.claude/.credentials.json` on the VM | Backup script misses Claude credentials |
| A6 | The Homebrew formula update strategy (how the formula itself is updated per release) is TBD per the issue | Homebrew users cannot get auto-updates; this is acceptable for the initial design |

---

## 7. Acceptance Criteria

See structured output section below.
