# Danube

Danube is a self-hosted CI/CD platform built in Python. It runs a Master process that orchestrates ephemeral Kubernetes pipeline pods containing Coordinator and Worker containers. Configuration is managed via a GitOps Blueprint repository.

## Repository layout
- `backend/`: Python services (master, coordinator, worker, API)
- `frontend/`: TypeScript web UI
- `infra/`: Helm charts and deployment artifacts
- `docs/`: Architecture and configuration documentation
- `examples/`: Example blueprints and sample configurations
- `tests/`: Cross-cutting tests
- `scripts/`: Repo-wide helper scripts
- `tools/`: Local developer tools and utilities

## Getting started
- See `docs/architecture/overview.md` for the system architecture.
- See `docs/configuration/blueprint-reference.md` for blueprint config layout.

## Development
- Backend code lives under `backend/`.
- Frontend code lives under `frontend/`.
- Infrastructure artifacts live under `infra/`.
