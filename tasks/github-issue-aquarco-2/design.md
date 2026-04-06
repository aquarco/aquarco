# Design: Unified Reverse Proxy Routing Layer (Phase 1)

**Task:** github-issue-aquarco-2
**Issue:** https://github.com/aquarco/aquarco/issues/2
**Date:** 2026-03-31

## Summary

Add a Caddy reverse proxy as a single-port (`:8080`) entry point with path-based routing to all services. Replace the current 6+ individually forwarded VM ports with unified routing through Caddy. This is Phase 1 only (static proxy config).

## Current State

| Service | Current Port Binding | Access From Host |
|---------|---------------------|-----------------|
| web (Next.js) | `8080:3000` (all interfaces) | `localhost:8080` |
| api (GraphQL) | `4000:4000` (all interfaces) | `localhost:4000` |
| postgres | `127.0.0.1:5432:5432` | `localhost:15432` (via Vagrant) |
| adminer | `0.0.0.0:8081:8080` | `localhost:8081` |
| prometheus | `127.0.0.1:9090:9090` | `localhost:9090` |
| grafana | `127.0.0.1:3000:3000` | `localhost:13000` |
| claude-spend | `8085:8085` | `localhost:8085` |

**Network topology:**
- Main stack (`compose.yml` + `compose.dev.yml`): project name `aquarco`, network `aquarco_aquarco`
- Monitoring stack (`compose.monitoring.yml`): project name `aquarco-monitoring`, network `aquarco-monitoring`
- The two networks are currently **isolated** — no cross-project routing.

## Target State

Single port `:8080` via Caddy reverse proxy:

```
:8080
  /              → web:3000        (Next.js)
  /api/*         → api:4000        (GraphQL, path stripped)
  /adminer/*     → adminer:8080    (path stripped)
  /grafana/*     → grafana:3000    (subpath-aware, prefix kept)
  /prometheus/*  → prometheus:9090 (subpath-aware, prefix kept)
  /repo/*        → 503 placeholder (Phase 2)
```

Vagrant forwards only: `8080` (proxy) + `15432` (postgres) + `8085` (claude-spend).

## Design Decisions

### D1: Caddy service placement
Caddy lives in `compose.yml` (main stack) because it is the primary entry point for all services. It joins both `aquarco` (native) and `aquarco-monitoring` (external) networks.

### D2: Monitoring network bridging
Rather than adding Caddy to the monitoring network from compose.yml (which would require the monitoring stack to be up first), we bridge monitoring services **into** the `aquarco` network. This is done by declaring `aquarco_aquarco` as an external network in `compose.monitoring.yml` and adding Grafana + Prometheus to it. This way Caddy can reach them on the `aquarco` network regardless of startup order.

### D3: Port removal strategy
- `web`: Remove `ports` entirely (Caddy proxies to `web:3000` internally)
- `api`: Change to `127.0.0.1:4000:4000` (keep for direct debug, not exposed via Vagrant)
- `adminer`: Remove `ports` entirely
- `grafana`: Remove `ports` entirely
- `prometheus`: Remove `ports` entirely
- `loki`: Keep `127.0.0.1:3100:3100` (internal, no browser UI needed, not routed through Caddy)

### D4: Prometheus healthcheck after route-prefix
Adding `--web.route-prefix=/prometheus` makes Prometheus serve all paths under `/prometheus/`. However, the healthcheck uses `wget -qO- http://localhost:9090/-/healthy`. With `--web.route-prefix=/prometheus`, the health endpoint moves to `http://localhost:9090/prometheus/-/healthy`. The healthcheck must be updated.

### D5: Grafana healthcheck after subpath
Grafana's health endpoint at `/api/health` becomes `/grafana/api/health` when `GF_SERVER_SERVE_FROM_SUB_PATH=true`. The healthcheck must be updated to `http://localhost:3000/grafana/api/health`.

### D6: claude-spend port (8085)
The Vagrantfile currently forwards port 8085 for `claude-spend` tool. This is **not** a Docker service — it's a host-level tool. Keep this port forward in the Vagrantfile as-is. It is outside Caddy's scope.

### D7: Apollo client URL change
The browser-side Apollo client currently uses `NEXT_PUBLIC_API_URL` defaulting to `http://localhost:4000/graphql`. After Caddy, browser requests go to `/api/graphql` (relative path, through proxy). SSR requests continue using `http://api:4000/graphql` (direct Docker network). The env var `NEXT_PUBLIC_API_URL` in compose.yml changes to `/api/graphql`.

### D8: Adminer network membership
Adminer is defined in `compose.dev.yml` which extends `compose.yml` (same project name `aquarco`). It inherits the `aquarco` network via `networks: [aquarco]`. Caddy can reach it at `adminer:8080`.

