# Epic 8: Operations

**Description**: Health checks, job draining, OpenTelemetry metrics, SLSA provenance, and installation tooling.

**Total Estimate**: 22 hours

---

## DANUBE-027: Health Checks & Readiness

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 4h  
**Dependencies:** DANUBE-017, DANUBE-004, DANUBE-006  
**Assignee:** Unassigned

### Description

Implement `/health` and `/health/ready` endpoints with subsystem checks (database, K8s, Dex, Registry).

### Acceptance Criteria

- [ ] `GET /health` endpoint (liveness)
- [ ] `GET /health/ready` endpoint (readiness)
- [ ] Database connectivity check
- [ ] K8s API connectivity check
- [ ] Dex pod status check
- [ ] Registry pod status check
- [ ] CaC sync freshness check
- [ ] JSON response with check details
- [ ] Tests for all health checks

---

## DANUBE-028: Job Draining for Upgrades

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 4h  
**Dependencies:** DANUBE-013  
**Assignee:** Unassigned

### Description

Implement draining mode to gracefully stop accepting new jobs and wait for running jobs to complete.

### Acceptance Criteria

- [ ] `danube drain` CLI command
- [ ] Draining mode flag in database
- [ ] Reject new job triggers during draining
- [ ] Wait for running jobs with timeout
- [ ] Force-kill option for jobs exceeding timeout
- [ ] `danube status` shows draining state
- [ ] Tests for draining logic
- [ ] Integration test for full drain flow

---

## DANUBE-029: OpenTelemetry Metrics & Traces

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 6h  
**Dependencies:** DANUBE-017, DANUBE-013  
**Assignee:** Unassigned

### Description

Integrate OpenTelemetry for metrics export (Prometheus format) and distributed tracing.

### Acceptance Criteria

- [ ] OpenTelemetry SDK configured
- [ ] Prometheus exporter at `/metrics`
- [ ] Key metrics implemented (jobs, DB queries, K8s calls)
- [ ] Trace spans for HTTP requests
- [ ] Trace spans for gRPC calls
- [ ] Trace spans for job execution
- [ ] OTLP exporter configured (optional)
- [ ] Tests for metrics collection

---

## DANUBE-030: Installation Documentation & Scripts

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 6h  
**Dependencies:** DANUBE-009, DANUBE-019  
**Assignee:** Unassigned

### Description

Create installation script and comprehensive documentation for deploying Danube.

### Acceptance Criteria

- [ ] Installation script (`install.sh`)
- [ ] K3s installation with Cilium
- [ ] Key generation script
- [ ] Systemd service file
- [ ] Installation documentation
- [ ] Troubleshooting guide
- [ ] Upgrade documentation
- [ ] Tested on Ubuntu 22.04

---

## DANUBE-031: SLSA L3 Provenance Generation & Signing

**Status:** 🔴 Todo  
**Priority:** P2  
**Estimate:** 8h  
**Dependencies:** DANUBE-013, DANUBE-005  
**Assignee:** Unassigned

### Description

Generate SLSA provenance documents for completed jobs and sign with Ed25519 key.

### Acceptance Criteria

- [ ] Ed25519 signing key generation
- [ ] SLSA provenance document generation (in-toto format)
- [ ] Provenance includes: build steps, inputs, outputs, env
- [ ] Ed25519 signature generation
- [ ] Provenance saved to artifacts directory
- [ ] `danube provenance verify` command
- [ ] Tests for provenance generation
- [ ] Tests for signature verification

---

## Updates

- 2026-01-10: Epic created
