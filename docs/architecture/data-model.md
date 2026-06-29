# Data Model

## SQLite Schema

### Core Tables

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    oidc_subject TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE teams (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    global_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE team_members (
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE pipelines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(id),
    repo_url TEXT NOT NULL,
    branch_filter TEXT,
    cron_schedule TEXT,
    config_path TEXT NOT NULL DEFAULT 'danubefile.py',
    worker_image TEXT NOT NULL,
    max_duration_seconds INTEGER DEFAULT 3600,
    workspace_size_gb INTEGER DEFAULT 5,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE pipeline_permissions (
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    PRIMARY KEY (pipeline_id, team_id)
);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL REFERENCES pipelines(id),
    trigger_type TEXT NOT NULL,
    trigger_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    runner_id TEXT,
    workspace_path TEXT,
    started_at TEXT,
    finished_at TEXT,
    log_path TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE steps (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    command TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'run',  -- 'run' | 'build' | 'push' (Step Kind)
    status TEXT NOT NULL DEFAULT 'pending',
    exit_code INTEGER,
    started_at TEXT,
    finished_at TEXT,
    log_offset_start INTEGER,
    log_offset_end INTEGER
);

CREATE TABLE secrets (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT REFERENCES pipelines(id),
    key TEXT NOT NULL,
    value_encrypted BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(pipeline_id, key)
);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(job_id, name)
);

CREATE TABLE runner_state (
    id TEXT PRIMARY KEY,
    job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    external_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_jobs_pipeline_id ON jobs(pipeline_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_steps_job_id ON steps(job_id);
CREATE INDEX idx_team_members_user_id ON team_members(user_id);
CREATE INDEX idx_pipeline_permissions_team_id ON pipeline_permissions(team_id);
CREATE INDEX idx_runner_state_job_id ON runner_state(job_id);
```

## Filesystem Layout

```text
/var/lib/danube/
├── danube.db
├── danube.db-wal
├── danube.db-shm
├── logs/
│   └── <job_id>.log
├── artifacts/
│   └── <job_id>/
│       ├── <artifact1>.tar.gz
│       ├── provenance.json
│       └── provenance.sig
├── workspaces/
│   └── <job_id>/
│       └── workspace files during execution
├── registry/
│   └── local image registry/cache data
└── keys/
    ├── encryption.key
    ├── signing.key
    ├── signing.key.pub
    ├── git_deploy_key
    └── git_deploy_key.pub
```

## Key Files

### encryption.key

- **Purpose**: Symmetric encryption for secrets in SQLite
- **Algorithm**: AES-256-GCM
- **Format**: Raw 32-byte binary
- **Permissions**: 0600
- **Generation**: `openssl rand -out encryption.key 32`

### signing.key

- **Purpose**: Sign provenance documents
- **Algorithm**: Ed25519
- **Format**: PEM private key or OpenSSH key, depending on implementation
- **Permissions**: 0600

### git_deploy_key

- **Purpose**: Clone Blueprint repository
- **Permissions**: 0600
- **Setup**: Add public key to Blueprint repository deploy keys with read-only access

## Database Configuration

SQLite is configured with:

```python
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

## Data Retention

Controlled by Blueprint config:

```json
{
  "spec": {
    "retention": {
      "logs_days": 30,
      "artifacts_days": 14,
      "registry_images_days": 30,
      "workspaces_days": 0
    }
  }
}
```

- Logs older than `logs_days` are deleted from disk and database references.
- Artifacts older than `artifacts_days` are deleted from disk and database.
- Cached images older than `registry_images_days` are pruned.
- Workspaces are normally deleted immediately; preserved workspaces are reaped by `workspaces_days`.
- Stale runner state is periodically reconciled against Podman resources.

## Backup Recommendations

### SQLite Database

```bash
sqlite3 /var/lib/danube/danube.db ".backup /backup/danube.db"
```

### Full Data Directory

```bash
tar czf danube-backup-$(date +%Y%m%d).tar.gz /var/lib/danube/
```

### Keys

Back up `/var/lib/danube/keys/` securely. Without `encryption.key`, stored secrets cannot be decrypted.

## Migrations

Schema is owned by `snekql`. Tables are declared as `snekql` `Model` classes
(see the SQLite schema above for the canonical shape), and the live schema is
built by applying an ordered set of migrations, then verified against the
models.

```python
from snekql.sqlite import Database

async with await Database.initialize(sqlite.Config(database=db_path)) as db:
    # Migrations are an ordered dict[str, str] of name -> raw SQL body, each
    # applied exactly once and recorded in snekql's Migration History. A fresh
    # database is built by replaying the whole chain.
    #
    # GOTCHA: each migration body runs exactly ONE statement (snekql calls
    # `connection.execute` per body). A multi-statement `scaffold([...])` blob
    # passed as a single body silently runs only its first statement. So the
    # chain is a literal, one-statement-per-entry `dict[str, str]` (one CREATE
    # TABLE or CREATE INDEX each) rather than a single scaffolded body.
    #
    # `scaffold([...])` is still the source of the SQL: its output is split on
    # `;` and each statement becomes its own append-only entry. Migrations are
    # immutable once written — never edit or rename an applied one; append a new
    # entry for any change.
    await db.migrate(MIGRATIONS)  # danube.db.migrations.MIGRATIONS
    # Structural check between models and the live schema; raises on drift.
    await db.verify(MODELS, policy="strict")  # danube.db.models.MODELS
```

- The danube entrypoint is `danube.db.open_database(path)`, which runs
  `initialize -> migrate -> verify(policy="strict")`.
- Migration deploys run `initialize -> migrate -> verify`.
- Read-only replicas / app startup that assumes an already-migrated schema run
  `initialize -> verify`.
- `verify` compares columns, indexes, foreign keys, and storage options; it
  cannot see default values, `CHECK` constraints, triggers, or data. Constraints
  that `verify` cannot inspect must be asserted by tests.
- Migration bodies live under the backend database package as the canonical
  ordered migration set passed to `db.migrate`.