## Implementation Steps

### Step 1: Create `docker/caddy/Caddyfile`
New file. Static Caddyfile with routing rules for all services.

**Key details:**
- `auto_https off` (no TLS in dev)
- `admin 0.0.0.0:2019` (Caddy admin API for future Phase 2)
- `handle_path` for `/api/*` and `/adminer/*` (strips prefix)
- `handle` for `/grafana/*` and `/prometheus/*` (keeps prefix — services are subpath-aware)
- `handle /repo/*` returns 503 placeholder
- Default `handle` catches all → `web:3000`

### Step 2: Modify `docker/compose.yml`
1. Add `caddy` service with `image: caddy:2-alpine`, ports `8080:8080` and `127.0.0.1:2019:2019`
2. Add volumes: `./caddy/Caddyfile:/etc/caddy/Caddyfile:ro`, `caddy_data:/data`, `caddy_config:/config`
3. Add `caddy_data` and `caddy_config` to top-level `volumes`
4. Caddy `depends_on: [web, api]`
5. Caddy joins `networks: [aquarco]`
6. **Remove** `web` service `ports` section entirely
7. **Change** `api` service ports from `"4000:4000"` to `"127.0.0.1:4000:4000"`
8. **Change** `web` environment `NEXT_PUBLIC_API_URL` to `/api/graphql`

### Step 3: Modify `docker/compose.dev.yml`
1. Remove `adminer` service `ports` section entirely
2. Add adminer to `networks: [aquarco]` (explicit, though it inherits from project default — being explicit ensures Caddy routing works)

### Step 4: Modify `docker/compose.monitoring.yml`
1. Add `aquarco` network declaration as external:
   ```yaml
   networks:
     default:
       name: aquarco-monitoring
     aquarco:
       external: true
       name: aquarco_aquarco
   ```
2. **Grafana** changes:
   - Add `networks: [default, aquarco]`
   - Remove `ports` section
   - Add environment vars: `GF_SERVER_ROOT_URL: "http://localhost:8080/grafana/"`, `GF_SERVER_SERVE_FROM_SUB_PATH: "true"`
   - Update healthcheck to `http://localhost:3000/grafana/api/health`
3. **Prometheus** changes:
   - Add `networks: [default, aquarco]`
   - Remove `ports` section
   - Add command args: `--web.external-url=http://localhost:8080/prometheus`, `--web.route-prefix=/prometheus`
   - Update healthcheck to `http://localhost:9090/prometheus/-/healthy`
4. **Loki** stays unchanged (internal only, no Caddy routing)

### Step 5: Modify `web/src/lib/apollo.tsx`
Change the browser-side fallback URL:
```typescript
const uri =
  typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_API_URL ?? '/api/graphql')
    : 'http://api:4000/graphql'
```
Only change: default from `'http://localhost:4000/graphql'` to `'/api/graphql'`.

### Step 6: Modify `vagrant/Vagrantfile`
Remove port forwards for: `api` (4000), `prometheus` (9090), `grafana` (3000→13000), `adminer` (8081).
Keep: `webui` (8080→8080, rename id to `"proxy"`), `postgres` (5432→15432), `claude-spend` (8085).

Final port forwards:
```ruby
config.vm.network "forwarded_port", guest: 8080, host: 8080, id: "proxy"
config.vm.network "forwarded_port", guest: 5432, host: 15432, id: "postgres"
config.vm.network "forwarded_port", guest: 8085, host: 8085, id: "claude-spend"
```

Commented-out repo slots remain as-is (Phase 2 reference).

## Assumptions

1. The monitoring stack is always started **after** the main stack (so `aquarco_aquarco` network exists when monitoring services try to join it). If monitoring starts first, Grafana/Prometheus will fail to connect to the external network. This is acceptable for the current dev workflow.
2. The `claude-spend` tool on port 8085 is not a Docker service and cannot be routed through Caddy.
3. The Node.js inspector port (`127.0.0.1:9229`) in compose.dev.yml stays as-is — it's a debug protocol, not HTTP, and shouldn't go through Caddy.

## Verification Plan

1. `docker compose -f compose.yml -f compose.dev.yml config` validates without errors
2. `docker compose -f compose.monitoring.yml config` validates without errors
3. `http://localhost:8080/` → Next.js web UI loads
4. `http://localhost:8080/api/graphql` → GraphQL playground responds
5. `http://localhost:8080/adminer/` → Adminer UI loads
6. `http://localhost:8080/grafana/` → Grafana dashboard loads
7. `http://localhost:8080/prometheus/` → Prometheus UI loads
8. GraphQL queries from web UI work (Apollo client uses `/api/graphql`)
9. Only ports 8080, 15432, 8085 forwarded from Vagrant
