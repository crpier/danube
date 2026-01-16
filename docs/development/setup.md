# Development Environment Setup

## Prerequisites

- Python 3.14+
- [UV](https://github.com/astral-sh/uv) package manager
- Git
- Docker or K3s (for integration testing)
- A code editor with Python support (VS Code, PyCharm, etc.)

## Initial Setup

### 1. Clone Repository

```bash
git clone https://github.com/yourorg/danube.git
cd danube
```

### 2. Install UV

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Install Dependencies

```bash
# Create virtual environment and install dependencies
uv sync

# Verify installation
uv run python --version
uv run danube --version
```

### 4. Install Pre-Commit Hooks

```bash
uv run pre-commit install
```

## Project Structure

```
danube/
├── danube/
│   ├── __init__.py
│   ├── master.py           # Master process entry point
│   ├── api/                # FastAPI HTTP/HTTP2 servers
│   │   ├── http.py
│   │   └── rpc.py
│   ├── orchestrator/       # Job orchestration
│   │   ├── scheduler.py
│   │   └── state_machine.py
│   ├── k8s/                # Kubernetes client wrapper
│   │   ├── client.py
│   │   ├── pod_builder.py
│   │   └── exec.py
│   ├── db/                 # Database layer
│   │   ├── queries.py
│   │   └── migrations/
│   ├── blueprint/          # Blueprint (GitOps)
│   │   ├── syncer.py
│   │   └── parser.py
│   ├── security/           # Secrets and auth
│   │   ├── secrets.py
│   │   └── auth.py
│   └── sdk/                # Coordinator Python SDK
│       └── coordinator.py
├── protocol/               # HTTP/2 JSON protocol definitions
│   └── rpc-schema.json
├── frontend/               # SolidJS UI
│   ├── src/
│   └── package.json
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/                   # Documentation
├── pyproject.toml          # UV/Python config
├── uv.lock                 # Locked dependencies
└── README.md
```

## Configuration for Development

### 1. Create Local Config

```bash
mkdir -p ~/.config/danube
cat > ~/.config/danube/danube.toml <<EOF
[server]
bind_address = "127.0.0.1:8080"
data_dir = "./data"

[config_repo]
url = "file:///tmp/danube-blueprint-test"
branch = "main"
sync_interval = "10s"

[logging]
level = "debug"
format = "text"
EOF
```

### 2. Create Test Blueprint Repository

```bash
# Create local Git repo for testing
mkdir -p /tmp/danube-blueprint-test
cd /tmp/danube-blueprint-test
git init

# Create minimal config
cat > config.json <<EOF
{
  "apiVersion": "danube.dev/v1",
  "kind": "Config",
  "metadata": {"name": "global"},
  "spec": {
    "retention": {
      "logs_days": 7,
      "artifacts_days": 7,
      "registry_images_days": 7
    },
    "egress_allowlist": ["registry.npmjs.org"]
  }
}
EOF

cat > users.json <<EOF
[
  {
    "apiVersion": "danube.dev/v1",
    "kind": "User",
    "metadata": {"name": "dev"},
    "spec": {
      "email": "dev@localhost",
      "password_hash": "\$2b\$12\$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5NU2FcbZqd7rO"
    }
  }
]
EOF

cat > teams.json <<EOF
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
EOF

mkdir pipelines

git add .
git commit -m "Initial test blueprint"

cd -
```

### 3. Set Up Local K3s (Optional)

For full integration testing:

```bash
# Install K3s
curl -sfL https://get.k3s.io | sh -s - \
  --flannel-backend=none \
  --disable-network-policy \
  --write-kubeconfig-mode=644

# Install Cilium
cilium install

# Create namespaces
kubectl create namespace danube-system
kubectl create namespace danube-jobs
```

## Running Danube Locally

### Run Master Process

```bash
# Run with local config
uv run python -m danube.master --config ~/.config/danube/danube.toml

# Or with environment variables
export DANUBE_LOG_LEVEL=debug
export DANUBE_DATA_DIR=./data
uv run python -m danube.master
```

### Run with Auto-Reload

For development, use `watchfiles` to auto-reload on code changes:

```bash
uv add --dev watchfiles
uv run watchfiles 'uv run python -m danube.master' danube/
```

### Access UI

Open http://localhost:8080 in browser.

Login credentials:
- Email: `dev@localhost`
- Password: `password`

## Code Quality Tools

### Type Checking

```bash
# Run pyright
uv run pyright danube/

# Check specific file
uv run pyright danube/master.py
```

**Configure pyright** in `pyproject.toml`:
```toml
[tool.pyright]
typeCheckingMode = "strict"
pythonVersion = "3.14"
include = ["danube"]
exclude = ["**/node_modules", "**/__pycache__", "tests"]
```

### Linting

```bash
# Run ruff
uv run ruff check danube/

# Auto-fix issues
uv run ruff check --fix danube/
```

### Formatting

```bash
# Format code with ruff
uv run ruff format danube/
```

### Import Sorting

```bash
# Sort imports
uv run ruff check --select I --fix danube/
```

## Testing

See [Testing Guide](./testing.md) for detailed testing instructions.

### Quick Test Run

```bash
# Run all tests
uv run snektest

# Run with coverage
uv run snektest --cov=danube --cov-report=html

# Run specific test file
uv run snektest tests/unit/test_orchestrator.py

# Run specific test
uv run snektest tests/unit/test_orchestrator.py::test_job_creation
```

## Database Management

### Create Migration

```bash
# Create migration with manual SQL
uv run alembic revision -m "Add new column"

# Edit migration file in danube/db/migrations/versions/
```

### Apply Migrations

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Apply specific migration
uv run alembic upgrade abc123
```

### Rollback Migration

```bash
# Rollback last migration
uv run alembic downgrade -1

# Rollback to specific revision
uv run alembic downgrade abc123
```

### Reset Database

```bash
# Drop all tables and re-create
rm ./data/danube.db
uv run alembic upgrade head
```

## HTTP/2 JSON Development

### Example RPC Requests

```bash
# RunStep
curl --http2-prior-knowledge -X POST http://localhost:9000/rpc/run-step \
  -H "Content-Type: application/json" \
  -d '{"job_id":"abc123","command":"npm ci"}'

# GetSecret
curl --http2-prior-knowledge -X POST http://localhost:9000/rpc/get-secret \
  -H "Content-Type: application/json" \
  -d '{"job_id":"abc123","key":"API_KEY"}'
```

## Frontend Development

See `frontend/README.md` for detailed frontend setup.

### Quick Start

```bash
cd frontend

# Install dependencies
npm install

# Run dev server
npm run dev

# Build for production
npm run build
```

Frontend runs on http://localhost:5173 and proxies API requests to http://localhost:8080.

## Debugging

### VS Code Launch Configuration

Create `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Danube Master",
      "type": "python",
      "request": "launch",
      "module": "danube.master",
      "args": ["--config", "${workspaceFolder}/.config/danube.toml"],
      "console": "integratedTerminal",
      "env": {
        "DANUBE_LOG_LEVEL": "debug"
      }
    },
    {
      "name": "Snektest Current File",
      "type": "python",
      "request": "launch",
      "module": "snektest",
      "args": ["${file}", "-v"],
      "console": "integratedTerminal"
    }
  ]
}
```

### Python Debugger

```python
# Add breakpoint in code
import pdb; pdb.set_trace()

