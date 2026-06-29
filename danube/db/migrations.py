"""Ordered migration chain for the Danube schema.

`MIGRATIONS` is the sole schema-creation authority: a fresh database is built by
replaying this whole chain via `db.migrate(MIGRATIONS)`, and the result is checked
against the models with `db.verify(MODELS, policy="strict")`.

## Why literal SQL and not `scaffold(MODELS)`

snekql migration bodies run exactly one statement each (see snekql
`docs/migrations.md`): `scaffold(...)` is a *dev-time codegen helper* that emits
the initial `CREATE TABLE`/`CREATE INDEX` statements as text for you to paste in
and own — it is not meant to be handed to `db.migrate` as a single multi-statement
body (that raises `sqlite3: You can only execute one statement at a time`).

So each statement below is its own immutable, append-only migration. This is also
the safer contract: these bodies are frozen history. When the models in
`danube.db.models` change, `db.verify(...)` reports the drift and forces a new
`ALTER`-style migration appended here, rather than silently regenerating the
initial schema. **Never edit or rename an existing entry** — only append.

To regenerate this set from scratch (initial schema only), run
`scaffold(MODELS)` from `snekql.sqlite` and split its output on `;`.
"""

MIGRATIONS: dict[str, str] = {
    "0001_create_users": (
        'CREATE TABLE "users" ("id" TEXT PRIMARY KEY, "email" TEXT NOT NULL, '
        '"name" TEXT NOT NULL, "oidc_subject" TEXT, '
        "\"created_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
        "\"updated_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))) STRICT"
    ),
    "0002_index_ux_users_email": (
        'CREATE UNIQUE INDEX "ux_users_email" ON "users" ("email")'
    ),
    "0003_index_ux_users_oidc_subject": (
        'CREATE UNIQUE INDEX "ux_users_oidc_subject" ON "users" ("oidc_subject")'
    ),
    "0004_create_teams": (
        'CREATE TABLE "teams" ("id" TEXT PRIMARY KEY, "name" TEXT NOT NULL, '
        '"global_admin" INTEGER NOT NULL, '
        "\"created_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))) STRICT"
    ),
    "0005_index_ux_teams_name": (
        'CREATE UNIQUE INDEX "ux_teams_name" ON "teams" ("name")'
    ),
    "0006_create_team_members": (
        'CREATE TABLE "team_members" ("team_id" TEXT NOT NULL, '
        '"user_id" TEXT NOT NULL, "role" TEXT NOT NULL, '
        'PRIMARY KEY ("team_id", "user_id"), '
        'FOREIGN KEY ("team_id") REFERENCES "teams" ("id") ON DELETE CASCADE, '
        'FOREIGN KEY ("user_id") REFERENCES "users" ("id") ON DELETE CASCADE) STRICT'
    ),
    "0007_index_idx_team_members_user_id": (
        'CREATE INDEX "idx_team_members_user_id" ON "team_members" ("user_id")'
    ),
    "0008_create_pipelines": (
        'CREATE TABLE "pipelines" ("id" TEXT PRIMARY KEY, "name" TEXT NOT NULL, '
        '"team_id" TEXT NOT NULL, "repo_url" TEXT NOT NULL, "branch_filter" TEXT, '
        '"cron_schedule" TEXT, "config_path" TEXT NOT NULL, '
        '"worker_image" TEXT NOT NULL, "max_duration_seconds" INTEGER, '
        '"workspace_size_gb" INTEGER, '
        "\"created_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
        "\"updated_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
        'FOREIGN KEY ("team_id") REFERENCES "teams" ("id")) STRICT'
    ),
    "0009_create_pipeline_permissions": (
        'CREATE TABLE "pipeline_permissions" ("pipeline_id" TEXT NOT NULL, '
        '"team_id" TEXT NOT NULL, "level" TEXT NOT NULL, '
        'PRIMARY KEY ("pipeline_id", "team_id"), '
        'FOREIGN KEY ("pipeline_id") REFERENCES "pipelines" ("id") ON DELETE CASCADE, '
        'FOREIGN KEY ("team_id") REFERENCES "teams" ("id") ON DELETE CASCADE) STRICT'
    ),
    "0010_index_idx_pipeline_permissions_team_id": (
        'CREATE INDEX "idx_pipeline_permissions_team_id" '
        'ON "pipeline_permissions" ("team_id")'
    ),
    "0011_create_jobs": (
        'CREATE TABLE "jobs" ("id" TEXT PRIMARY KEY, "pipeline_id" TEXT NOT NULL, '
        '"trigger_type" TEXT NOT NULL, "trigger_ref" TEXT, "status" TEXT NOT NULL, '
        '"runner_id" TEXT, "workspace_path" TEXT, "started_at" TEXT, '
        '"finished_at" TEXT, "log_path" TEXT, "failure_reason" TEXT, '
        "\"created_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
        'FOREIGN KEY ("pipeline_id") REFERENCES "pipelines" ("id")) STRICT'
    ),
    "0012_index_idx_jobs_pipeline_id": (
        'CREATE INDEX "idx_jobs_pipeline_id" ON "jobs" ("pipeline_id")'
    ),
    "0013_index_idx_jobs_status": (
        'CREATE INDEX "idx_jobs_status" ON "jobs" ("status")'
    ),
    "0014_create_steps": (
        'CREATE TABLE "steps" ("id" TEXT PRIMARY KEY, "job_id" TEXT NOT NULL, '
        '"name" TEXT NOT NULL, "sequence" INTEGER NOT NULL, "command" TEXT NOT NULL, '
        '"status" TEXT NOT NULL, "exit_code" INTEGER, "started_at" TEXT, '
        '"finished_at" TEXT, "log_offset_start" INTEGER, "log_offset_end" INTEGER, '
        'FOREIGN KEY ("job_id") REFERENCES "jobs" ("id") ON DELETE CASCADE) STRICT'
    ),
    "0015_index_idx_steps_job_id": (
        'CREATE INDEX "idx_steps_job_id" ON "steps" ("job_id")'
    ),
    "0016_create_secrets": (
        'CREATE TABLE "secrets" ("id" TEXT PRIMARY KEY, "pipeline_id" TEXT, '
        '"key" TEXT NOT NULL, "value_encrypted" BLOB NOT NULL, '
        "\"created_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
        'FOREIGN KEY ("pipeline_id") REFERENCES "pipelines" ("id")) STRICT'
    ),
    "0017_index_ux_secrets_pipeline_id_key": (
        'CREATE UNIQUE INDEX "ux_secrets_pipeline_id_key" '
        'ON "secrets" ("pipeline_id", "key")'
    ),
    "0018_create_artifacts": (
        'CREATE TABLE "artifacts" ("id" TEXT PRIMARY KEY, "job_id" TEXT NOT NULL, '
        '"name" TEXT NOT NULL, "path" TEXT NOT NULL, "size_bytes" INTEGER NOT NULL, '
        "\"created_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
        'FOREIGN KEY ("job_id") REFERENCES "jobs" ("id") ON DELETE CASCADE) STRICT'
    ),
    "0019_index_ux_artifacts_job_id_name": (
        'CREATE UNIQUE INDEX "ux_artifacts_job_id_name" '
        'ON "artifacts" ("job_id", "name")'
    ),
    "0020_create_runner_state": (
        'CREATE TABLE "runner_state" ("id" TEXT PRIMARY KEY, "job_id" TEXT, '
        '"kind" TEXT NOT NULL, "external_id" TEXT NOT NULL, "status" TEXT NOT NULL, '
        "\"created_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
        "\"updated_at\" TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
        'FOREIGN KEY ("job_id") REFERENCES "jobs" ("id") ON DELETE CASCADE) STRICT'
    ),
    "0021_index_idx_runner_state_job_id": (
        'CREATE INDEX "idx_runner_state_job_id" ON "runner_state" ("job_id")'
    ),
    # Step Kind discriminator for Image Building (#47). Adding a NOT NULL column
    # via ALTER requires a *server* DEFAULT, but the model declares only a client
    # default (snekql v1 server defaults are CurrentTimestamp-only), and strict
    # verify rejects the mismatch. So rebuild `steps` the standard SQLite way:
    # create the table with the new `kind TEXT NOT NULL` (no server default),
    # copy the rows (backfilling 'run'), drop the old table, rename, reindex.
    # Nothing references `steps`, so the drop/rename is safe.
    "0022_create_steps_with_kind": (
        'CREATE TABLE "steps_new" ("id" TEXT PRIMARY KEY, "job_id" TEXT NOT NULL, '
        '"name" TEXT NOT NULL, "sequence" INTEGER NOT NULL, "command" TEXT NOT NULL, '
        '"kind" TEXT NOT NULL, "status" TEXT NOT NULL, "exit_code" INTEGER, '
        '"started_at" TEXT, "finished_at" TEXT, "log_offset_start" INTEGER, '
        '"log_offset_end" INTEGER, '
        'FOREIGN KEY ("job_id") REFERENCES "jobs" ("id") ON DELETE CASCADE) STRICT'
    ),
    "0023_copy_steps_into_kind_table": (
        'INSERT INTO "steps_new" '
        '("id", "job_id", "name", "sequence", "command", "kind", "status", '
        '"exit_code", "started_at", "finished_at", "log_offset_start", '
        '"log_offset_end") '
        'SELECT "id", "job_id", "name", "sequence", "command", \'run\', "status", '
        '"exit_code", "started_at", "finished_at", "log_offset_start", '
        '"log_offset_end" FROM "steps"'
    ),
    "0024_drop_old_steps": 'DROP TABLE "steps"',
    "0025_rename_steps_table": 'ALTER TABLE "steps_new" RENAME TO "steps"',
    "0026_index_idx_steps_job_id": (
        'CREATE INDEX "idx_steps_job_id" ON "steps" ("job_id")'
    ),
    # Job-level egress opt-in (#52, `docs/adr/0002-default-deny-egress.md`). A
    # plain nullable ADD COLUMN: the model declares `egress` `nullable=True` with a
    # client default, so no server DEFAULT is needed and strict verify stays clean.
    # Pre-existing rows read back NULL, which the Blueprint reconcile overwrites on
    # the next apply; `0` means default-deny.
    "0027_pipelines_add_egress": 'ALTER TABLE "pipelines" ADD COLUMN "egress" INTEGER',
}
