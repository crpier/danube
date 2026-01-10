# Epic 7: Frontend

**Description**: SolidJS single-page application with routing, pipeline/job views, and log streaming.

**Total Estimate**: 18 hours

---

## DANUBE-024: SolidJS App Shell & Routing

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 6h  
**Dependencies:** DANUBE-018  
**Assignee:** Unassigned

### Description

Create SolidJS application with routing, layout, navigation, and authentication integration.

### Acceptance Criteria

- [ ] Solid Router configured
- [ ] App layout (header, sidebar, content)
- [ ] Navigation menu
- [ ] Auth context (JWT storage)
- [ ] Login/logout flow
- [ ] Protected routes
- [ ] API client wrapper (fetch with auth)
- [ ] Build configuration (Vite)

---

## DANUBE-025: Pipeline & Job Views

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 10h  
**Dependencies:** DANUBE-024  
**Assignee:** Unassigned

### Description

Build UI views for pipeline list, pipeline detail, job list, job detail, and live log streaming.

### Acceptance Criteria

- [ ] Pipeline list view
- [ ] Pipeline detail view
- [ ] Job list view (with filtering)
- [ ] Job detail view
- [ ] Live log viewer (SSE integration)
- [ ] Job cancellation button
- [ ] Artifact download links
- [ ] Real-time status updates
- [ ] Error handling and loading states

---

## DANUBE-026: Static File Serving from FastAPI

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 2h  
**Dependencies:** DANUBE-025, DANUBE-017  
**Assignee:** Unassigned

### Description

Configure FastAPI to serve built frontend files at root path.

### Acceptance Criteria

- [ ] Frontend build integrated in project
- [ ] StaticFiles middleware configured
- [ ] Root path (`/`) serves `index.html`
- [ ] SPA routing fallback (all routes → index.html)
- [ ] API routes prefixed with `/api/`
- [ ] Production build tested

---

## Updates

- 2026-01-10: Epic created
