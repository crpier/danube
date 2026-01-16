# Phase 3: Delivery & Operations

### DAN-19: Helm chart deployment
- **Status**: todo
- **Summary**: Package Danube services into a Helm chart.
- **Description**: Deploy Master, Dex, registry, PVCs, namespaces, and NetworkPolicies.
- **Acceptance Criteria**:
  - `helm upgrade --install` deploys all components into `danube-system`.
  - Values allow customizing images, storage, and config repo settings.
- **Testing**:
  - Add integration test to render chart and validate manifests.
  - Run `helm template` validation in CI.
- **Documentation**:
  - Update `docs/deployment/installation.md` Helm section.

### DAN-20: Bootstrap installer
- **Status**: todo
- **Summary**: Provide one-command install for fresh hosts.
- **Description**: Install K3s + Cilium + Helm chart with system checks.
- **Acceptance Criteria**:
  - Installer provisions K3s, Cilium, and deploys chart.
  - Installation logs show progress and errors clearly.
- **Testing**:
  - Add integration test for installer script in a clean VM or container.
  - Run `bash -n` lint check for installer script.
- **Documentation**:
  - Update `docs/deployment/installation.md` bootstrap section.

### DAN-21: Systemd service and hardening
- **Status**: todo
- **Summary**: Create systemd unit for Danube master.
- **Description**: Provide service file with secure defaults and restart policy.
- **Acceptance Criteria**:
  - Service uses dedicated user and restricted permissions.
  - `systemctl start danube` starts master successfully.
- **Testing**:
  - Add unit test to validate unit file rendering.
  - Run `systemd-analyze verify` for unit file (manual or CI).
- **Documentation**:
  - Update `docs/deployment/installation.md` systemd section.

### DAN-22: CLI for secrets and key rotation
- **Status**: todo
- **Summary**: Implement secret management commands.
- **Description**: Add CLI commands to set secrets and rotate encryption keys.
- **Acceptance Criteria**:
  - `danube secret set` stores encrypted secrets.
  - `danube secret rotate-key` re-encrypts secrets.
- **Testing**:
  - Add unit tests for CLI commands.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/architecture/security.md` secret rotation section.

### DAN-23: CLI for blueprint sync + validate
- **Status**: todo
- **Summary**: Provide admin CLI for GitOps operations.
- **Description**: Add CLI commands to sync and validate blueprint repositories.
- **Acceptance Criteria**:
  - `danube blueprint sync` triggers a sync cycle.
  - `danube blueprint validate` outputs validation errors.
- **Testing**:
  - Add unit tests for CLI commands.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/configuration/blueprint-reference.md` CLI section.

### DAN-24: CLI for drain + status
- **Status**: todo
- **Summary**: Add operational commands for upgrades.
- **Description**: Implement drain, force drain, and status reporting.
- **Acceptance Criteria**:
  - Drain mode blocks new jobs and reports status.
  - Status shows active jobs and drain state.
- **Testing**:
  - Add unit tests for drain transitions.
  - Run `uv run snektest tests/unit/`.
- **Documentation**:
  - Update `docs/deployment/upgrades.md` drain section.

### DAN-25: Frontend SPA shell + auth flow
- **Status**: todo
- **Summary**: Build the baseline UI shell.
- **Description**: Implement the frontend layout, routing, and Dex login flow.
- **Acceptance Criteria**:
  - User can authenticate and reach the main dashboard.
  - API requests include auth tokens.
- **Testing**:
  - Add frontend unit tests for auth flow.
  - Run `npm run test` in `frontend/`.
- **Documentation**:
  - Update `docs/development/setup.md` frontend section.

### DAN-26: Job list + detail + log streaming UI
- **Status**: todo
- **Summary**: Display jobs and live logs in the UI.
- **Description**: Add job list view, job detail page, and SSE log stream UI.
- **Acceptance Criteria**:
  - Job list shows status and timestamps.
  - Job detail shows step logs streamed live.
- **Testing**:
  - Add frontend tests for list/detail rendering.
  - Run `npm run test` in `frontend/`.
- **Documentation**:
  - Update `docs/architecture/observability.md` UI log streaming note.

### DAN-27: Pipeline list + manual trigger UI
- **Status**: todo
- **Summary**: View pipelines and trigger jobs.
- **Description**: Add pipeline list view and manual trigger button.
- **Acceptance Criteria**:
  - Pipeline list displays metadata and permissions.
  - Manual trigger creates a job and shows in jobs list.
- **Testing**:
  - Add frontend tests for pipeline list and trigger.
  - Run `npm run test` in `frontend/`.
- **Documentation**:
  - Update `docs/architecture/components.md` UI endpoints section.

### DAN-28: End-to-end pipeline flow test
- **Status**: todo
- **Summary**: Validate full pipeline execution flow.
- **Description**: Implement E2E test for webhook trigger → job completion → logs.
- **Acceptance Criteria**:
  - E2E test passes with success status and expected logs.
- **Testing**:
  - Add E2E test under `tests/e2e/`.
  - Run `uv run snektest tests/e2e/`.
- **Documentation**:
  - Update `docs/development/testing.md` e2e section.
