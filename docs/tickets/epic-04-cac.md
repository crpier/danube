# Epic 4: Configuration as Code

**Description**: Git repository sync, YAML parsing/validation, webhook routing, and cron scheduling.

**Total Estimate**: 20 hours

---

## DANUBE-014: Git Repository Watcher

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 6h  
**Dependencies:** DANUBE-002, DANUBE-004  
**Assignee:** Unassigned

### Description

Implement periodic Git repository polling, clone/pull operations, and change detection.

### Acceptance Criteria

- [ ] Git clone on startup
- [ ] Periodic pull (configurable interval)
- [ ] SSH key authentication support
- [ ] Change detection (commit SHA comparison)
- [ ] File change tracking
- [ ] Error handling for auth failures
- [ ] Tests with local Git repo
- [ ] Integration tests with real repo

---

## DANUBE-015: YAML Parser & Validation

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 6h  
**Dependencies:** DANUBE-014  
**Assignee:** Unassigned

### Description

Parse CaC YAML files (config.yaml, users.yaml, teams.yaml, pipelines/*.yaml) with Pydantic validation and sync to database.

### Acceptance Criteria

- [ ] Pydantic models for all CaC types
- [ ] YAML parsing for each file type
- [ ] Schema validation
- [ ] Reference validation (teams exist, etc.)
- [ ] Database sync (create/update/delete)
- [ ] Transactional updates
- [ ] Tests for all YAML schemas
- [ ] Tests for validation failures

---

## DANUBE-016: Webhook Router & Cron Scheduler

**Status:** 🔴 Todo  
**Priority:** P0  
**Estimate:** 8h  
**Dependencies:** DANUBE-013, DANUBE-015  
**Assignee:** Unassigned

### Description

Route incoming webhooks to pipelines, validate signatures, and implement cron-based pipeline triggers.

### Acceptance Criteria

- [ ] GitHub webhook handler
- [ ] GitLab webhook handler
- [ ] Webhook signature verification
- [ ] Pipeline matching by repo URL
- [ ] Branch filter matching
- [ ] Cron scheduler (croniter)
- [ ] Cron expression parsing
- [ ] Trigger deduplication
- [ ] Tests for webhook routing
- [ ] Tests for cron scheduling

---

## Updates

- 2026-01-10: Epic created
