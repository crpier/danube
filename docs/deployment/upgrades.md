# Upgrade Guide

## Overview

Danube upgrades involve updating the Python package and running database migrations. The process is designed to minimize downtime.

## Upgrade Strategy

### Zero-Downtime Upgrade (Recommended)

Use job draining to wait for active jobs to complete before upgrading.

### Forced Upgrade

Stop service immediately, accepting cancellation of running jobs.

## Pre-Upgrade Checklist

Before upgrading:

- [ ] Review release notes for breaking changes
- [ ] Backup database: `sqlite3 /var/lib/danube/danube.db ".backup /backup/danube.db"`
- [ ] Backup keys: `cp -r /var/lib/danube/keys /backup/keys`
- [ ] Check disk space: `df -h /var/lib/danube` (need >20% free)
- [ ] Verify current version: `danube version`
- [ ] Test upgrade in staging environment first

## Upgrade Procedure

### Step 1: Enter Draining Mode

```bash
# Wait for all jobs to complete (up to 1 hour timeout)
danube drain --timeout=3600

# Check draining status
danube status
```

**Draining mode behavior**:
- No new jobs accepted (webhooks return 503)
- Cron jobs skipped
- Running jobs continue
- UI shows "Draining" banner

If jobs don't complete within timeout, choose:
```bash
# Force kill running jobs
danube drain --force

# Or abort drain and cancel upgrade
danube drain --cancel
```

### Step 2: Stop Danube Service

```bash
sudo systemctl stop danube

# Verify stopped
sudo systemctl status danube
```

### Step 3: Backup Database

```bash
# Online backup (WAL mode safe)
sqlite3 /var/lib/danube/danube.db ".backup /var/lib/danube/danube.db.backup-$(date +%Y%m%d-%H%M%S)"

# Verify backup
sqlite3 /var/lib/danube/danube.db.backup-* "PRAGMA integrity_check;"
```

### Step 4: Upgrade Danube Package

```bash
# Upgrade to latest version
uv tool upgrade danube

# Or upgrade to specific version
uv tool upgrade danube --version=1.2.0

# Verify new version
danube version
```

### Step 5: Run Database Migrations

```bash
# Check for pending migrations
uv run alembic current
uv run alembic history

# Apply migrations
uv run alembic upgrade head

# Verify migrations applied
uv run alembic current
```

### Step 6: Restart Service

```bash
sudo systemctl start danube

# Check status
sudo systemctl status danube

# Watch logs for errors
sudo journalctl -u danube -f
```

### Step 7: Verify Health

```bash
# Check health endpoint
curl http://localhost:8080/health/ready

# Expected response:
# {"status": "ready", "checks": {...}, "timestamp": "..."}

# Trigger test job
danube job trigger --pipeline=test-pipeline --wait

# Check UI
open http://localhost:8080
```

## Rollback Procedure

If upgrade fails, rollback:

### Step 1: Stop Danube

```bash
sudo systemctl stop danube
```

### Step 2: Restore Database

```bash
# Find latest backup
ls -lh /var/lib/danube/danube.db.backup-*

# Restore
cp /var/lib/danube/danube.db.backup-YYYYMMDD-HHMMSS /var/lib/danube/danube.db

# Verify restored database
sqlite3 /var/lib/danube/danube.db "PRAGMA integrity_check;"
```

### Step 3: Downgrade Danube Package

```bash
# Reinstall previous version
uv tool uninstall danube
uv tool install danube=1.0.0  # Previous version
```

### Step 4: Rollback Migrations (if needed)

```bash
# Check current migration
uv run alembic current

# Downgrade to previous revision
uv run alembic downgrade -1

# Or downgrade to specific revision
uv run alembic downgrade abc123
```

### Step 5: Restart Service

```bash
sudo systemctl start danube
sudo journalctl -u danube -f
```

## Version-Specific Upgrade Notes

### Upgrading to 1.1.0 (Example)

**Breaking changes**:
- `config.json` schema updated: `retention` moved under `spec.retention`
- Database migration adds `priority` column to `jobs` table

