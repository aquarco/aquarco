# Design: Unified Reverse Proxy Routing Layer (Caddy)

**Task:** github-issue-aquarco-2
**Issue:** https://github.com/aquarco/aquarco/issues/2
**Stage:** design
**Date:** 2026-03-31

## Summary

Add a Caddy reverse proxy as the single entry point (port 8080) for all Aquarco services, replacing the current multi-port architecture. All browser-facing traffic routes through Caddy with path-based dispatch. Internal service ports are removed from external exposure.

## Current State

| Service     | Current External Port | Access              |
|-------------|----------------------|---------------------|
| Web (Next.js) | 8080 (mapped from 3000) | Direct              |
| API (GraphQL) | 4000 (all interfaces)   | Direct              |
| Adminer     | 8081 (all interfaces)   | Direct              |
| Prometheus  | 9090 (127.0.0.1)       | Direct              |
| Grafana     | 3000 (127.0.0.1)       | Direct              |
| Loki        | 3100 (127.0.0.1)       | Direct (no browser) |
| PostgreSQL  | 5432 (127.0.0.1)       | Direct              |

## Target State

| Path                | Backend           | Routing Mode    |
|---------------------|-------------------|-----------------|
| `/`                 | `web:3000`        | Default handler |
| `/api/*`            | `api:4000`        | Strip prefix    |
| `/adminer/*`        | `adminer:8080`    | Strip prefix    |
| `/grafana/*`        | `grafana:3000`    | Keep prefix     |
| `/prometheus/*`     | `prometheus:9090` | Keep prefix     |
| `/repo/*`           | 503 placeholder   | Phase 2         |

Only two ports forwarded through Vagrant: **8080** (Caddy proxy) and **15432** (PostgreSQL for IDE tools). Plus **8085** (claude-spend, existing tool).

## Design Decisions

### D1: Caddy service placement
Caddy lives in `docker/compose.yml` (not in a separate compose file) because it is a core infrastructure service needed for all environments. It joins the `aquarco` network.

### D2: Monitoring network bridging
`compose.monitoring.yml` must add the external `aquarco_aquarco` network so Caddy (in the `aquarco` project) can reach Grafana and Prometheus. The network name follows Docker Compose convention: `{project}_{network}` = `aquarco_aquarco`.

### D3: Prometheus healthcheck update
Setting `--web.route-prefix=/prometheus` causes Prometheus to serve **all** endpoints under `/prometheus/`, including `/-/healthy`. The healthcheck must be updated to `http://localhost:9090/prometheus/-/healthy`.

### D4: Grafana healthcheck remains unchanged
Grafana's `GF_SERVER_SERVE_FROM_SUB_PATH` only affects external routing. The internal health endpoint at `localhost:3000/api/health` remains accessible.

### D5: Port 8085 (claude-spend) in Vagrantfile
The analysis identified port 8085 forwarding for the `claude-spend` tool, which is not mentioned in the issue. **Decision: Keep port 8085 forwarded.** It is an independent tool not routable through Caddy. It can be consolidated in a future task.

### D6: API debug port
`compose.dev.yml` adds `127.0.0.1:9229:9229` for Node.js inspector. This stays as-is since it is a debug-only, localhost-bound port unrelated to HTTP routing.

### D7: Loki stays internal
Loki has no browser UI. Its port binding (`127.0.0.1:3100:3100`) stays for internal scraping only. No Caddy route needed.

### D8: API port restriction
Change API port from `"4000:4000"` (all interfaces) to `"127.0.0.1:4000:4000"` (localhost only) for debug-only access. Caddy reaches the API via Docker network, not host ports.

## Detailed Changes

### File 1: `docker/caddy/Caddyfile` (NEW)

```caddyfile
{
    auto_https off
    admin 0.0.0.0:2019
}

:8080 {
    # GraphQL API - strip /api prefix
    handle_path /api/* {
        reverse_proxy api:4000
    }

    # Adminer - strip /adminer prefix
    handle_path /adminer/* {
        reverse_proxy adminer:8080
    }

    # Grafana - keep prefix (Grafana serves from subpath)
    handle /grafana/* {
        reverse_proxy grafana:3000
    }

    # Prometheus - keep prefix
    handle /prometheus/* {
        reverse_proxy prometheus:9090
    }

    # Repo previews - placeholder for Phase 2
    handle /repo/* {
        respond "Repository preview not yet available" 503
    }

    # Default: Web UI (must be last)
    handle {
        reverse_proxy web:3000
    }
}
```

**Notes:**
- `auto_https off` — no TLS in dev environment.
- `admin 0.0.0.0:2019` — Caddy admin API exposed for future Phase 2 dynamic route reconfiguration. Bound to `127.0.0.1` on host via compose port mapping.
- `handle_path` strips the matched prefix before proxying; `handle` preserves it.
- Order matters: specific paths before the catch-all `handle {}`.

### File 2: `docker/compose.yml`

