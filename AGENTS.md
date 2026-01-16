# AGENTS

## Project Summary
Danube is a self-hosted CI/CD platform built in Python. It runs a Master process that orchestrates ephemeral Kubernetes pipeline pods containing a Coordinator and Worker container. Configuration is managed via a GitOps Blueprint repository.

## Key Documentation
- Architecture overview: `docs/architecture/overview.md`
- Components: `docs/architecture/components.md`
- Execution model: `docs/architecture/execution-model.md`
- Security: `docs/architecture/security.md`
- Observability: `docs/architecture/observability.md`
- Networking: `docs/architecture/networking.md`
- Data model: `docs/architecture/data-model.md`
- Blueprint config: `docs/configuration/blueprint-reference.md`
- Server config: `docs/configuration/server-config.md`

## Planning Tickets
- Phase 1 tickets: `docs/planning/phase-1-core.md`
- Phase 2 tickets: `docs/planning/phase-2-core.md`
- Phase 3 tickets: `docs/planning/phase-3-core.md`

Tickets are named `DAN-<number>`.

## Development Notes
- Python 3.14+, UV, and snektest are standard tools.
- Follow the architecture docs before implementing new components.
- Each ticket includes testing and documentation expectations; handle them within the ticket scope.
- If the architecture of the application changes during implementation, update the docs accordingly.
