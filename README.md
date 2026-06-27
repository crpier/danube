# Danube

Danube is a self-hosted, single-host CI/CD appliance built in Python. It runs a Master process that orchestrates ephemeral rootless Podman job pods for pipeline execution. Pipeline definitions are written in Python, while configuration is managed through a GitOps Blueprint repository.

## Core model

- One Danube Master per appliance host
- One pipeline run = one isolated ephemeral Podman pod
- Coordinator container runs the user's `danubefile.py`
- Worker container runs build commands in a user-selected image
- Master mediates all command execution, logs, secrets, artifacts, and cleanup
- Configuration lives in a version-controlled Blueprint repository

## Repository layout

- `danube/`: the importable `danube` package — Python services, SDK, runner, API, and orchestration code
- `frontend/`: Web UI
- `infra/`: Deployment artifacts for the appliance
- `docs/`: Architecture and configuration documentation
- `examples/`: Example blueprints and sample configurations
- `tests/`: `unit/`, `integration/`, and `e2e/` suites (snektest)
- `scripts/`: Repo-wide helper scripts
- `tools/`: Local developer tools and utilities

## Getting started

- See `docs/architecture/overview.md` for the system architecture.
- See `docs/architecture/local-runner.md` for the rootless Podman runner.
- See `docs/configuration/blueprint-reference.md` for Blueprint configuration.
- See `docs/deployment/installation.md` for installation notes.