Changes:
1. **Add `caddy` service** after existing services:
   ```yaml
   caddy:
     image: caddy:2-alpine
     restart: unless-stopped
     networks:
       - aquarco
     ports:
       - "8080:8080"
       - "127.0.0.1:2019:2019"
     volumes:
       - ./caddy/Caddyfile:/etc/caddy/Caddyfile:ro
       - caddy_data:/data
       - caddy_config:/config
     depends_on:
       - web
       - api
   ```

2. **Remove `web` ports section entirely** — currently `ports: ["8080:3000"]`. Caddy handles external access. Web container still listens on 3000 internally on the Docker network.

3. **Change `api` ports** — from `"4000:4000"` to `"127.0.0.1:4000:4000"` (debug-only, localhost-bound).

4. **Change `web` environment** — `NEXT_PUBLIC_API_URL` from `${NEXT_PUBLIC_API_URL:-http://localhost:4000/graphql}` to `${NEXT_PUBLIC_API_URL:-/api/graphql}`.

5. **Add volumes** — `caddy_data:` and `caddy_config:` to the top-level `volumes:` section.

### File 3: `docker/compose.dev.yml`

Changes:
1. **Remove `adminer` ports section** — currently `ports: ["0.0.0.0:8081:8080"]`. Delete the `ports` key entirely. Caddy routes `/adminer/*` to it via Docker network.

### File 4: `docker/compose.monitoring.yml`

Changes:
1. **Add external network reference** to existing networks section:
   ```yaml
   networks:
     default:
       name: aquarco-monitoring
     aquarco:
       external: true
       name: aquarco_aquarco
   ```

2. **Prometheus changes:**
   - Add `networks: [default, aquarco]`
   - Remove `ports` section (was `127.0.0.1:9090:9090`)
   - Add subpath flags to `command`:
     ```yaml
     - '--web.external-url=http://localhost:8080/prometheus'
     - '--web.route-prefix=/prometheus'
     ```
   - **Update healthcheck** to account for route prefix:
     ```yaml
     test: ["CMD-SHELL", "wget -qO- http://localhost:9090/prometheus/-/healthy || exit 1"]
     ```

3. **Grafana changes:**
   - Add `networks: [default, aquarco]`
   - Remove `ports` section (was `127.0.0.1:3000:3000`)
   - Add environment variables (merge with existing):
     ```yaml
     GF_SERVER_ROOT_URL: "http://localhost:8080/grafana/"
     GF_SERVER_SERVE_FROM_SUB_PATH: "true"
     ```

4. **Loki** — no changes. Stays on `aquarco-monitoring` network only with its existing port binding.

### File 5: `web/src/lib/apollo.tsx`

Change browser-side fallback URL on line 9:
```typescript
// Before:
? (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:4000/graphql')

// After:
? (process.env.NEXT_PUBLIC_API_URL ?? '/api/graphql')
```

The SSR path (`http://api:4000/graphql`) stays unchanged — server-side requests go directly to the API container via Docker network, not through Caddy.

### File 6: `vagrant/Vagrantfile`

Replace the port forwarding section (lines 25-33):
```ruby
# -------------------------------------------------------------------------
# Port forwarding — Aquarco system ports
# -------------------------------------------------------------------------
config.vm.network "forwarded_port", guest: 8080, host: 8080, id: "proxy"       # Caddy reverse proxy (single entry point)
config.vm.network "forwarded_port", guest: 5432, host: 15432, id: "postgres"   # PostgreSQL (system) — host 15432 to avoid local PG conflict
config.vm.network "forwarded_port", guest: 8085, host: 8085, id: "claude-spend"
```

**Removed forwards:** 4000 (api), 9090 (prometheus), 3000/13000 (grafana), 8081 (adminer).
**Kept:** 8080 (now Caddy instead of web direct), 15432→5432 (postgres), 8085 (claude-spend tool).
**Commented repo slots** (lines 39-54): Keep as-is — they become unnecessary with Phase 2 but are harmless.

## Startup Order

The `caddy` service depends on `web` and `api`. When monitoring is also started, Caddy will attempt to proxy to Grafana/Prometheus — if they're not running, Caddy returns a 502 (expected behavior, not an error). No hard dependency on monitoring stack.

## Verification Plan

1. `docker compose -f compose.yml -f compose.dev.yml config` — validates merged config
2. `docker compose -f compose.monitoring.yml config` — validates monitoring config
3. From host browser after full stack up:
   - `http://localhost:8080/` → Web UI loads
   - `http://localhost:8080/api/graphql` → GraphQL playground
   - `http://localhost:8080/adminer/` → Adminer UI
   - `http://localhost:8080/grafana/` → Grafana dashboard
   - `http://localhost:8080/prometheus/` → Prometheus UI
4. GraphQL queries work from web UI (Apollo client uses `/api/graphql`)
5. `http://localhost:8080/repo/test` → 503 placeholder response
