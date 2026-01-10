# Danube Development Tickets

## Overview

This directory contains all development tickets organized by epic. Each epic is a self-contained file with multiple related tickets.

## Epic Structure

| Epic ID | Epic Name | Tickets | Description |
|---------|-----------|---------|-------------|
| [Epic 1](./epic-01-foundation.md) | Foundation | 5 tickets | Project setup, logging, configuration, database |
| [Epic 2](./epic-02-kubernetes.md) | Kubernetes Integration | 4 tickets | K8s client, pod management, exec API, registry |
| [Epic 3](./epic-03-execution.md) | Execution Engine | 4 tickets | gRPC, SDK, orchestrator, job management |
| [Epic 4](./epic-04-cac.md) | Configuration as Code | 3 tickets | Git sync, YAML parsing, webhooks, scheduler |
| [Epic 5](./epic-05-api-auth.md) | API & Authentication | 4 tickets | FastAPI, REST endpoints, Dex OIDC, RBAC |
| [Epic 6](./epic-06-logs-artifacts.md) | Logs & Artifacts | 3 tickets | Log streaming, artifact management, reaper |
| [Epic 7](./epic-07-frontend.md) | Frontend | 3 tickets | SolidJS UI, routing, views |
| [Epic 8](./epic-08-operations.md) | Operations | 4 tickets | Health checks, draining, metrics, SLSA |

**Total**: 8 epics, 31 tickets, ~186 hours estimated

## Status Definitions

Each ticket has a status indicator:

- 🔴 **Todo**: Not started
- 🟡 **In Progress**: Currently being worked on
- 🟢 **Done**: Completed and tested
- ⚫ **Blocked**: Waiting on dependency or external factor

## Priority Levels

- **P0**: Critical - blocking other work
- **P1**: High - important for MVP
- **P2**: Medium - nice to have
- **P3**: Low - future enhancement

## Updating Ticket Status

### Manual Update

Edit the ticket file and change the status emoji and field:

```markdown
**Status:** 🟡 In Progress
```

### Using Helper Script

```bash
# Mark ticket as in progress
python scripts/update_ticket.py DANUBE-001 in-progress

# Mark ticket as done
python scripts/update_ticket.py DANUBE-001 done

# Mark ticket as blocked
python scripts/update_ticket.py DANUBE-001 blocked "Waiting for K8s setup"
```

## Workflow

### Starting a Ticket

1. Choose a ticket marked 🔴 Todo
2. Check dependencies are completed
3. Update status to 🟡 In Progress
4. Create feature branch: `git checkout -b DANUBE-XXX-short-description`
5. Implement according to acceptance criteria
6. Write tests
7. Update documentation if needed
8. Create PR

### Completing a Ticket

1. Verify all acceptance criteria checked
2. Ensure tests pass
3. Update status to 🟢 Done
4. Merge PR
5. Move to next ticket

## Dependencies

Tickets have dependencies indicated in the **Dependencies** field. Complete dependency tickets first.

Example dependency chain:
```
DANUBE-001 (Project setup)
    └── DANUBE-002 (Logging)
            └── DANUBE-003 (Database)
```

## Estimation

Time estimates are rough guidelines based on:
- Development time
- Testing time
- Documentation updates

Actual time may vary based on:
- Developer experience with Python
- Familiarity with kubernetes/FastAPI/etc.
- Complexity discovered during implementation

## Milestone Mapping

| Milestone | Epics | Target |
|-----------|-------|--------|
| M1: Foundation | Epic 1 | Week 1-2 |
| M2: K8s Core | Epic 2 | Week 3-4 |
| M3: Execution | Epic 3 | Week 5-7 |
| M4: Configuration | Epic 4 | Week 8-9 |
| M5: API & Auth | Epic 5 | Week 10-11 |
| M6: Logs | Epic 6 | Week 12 |
| M7: UI | Epic 7 | Week 13-14 |
| M8: Operations | Epic 8 | Week 15 |

## Querying Tickets

### Find all in-progress tickets

```bash
grep -r "🟡 In Progress" docs/tickets/*.md
```

### Find all blocked tickets

```bash
grep -r "⚫ Blocked" docs/tickets/*.md
```

### Find tickets by assignee

```bash
grep -r "Assignee: alice" docs/tickets/*.md
```

### Count completed tickets

```bash
grep -r "🟢 Done" docs/tickets/*.md | wc -l
```

## Contributing

When creating new tickets:

1. Use the template from any existing ticket
2. Assign appropriate priority (P0-P3)
3. List all dependencies
4. Define clear acceptance criteria (checkboxes)
5. Provide technical notes if helpful
6. Estimate hours realistically

## Questions?

For questions about tickets or the development process, see:
- [Development Setup](../development/setup.md)
- [Contributing Guide](../development/contributing.md)
