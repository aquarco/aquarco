# Aquarco

Sandboxed VirtualBox VM for autonomous AI agents. Agents watch GitHub
repositories for issues and PRs, run multi-stage pipelines (analyze, design,
implement, test, review), and submit pull requests — all inside an isolated VM.

## Quick Start

```bash
pip install -e cli/        # Install the aquarco CLI
aquarco init               # Bootstrap the VM (~5 min)
aquarco auth github        # Authenticate GitHub
aquarco auth claude        # Authenticate Claude
aquarco repos add https://github.com/user/repo  # Watch a repo
```

Open http://localhost:8080 or run `aquarco ui` to launch the dashboard.

## CLI Reference

The `aquarco` CLI runs on the **host** (macOS) and manages the VM via Vagrant SSH and the GraphQL API.

Install: `pip install -e cli/` (requires Python 3.10+)

| Command | Description |
|---------|-------------|
| `aquarco init` | Bootstrap the Aquarco VM (checks VirtualBox + Vagrant, runs `vagrant up`, verifies health) |
| `aquarco init --dev` | Bootstrap in dev mode: mounts the aquarco source tree into the VM |
| `aquarco backup` | Back up database and credentials to `~/.aquarco/backups/` on the host |
| `aquarco backup --no-db` | Back up credentials only |
| `aquarco backup --no-creds` | Back up database only |
| `aquarco backup -o <dir>` | Back up to a custom directory instead of the default |
| `aquarco update` | Update VM: Docker images, migrations, restart services (with drain mode support) |
| `aquarco auth` | Auto-detect unauthenticated services and run their login flows |
| `aquarco auth claude` | Authenticate Claude via OAuth PKCE flow |
| `aquarco auth github` | Authenticate GitHub via device flow |
| `aquarco auth status` | Check Claude and GitHub auth status |
| `aquarco repos add <url>` | Register a repository for autonomous watching |
| `aquarco repos list` | List all watched repositories |
| `aquarco repos remove <name>` | Remove a watched repository |
| `aquarco run <title> -r <repo>` | Create a task for agent execution |
| `aquarco status` | Dashboard overview (task counts, agents, cost) |
| `aquarco status <id>` | Detailed task status with stage history |
| `aquarco ui web` | Start web UI and open dashboard (default) |
| `aquarco ui db` | Start Adminer and open database admin |
| `aquarco ui api` | Open GraphQL playground |
| `aquarco ui stop` | Stop all UI services (web, adminer) |
