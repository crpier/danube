# Contributing to Danube

## Prerequisites

- Python 3.14+
- UV
- Node.js if working on the frontend
- Rootless Podman if working on runner integration tests

## Project layout

- `danube/`: the importable `danube` package — Python services, API, runner, SDK, and orchestration code
- `tests/`: `unit/`, `integration/`, and `e2e/` suites (snektest)
- `frontend/`: Web UI
- `infra/`: Appliance deployment artifacts
- `docs/`: Architecture, configuration, and deployment docs

## Development workflow

- Run all commands from the repo root.
- Keep the `danube` package and frontend changes scoped to their directories.
- Update documentation in `docs/` when architecture or configuration changes.

## Validation

There is no CI yet, so validation is manual. Before opening or merging a PR, run
the full gate from the repo root and keep it clean:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python -m snektest tests/
```
