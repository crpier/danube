# Epic 5: API & Authentication

**Description**: FastAPI application, REST endpoints, Dex OIDC integration, and team-based RBAC.

**Total Estimate**: 28 hours

---

## DANUBE-017: FastAPI Application Shell

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 4h  
**Dependencies:** DANUBE-002  
**Assignee:** Unassigned

### Description

Create FastAPI application with middleware, exception handlers, and basic structure.

### Acceptance Criteria

- [ ] FastAPI app initialization
- [ ] CORS middleware configured
- [ ] Exception handlers (HTTP exceptions, validation errors)
- [ ] Request logging middleware
- [ ] Health check endpoints
- [ ] OpenAPI documentation auto-generated
- [ ] Tests for middleware
- [ ] Tests for exception handling

---

## DANUBE-018: REST API Endpoints

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 10h  
**Dependencies:** DANUBE-017, DANUBE-013, DANUBE-004  
**Assignee:** Unassigned

### Description

Implement REST API endpoints for pipelines, jobs, logs, artifacts, and secrets management.

### Acceptance Criteria

- [ ] `GET /api/v1/pipelines` - List pipelines
- [ ] `GET /api/v1/pipelines/{id}` - Get pipeline
- [ ] `GET /api/v1/jobs` - List jobs
- [ ] `GET /api/v1/jobs/{id}` - Get job
- [ ] `POST /api/v1/jobs/{id}/cancel` - Cancel job
- [ ] `GET /api/v1/jobs/{id}/logs` - Get logs
- [ ] `GET /api/v1/jobs/{id}/logs/stream` - SSE log stream
- [ ] `GET /api/v1/artifacts/{job_id}/{name}` - Download artifact
- [ ] Pydantic request/response models
- [ ] Tests for all endpoints

---

## DANUBE-019: Dex OIDC Integration

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 8h  
**Dependencies:** DANUBE-017, DANUBE-009  
**Assignee:** Unassigned

### Description

Deploy Dex OIDC provider, configure user authentication from CaC, and implement JWT validation.

### Acceptance Criteria

- [ ] Dex deployment in danube-system namespace
- [ ] Dex configured with static passwords from users.yaml
- [ ] JWT token issuance
- [ ] FastAPI JWT validation middleware
- [ ] Login redirect flow
- [ ] Logout endpoint
- [ ] Token refresh
- [ ] Tests for JWT validation
- [ ] Integration test for full auth flow

---

## DANUBE-020: Team-Based RBAC Middleware

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 6h  
**Dependencies:** DANUBE-019, DANUBE-004  
**Assignee:** Unassigned

### Description

Implement permission checking middleware based on team memberships and pipeline permissions.

### Acceptance Criteria

- [ ] Permission decorator for endpoints
- [ ] Team membership lookup
- [ ] Pipeline permission checking
- [ ] Global admin support
- [ ] Permission levels (read, write, admin)
- [ ] 403 Forbidden responses
- [ ] Tests for permission checks
- [ ] Integration tests with real users/teams

---

## Updates

- 2026-01-10: Epic created
