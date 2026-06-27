# Development Environment Setup

## Prerequisites

- Python 3.14+
- UV package manager
- Git
- Podman for runner integration tests
- Node.js only if working on the frontend

## Initial Setup

```bash
git clone https://github.com/yourorg/danube.git
cd danube
uv sync
uv run python --version
```

## Project Structure

```text
danube/
├── backend/
│   └── danube/
│       ├── master.py
│       ├── api/
│       ├── orchestrator/
│       ├── runner/
│       ├── db/
│       ├── blueprint/
│       ├── security/
│       └── sdk/
├── frontend/
├── tests/
├── docs/
├── pyproject.toml
└── uv.lock
```

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

```bash
uv run pyright backend/danube
uv run ruff check backend/danube
uv run ruff format backend/danube
```

## Testing

```bash
uv run snektest
uv run snektest tests/unit/
uv run snektest tests/integration/
```

See [Testing Guide](./testing.md).

## Common Development Tasks

### Add API Endpoint

1. Define route in `backend/danube/api/`.
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

```bash
cd frontend
npm install
npm run dev
```

Frontend dev server should proxy API requests to http://localhost:8080.
