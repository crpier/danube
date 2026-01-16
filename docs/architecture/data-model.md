# Data Model

## SQLite Schema

### Core Tables

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    oidc_subject TEXT UNIQUE,  -- Dex subject claim
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE teams (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE team_members (
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',  -- 'member', 'admin'
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE pipelines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(id),
    repo_url TEXT NOT NULL,
    branch_filter TEXT,  -- JSON array: ["main", "release/*"]
    cron_schedule TEXT,  -- Cron expression or NULL
    config_path TEXT NOT NULL DEFAULT 'danubefile.py',
    max_duration_seconds INTEGER DEFAULT 3600,
    workspace_size_gb INTEGER DEFAULT 5,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE pipeline_permissions (
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    level TEXT NOT NULL,  -- 'read', 'write', 'admin'
    PRIMARY KEY (pipeline_id, team_id)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
    trigger_type TEXT NOT NULL,  -- 'webhook', 'cron', 'manual'
    trigger_ref TEXT,  -- Git SHA or branch name
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT,
    finished_at TEXT,
    log_path TEXT,  -- Relative path to log file
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE steps (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    exit_code INTEGER,
    started_at TEXT,
    finished_at TEXT,
    log_offset_start INTEGER,  -- Byte offset in job log file
    log_offset_end INTEGER
);

CREATE TABLE secrets (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT REFERENCES pipelines(id),  -- NULL = global secret
    key TEXT NOT NULL,
    value_encrypted BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(pipeline_id, key)
);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,  -- Filesystem path relative to artifacts dir
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(job_id, name)
);

-- Indexes
CREATE INDEX idx_jobs_pipeline_id ON jobs(pipeline_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_steps_job_id ON steps(job_id);
CREATE INDEX idx_team_members_user_id ON team_members(user_id);
CREATE INDEX idx_pipeline_permissions_team_id ON pipeline_permissions(team_id);
```

## Filesystem Layout

```
/var/lib/danube/
├── danube.db              # SQLite database (WAL mode enabled)
├── danube.db-wal          # Write-ahead log
├── danube.db-shm          # Shared memory file
├── logs/
│   └── <job_id>.log       # Append-only log file per job
├── artifacts/
│   └── <job_id>/
│       ├── <artifact1>.tar.gz
│       └── <artifact2>/
│           └── files...
├── registry/              # Container registry storage
│   └── docker/
│       └── registry/
│           └── v2/
│               └── repositories/
└── keys/
    ├── encryption.key     # AES-256-GCM key (32 bytes)
    ├── signing.key        # Ed25519 private key for provenance
    ├── signing.key.pub    # Ed25519 public key
    ├── git_deploy_key     # SSH private key for Blueprint repo
    └── git_deploy_key.pub # SSH public key (add to Git repo deploy keys)
```

## Key Files

### encryption.key

- **Purpose**: Symmetric encryption for secrets in SQLite
- **Algorithm**: AES-256-GCM
- **Format**: Raw 32-byte binary
- **Permissions**: 0600 (read/write owner only)
- **Generation**: `openssl rand -out encryption.key 32`

### signing.key

- **Purpose**: Sign SLSA provenance documents
- **Algorithm**: Ed25519
- **Format**: PEM-encoded private key
- **Permissions**: 0600
- **Generation**: `ssh-keygen -t ed25519 -f signing.key -N ''`

### git_deploy_key

- **Purpose**: Clone Blueprint repository
- **Algorithm**: Ed25519 or RSA
- **Format**: SSH private key
- **Permissions**: 0600
- **Setup**: Add public key to Git repository deploy keys with read-only access

## Database Configuration

SQLite is configured with:

```python
PRAGMA journal_mode = WAL;           # Write-ahead logging
PRAGMA synchronous = NORMAL;         # Balance durability/performance
PRAGMA foreign_keys = ON;            # Enforce foreign key constraints
PRAGMA busy_timeout = 5000;          # Wait up to 5s for locks
```

## Data Retention

Controlled by Reaper component based on Blueprint config:

```json
{
  "spec": {
    "retention": {
      "logs_days": 30,
      "artifacts_days": 14,
      "registry_images_days": 30
    }
  }
}
```

- Logs older than `logs_days` deleted from disk and database
- Artifacts older than `artifacts_days` deleted from disk and database
- Registry images older than `registry_images_days` garbage collected

## Backup Recommendations

### SQLite Database

```bash
# Online backup (safe while Danube is running)
sqlite3 /var/lib/danube/danube.db ".backup /backup/danube.db"

# Or use file copy (must stop Danube first)
systemctl stop danube
cp /var/lib/danube/danube.db* /backup/
systemctl start danube
```

### Full Data Directory

```bash
# Backup everything
tar czf danube-backup-$(date +%Y%m%d).tar.gz /var/lib/danube/
```

### Encryption Keys

**Critical**: Backup `/var/lib/danube/keys/` securely. Without `encryption.key`, secrets cannot be decrypted.

## Migrations

Database migrations are SQL files applied with Alembic:

```bash
# Generate migration (manual SQL)
uv run alembic revision -m "Add new column"

# Apply migrations
uv run alembic upgrade head

# Rollback
uv run alembic downgrade -1
```

Migrations stored in `danube/db/migrations/versions/`.
