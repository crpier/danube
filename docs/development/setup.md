# Development Environment Setup

## Prerequisites

- Python 3.14+
- UV package manager
- Git
- Podman for runner integration tests
- [Bun](https://bun.sh) 1.3+ only if working on the frontend

## Initial Setup

```bash
git clone https://github.com/yourorg/danube.git
cd danube
uv sync
uv run python --version
```

## Project Structure

Flat root layout: the `danube` package lives at the repo root.

```text
danube/
├── danube/
│   ├── __init__.py
│   ├── master.py
│   ├── api/
│   ├── orchestrator/
│   ├── runner/
│   ├── db/
│   ├── blueprint/
│   ├── security/
│   └── sdk/
├── frontend/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/
├── pyproject.toml
└── uv.lock
```

Most of the package subdirectories above are placeholders for later issues; the
bootstrap ships `__init__.py` and `master.py`.

## Local Configuration

Create a local config:

```bash
mkdir -p ~/.config/danube
cat > ~/.config/danube/danube.toml <<EOF
[server]
bind_address = "127.0.0.1:8080"
rpc_address = "127.0.0.1:9000"
data_dir = "./data"

[config_repo]
url = "file:///tmp/danube-blueprint-test"
branch = "main"
sync_interval = "10s"

[runner]
type = "local"
runtime = "podman"
max_concurrent_jobs = 2

[logging]
level = "debug"
format = "text"
EOF
```

## Test Blueprint Repository

```bash
mkdir -p /tmp/danube-blueprint-test
cd /tmp/danube-blueprint-test
git init
mkdir pipelines
```

`config.json`:

```json
{
  "apiVersion": "danube.dev/v1",
  "kind": "Config",
  "metadata": {"name": "global"},
  "spec": {
    "retention": {
      "logs_days": 7,
      "artifacts_days": 7,
      "registry_images_days": 7,
      "workspaces_days": 0
    },
    "networking": {
      "default_deny_egress": true,
      "egress_allowlist": ["registry.npmjs.org"]
    }
  }
}
```

`users.json`:

```json
[
  {
    "apiVersion": "danube.dev/v1",
    "kind": "User",
    "metadata": {"name": "dev"},
    "spec": {
      "email": "dev@localhost",
      "password_hash": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5NU2FcbZqd7rO"
    }
  }
]
```

`teams.json`:

```json
[
  {
    "apiVersion": "danube.dev/v1",
    "kind": "Team",
    "metadata": {"name": "dev-team"},
    "spec": {
      "members": ["dev@localhost"],
      "global_admin": true
    }
  }
]
```

Commit:

```bash
git add .
git commit -m "Initial test blueprint"
```

## Running Danube Locally

```bash
uv run python -m danube.master --config ~/.config/danube/danube.toml
```

Or:

```bash
export DANUBE_LOG_LEVEL=debug
export DANUBE_DATA_DIR=./data
uv run python -m danube.master
```

Open http://localhost:8080.

## Podman for Development

Danube's first runner target is rootless Podman through the Podman API.

```bash
podman info
podman system service --time=0 unix://$XDG_RUNTIME_DIR/podman/podman.sock
```

Integration tests that create containers should be skipped unless rootless Podman and the Podman API socket are available.

## Code Quality

Run from the repo root:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## Testing

```bash
uv run python -m snektest tests/
uv run python -m snektest tests/unit/
uv run python -m snektest tests/integration/
```

There is no CI yet; this gate is run manually until Danube can run its own
pipelines. See [Testing Guide](./testing.md).

## Common Development Tasks

### Add API Endpoint

1. Define route in `danube/api/`.
2. Add Pydantic models.
3. Add handler tests.
4. Update architecture/config docs if behavior changes.

### Add Runner Capability

1. Extend runner interface.
2. Implement in `LocalContainerRunner`.
3. Add unit tests with mocked Podman adapter.
4. Add integration tests guarded by runtime availability.
5. Update execution/security/networking docs.

### Add Blueprint Option

1. Update Blueprint schema.
2. Update parser and validation.
3. Add validation tests.
4. Update `docs/configuration/blueprint-reference.md`.

## Frontend Development

The frontend is a small TypeScript SPA built with [Bun](https://bun.sh) (no
runtime framework dependency). It lives in `frontend/` and is served by the
Master at `/`.

```bash
cd frontend
bun install              # install dev deps (TypeScript) from bun.lock
bun run lint             # type-check only (tsc --noEmit); this is the CI "lint"
bun run build            # bundle + minify into frontend/dist/
```

`bun run build` (which runs `scripts/build.ts`) emits hashed JS/CSS assets and a
rewritten `index.html` into `frontend/dist/`. That directory is git-ignored;
each environment builds its own.

### How the build is served

The Master serves whatever build is on disk — it does not build the frontend
itself. On startup `danube.master._resolve_spa_dir()` picks the SPA directory:

- `DANUBE_FRONTEND_DIST` if set, else
- the repo default `frontend/dist/`.

If that directory contains an `index.html` it is mounted at `/` via
`danube.api.spa.mount_spa`; otherwise the Master runs API-only and `/` returns
404 (so API-only and pre-build dev runs work unchanged). The SPA catch-all is
mounted *after* every API/RPC/webhook router, so JSON routes are never shadowed;
unmatched non-API paths fall back to `index.html` for client-side routing, while
unknown `/api/...` paths still return 404.

For local UI work, build once and run the Master, which serves the assets and
the API from the same origin (no dev proxy needed):

```bash
(cd frontend && bun run build)
uv run python -m danube.master      # serves SPA at / and the API at /api/v1
```

Re-run `bun run build` after frontend changes (or use `bun run dev` to rebuild
on change), then refresh the browser. Because the SPA and API share an origin,
the bearer token from the OIDC login flow is sent on every API/SSE request.
