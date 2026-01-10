# Migration Summary: Rust to Python

**Date**: 2026-01-10  
**Status**: Planning Complete - Ready for Implementation

## Overview

Danube architecture has been redesigned from Rust to Python based on updated project requirements and team considerations.

## Key Decisions

### Why Python?

1. **Team velocity**: Faster iteration and broader contributor pool
2. **Type safety**: Modern Python with pyright/ty provides strong type guarantees
3. **Testing ecosystem**: snektest provides excellent async testing patterns
4. **Relaxed constraints**: Single-binary requirement removed; UV-based install is acceptable
5. **Resource flexibility**: Auto-scaling cloud environment removes strict resource limits
6. **Team interest**: While team wants to learn Rust, Python familiarity reduces initial friction

### What Changed?

| Aspect | Original (Rust) | New (Python) |
|--------|-----------------|--------------|
| **Deployment** | Single binary | UV-based installation |
| **HTTP Framework** | Axum | FastAPI |
| **gRPC** | Tonic | grpcio |
| **K8s Client** | kube-rs | kubernetes (official) |
| **Database** | sqlx | SQLAlchemy 2.0 async |
| **Async Runtime** | Tokio | asyncio + uvloop |
| **Type Checking** | Compile-time | pyright strict mode |
| **Testing** | cargo test | snektest |
| **Package Manager** | Cargo | UV |
| **Min Requirements** | 2 vCPU / 4GB | 4 vCPU / 8GB (flexible) |

### What Stayed the Same?

- **Architecture**: Hub-and-spoke networking, Pod pattern, execution model
- **Security**: AES-256-GCM encryption, SecretService, SLSA Level 3
- **Features**: GitOps, Python pipelines, Dex OIDC, Cilium egress filtering
- **Storage**: SQLite with WAL mode, same database schema
- **Frontend**: SolidJS (now served by FastAPI instead of rust-embed)

## Documentation Restructure

### Old Structure

- Single monolithic `docs/design_doc.md` (1218 lines)
- All tickets in one file
- Difficult for LLMs to parse

### New Structure

```
docs/
├── architecture/          # 7 focused architecture docs
│   ├── overview.md
│   ├── components.md
│   ├── execution-model.md
│   ├── data-model.md
│   ├── security.md
│   ├── networking.md
│   └── observability.md
├── configuration/         # 2 configuration references
│   ├── server-config.md
│   └── cac-reference.md
├── deployment/            # 2 deployment guides
│   ├── installation.md
│   └── upgrades.md
├── development/           # 2 development guides
│   ├── setup.md
│   └── testing.md
└── tickets/               # 8 epic files + README
    ├── README.md
    ├── epic-01-foundation.md
    ├── epic-02-kubernetes.md
    ├── epic-03-execution.md
    ├── epic-04-cac.md
    ├── epic-05-api-auth.md
    ├── epic-06-logs-artifacts.md
    ├── epic-07-frontend.md
    └── epic-08-operations.md
```

**Total**: 20 focused, concise documentation files

## Implementation Plan

### Epics & Tickets

| Epic | Tickets | Hours | Description |
|------|---------|-------|-------------|
| Epic 1: Foundation | 5 | 24h | Project setup, logging, config, database, encryption |
| Epic 2: Kubernetes | 4 | 30h | K8s client, pods, exec API, registry |
| Epic 3: Execution | 4 | 32h | gRPC, SDK, orchestrator |
| Epic 4: CaC | 3 | 20h | Git sync, YAML parsing, webhooks |
| Epic 5: API/Auth | 4 | 28h | FastAPI, REST, Dex, RBAC |
| Epic 6: Logs | 3 | 20h | Streaming, artifacts, reaper |
| Epic 7: Frontend | 3 | 18h | SolidJS UI, views, serving |
| Epic 8: Operations | 4 | 22h | Health, draining, metrics, SLSA |

**Total**: 31 tickets, ~186 hours (~12-13 weeks full-time, ~19 weeks at 16h/week)

### Ticket Status System

Each ticket has:
- ✅ Clear acceptance criteria (checkboxes)
- ✅ Dependencies listed
- ✅ Time estimates
- ✅ Status tracking (🔴 Todo, 🟡 In Progress, 🟢 Done, ⚫ Blocked)
- ✅ Technical notes with code examples

### Milestone Mapping

| Milestone | Epics | Weeks |
|-----------|-------|-------|
| M1: Foundation | Epic 1 | 1-2 |
| M2: K8s Core | Epic 2 | 3-4 |
| M3: Execution | Epic 3 | 5-7 |
| M4: Configuration | Epic 4 | 8-9 |
| M5: API & Auth | Epic 5 | 10-11 |
| M6: Logs | Epic 6 | 12 |
| M7: UI | Epic 7 | 13-14 |
| M8: Operations | Epic 8 | 15 |

## Next Steps

### Immediate Actions

1. **Review documentation**: Read through `docs/architecture/` files
2. **Validate tickets**: Ensure all tickets make sense for your team
3. **Set up environment**: Follow `docs/development/setup.md`
4. **Start Epic 1**: Begin with DANUBE-001 (Project Setup)

### Quick Start Commands

```bash
# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh

# Initialize project
uv init danube
cd danube

# Install dependencies (add as you go)
uv add fastapi uvicorn sqlalchemy aiosqlite kubernetes grpcio

# Start building!
```

### First Ticket Walkthrough

**DANUBE-001: Project Setup**

1. Create `pyproject.toml` with UV configuration
2. Set up directory structure (`danube/`, `tests/`, `docs/`)
3. Configure pyright in strict mode
4. Add pre-commit hooks (ruff, pyright)
5. Create README.md
6. Add `.gitignore` for Python
7. Choose license (MIT or Apache 2.0)

See `docs/tickets/epic-01-foundation.md` for full details.

## Performance Considerations

### Python Performance Notes

- **asyncio + uvloop**: 2-4x faster than default event loop
- **Pydantic v2**: Rust-based core, very fast validation
- **SQLAlchemy 2.0**: Modern async support, good performance
- **kubernetes client**: Official, well-optimized
- **Expected overhead vs Rust**: ~2-3x memory, ~2-5x CPU for orchestration (acceptable with flexible resources)

### Mitigation Strategies

- Use uvloop for event loop
- Connection pooling for K8s API
- Consider orjson for JSON (10x faster)
- Profile with py-spy for hotspots
- Scale horizontally if needed (deploy multiple Danube instances)

## Risk Assessment

### Low Risk

- ✅ Python async ecosystem is mature
- ✅ All required libraries are production-ready
- ✅ Team familiar with Python
- ✅ Type safety via pyright is strong

### Medium Risk

- ⚠️ Memory usage higher than Rust (mitigated by flexible resources)
- ⚠️ GC pauses under extreme load (monitor and tune)
- ⚠️ Runtime errors despite type checking (mitigated by comprehensive tests)

### Mitigations

- Comprehensive test suite (unit + integration + E2E)
- Memory profiling during development
- Load testing before production
- Monitoring and alerts

## Questions?

- **Architecture**: See `docs/architecture/overview.md`
- **Getting started**: See `docs/development/setup.md`
- **Tickets**: See `docs/tickets/README.md`
- **Installation**: See `docs/deployment/installation.md`

## Legacy Reference

- Old design doc archived at `docs/design_doc_legacy.md`
- Rust-based architecture preserved for reference
- All original architectural decisions documented

---

**Ready to build!** 🚀

Start with Epic 1, follow the tickets, and refer to documentation as needed. The architecture is solid, the plan is clear, and the tools are modern and powerful.

Good luck with Danube! 🌊
