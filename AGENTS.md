# AGENTS

## Project Summary

Danube is a self-hosted, single-host CI/CD appliance built in Python. It runs a Master process that orchestrates ephemeral rootless Podman job pods through a runner abstraction. Each job uses a Coordinator container for Python pipeline code and a Worker container for build commands. Configuration is managed via a GitOps Blueprint repository.

## Key Documentation

- Architecture overview: `docs/architecture/overview.md`
- Components: `docs/architecture/components.md`
- Execution model: `docs/architecture/execution-model.md`
- Local runner: `docs/architecture/local-runner.md`
- Security: `docs/architecture/security.md`
- Observability: `docs/architecture/observability.md`
- Networking: `docs/architecture/networking.md`
- Data model: `docs/architecture/data-model.md`
- Blueprint config: `docs/configuration/blueprint-reference.md`
- Server config: `docs/configuration/server-config.md`

## Development Notes

- Python 3.14+, UV, and snektest are standard tools.
- Follow the architecture docs before implementing new components.
- If the architecture of the application changes during implementation, update the docs accordingly.