# Or use debugpy for remote debugging
import debugpy
debugpy.listen(5678)
debugpy.wait_for_client()
```

### Logging

```python
import logging

logger = logging.getLogger(__name__)

# Debug log
logger.debug("Processing job", extra={"job_id": job.id})

# With structured logging
logger.info("Job started", extra={
    "event": "job_started",
    "job_id": job.id,
    "pipeline": job.pipeline_id
})
```

## Common Development Tasks

### Add New API Endpoint

1. Define route in `danube/api/http.py`
2. Add request/response models with Pydantic
3. Implement handler function
4. Add tests in `tests/unit/api/test_http.py`
5. Update OpenAPI docs (automatic with FastAPI)

### Add New Database Table

1. Add SQL in `danube/db/queries.py`
2. Update migrations (manual SQL) in `danube/db/migrations/`
3. Apply migration: `uv run alembic upgrade head`
4. Add repository methods in `danube/db/queries.py`
5. Add tests

### Add New Configuration Option

1. Update Blueprint schema in `danube/blueprint/schema.json`
2. Update parser in `danube/blueprint/parser.py`
3. Update validation logic
4. Update documentation in `docs/configuration/cac-reference.md` (Blueprint JSON examples).
5. Refresh JSON Schema if needed

5. Add validation tests

## Troubleshooting

### UV sync fails

```bash
# Clear cache and retry
uv cache clean
uv sync
```

### Import errors

```bash
# Ensure virtual environment is activated
uv run python -c "import sys; print(sys.executable)"

# Should show path with .venv
```

### K8s connection errors

```bash
# Check kubeconfig
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl cluster-info

# Or use in-cluster config
export KUBERNETES_SERVICE_HOST=localhost
export KUBERNETES_SERVICE_PORT=6443
```

### Database locked

```bash
# Check for stale connections
lsof ./data/danube.db

# Remove lock file
rm ./data/danube.db-wal ./data/danube.db-shm
```

## Contributing

See `docs/development/contributing.md` for contribution guidelines.