**Migration steps**:
1. Update Blueprint repository: Move `retention` config
2. Follow standard upgrade procedure
3. Migration automatically adds `priority` column (default `medium`)

### Upgrading to 2.0.0 (Example)

**Breaking changes**:
- Python SDK API changed: `step.run()` signature updated
- Database schema change: `steps` table restructured

**Migration steps**:
1. Update all `danubefile.py` scripts in app repos
2. Test updated scripts in staging
3. Follow standard upgrade procedure
4. Migration may take several minutes on large databases

## K3s Upgrade

K3s can be upgraded separately from Danube:

```bash
# Stop Danube first
sudo systemctl stop danube

# Upgrade K3s
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.28.5+k3s1 sh -s - \
  --flannel-backend=none \
  --disable-network-policy

# Wait for K3s ready
kubectl wait --for=condition=ready node --all --timeout=120s

# Verify Cilium still works
cilium status

# Restart Cilium if needed
cilium install --version 1.14.5

# Start Danube
sudo systemctl start danube
```

## Automated Upgrades

For automated upgrades in production:

```bash
#!/bin/bash
# upgrade-danube.sh

set -e

echo "Starting Danube upgrade..."

# Backup
BACKUP_FILE="/var/lib/danube/danube.db.backup-$(date +%Y%m%d-%H%M%S)"
sqlite3 /var/lib/danube/danube.db ".backup $BACKUP_FILE"
echo "Database backed up to $BACKUP_FILE"

# Drain
echo "Entering draining mode..."
danube drain --timeout=1800 || {
  echo "Draining failed or timed out"
  exit 1
}

# Stop
sudo systemctl stop danube

# Upgrade
uv tool upgrade danube

# Migrate
cd /path/to/danube/source
uv run alembic upgrade head

# Start
sudo systemctl start danube

# Health check
sleep 5
if curl -f http://localhost:8080/health/ready > /dev/null 2>&1; then
  echo "Upgrade successful!"
else
  echo "Health check failed, rolling back..."
  sudo systemctl stop danube
  cp "$BACKUP_FILE" /var/lib/danube/danube.db
  uv tool uninstall danube
  uv tool install danube=<previous-version>
  sudo systemctl start danube
  exit 1
fi
```

## Upgrade Frequency Recommendations

- **Patch versions** (1.0.x): Apply within 1 week
- **Minor versions** (1.x.0): Apply within 1 month
- **Major versions** (x.0.0): Plan carefully, test in staging first

## Security Updates

Critical security updates released as patch versions. Subscribe to:
- GitHub releases: `Watch` → `Custom` → `Releases`
- Mailing list: security@danube.dev
- RSS feed: https://github.com/yourorg/danube/releases.atom

Apply security updates immediately.

## Monitoring During Upgrade

Monitor these metrics during upgrade:

- Job success rate
- API response time
- Database query duration
- Error logs

Set up alerts for:
- Health check failures
- Database corruption
- Migration failures
- Job failure spike

## Common Upgrade Issues

### Database locked during migration

```bash
# Check for active connections
lsof /var/lib/danube/danube.db

# Kill any stale connections
pkill -f danube

# Retry migration
uv run alembic upgrade head
```

### Migration fails midway

```bash
# Check alembic_version table
sqlite3 /var/lib/danube/danube.db "SELECT * FROM alembic_version;"

# Manually mark current version
sqlite3 /var/lib/danube/danube.db "UPDATE alembic_version SET version_num='<revision>';"

# Retry migration
uv run alembic upgrade head
```

### Blueprint sync fails after upgrade

```bash
# Check for schema changes in config.json
danube blueprint validate --config=/path/to/config.json
danube blueprint sync --force
```


## Downtime Estimation

| Upgrade Type | Typical Downtime |
|--------------|------------------|
| Patch (1.0.x) | 1-2 minutes |
| Minor (1.x.0) | 2-5 minutes |
| Major (x.0.0) | 5-15 minutes |

With draining, add time for running jobs to complete.
