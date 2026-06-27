# Contributing to Danube

## Prerequisites

- Python 3.14+
- UV
- Node.js if working on the frontend
- Rootless Podman if working on runner integration tests

## Project layout

- `backend/`: Python services, API, runner, SDK, and orchestration code
- `frontend/`: Web UI
- `infra/`: Appliance deployment artifacts
- `docs/`: Architecture, configuration, and deployment docs

## Development workflow

- Use `just` commands at the repo root for common tasks.
- Keep backend and frontend changes scoped to their directories.
- Update documentation in `docs/` when architecture or configuration changes.
