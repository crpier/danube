# Epic 6: Logs & Artifacts

**Description**: Log streaming (disk + SSE), artifact upload/download, and garbage collection (Reaper).

**Total Estimate**: 20 hours

---

## DANUBE-021: Log Streaming (SSE + Disk)

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 8h  
**Dependencies:** DANUBE-013, DANUBE-018  
**Assignee:** Unassigned

### Description

Stream job logs to disk (append-only) and to UI clients via Server-Sent Events (SSE).

### Acceptance Criteria

- [ ] Log file writer (aiofiles)
- [ ] Append-only log format
- [ ] SSE endpoint for log streaming
- [ ] Real-time log broadcasting
- [ ] Log file reader with offset
- [ ] Backpressure handling for slow clients
- [ ] Secret scrubbing in logs
- [ ] Tests for log writing
- [ ] Tests for SSE streaming

---

## DANUBE-022: Artifact Upload/Download

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 6h  
**Dependencies:** DANUBE-011, DANUBE-018  
**Assignee:** Unassigned

### Description

Implement artifact upload via gRPC (streaming) and download via HTTP API.

### Acceptance Criteria

- [ ] `UploadArtifact` gRPC handler (streaming)
- [ ] Artifact storage in `/var/lib/danube/artifacts/{job_id}/`
- [ ] Artifact metadata in database
- [ ] `GET /api/v1/artifacts/{job_id}/{name}` endpoint
- [ ] Support for tar.gz archives
- [ ] Directory upload support
- [ ] Tests for upload
- [ ] Tests for download

---

## DANUBE-023: Reaper (GC for Logs, Artifacts, Images)

**Status:** 🔴 Todo  
**Priority:** P1  
**Estimate:** 6h  
**Dependencies:** DANUBE-021, DANUBE-022, DANUBE-009  
**Assignee:** Unassigned

### Description

Background task to delete old logs, artifacts, and registry images based on retention policy.

### Acceptance Criteria

- [ ] Daily scheduled task (runs at 03:00 UTC)
- [ ] Delete logs older than `retention.logs_days`
- [ ] Delete artifacts older than `retention.artifacts_days`
- [ ] Delete registry images older than `retention.registry_images_days`
- [ ] Disk space freed metric
- [ ] Dry-run mode for testing
- [ ] Tests for retention logic
- [ ] Integration test with real files

---

## Updates

- 2026-01-10: Epic created
