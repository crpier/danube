"""snekql models for the Danube persistence schema.

These classes are the single source of truth for the SQLite schema documented in
`docs/architecture/data-model.md`. The live schema is built by replaying the
migration chain (`danube.db.migrations`) and checked against these models with
`db.verify(MODELS, policy="strict")`.

Notes on the snekql mapping:

- TEXT UUID primary keys are application-generated, so `id` is a plain `Col[str]`
  with `primary_key=True` (no `auto_increment`, no server default).
- Timestamp columns are server-generated via `sqlite.CurrentTimestamp`, so they
  are `GenCol[datetime]`.
- Literal SQL defaults (`'member'`, `3600`, ...) map to snekql *client* defaults
  applied at model construction. snekql v1 only supports `CurrentTimestamp` as a
  server default, and `verify` cannot see defaults at all, so this is purely a
  construction-time convenience and is invisible to drift checks.
- `__tablename__` is set explicitly to the documented plural names; snekql would
  otherwise derive singular snake_case names from the class names.
"""

from datetime import datetime
from typing import Any, ClassVar

from snekql import sqlite
from snekql.sqlite import Fetched, Pending


class User[S = Pending](sqlite.Model[S, "User[Fetched]"]):
    __tablename__ = "users"

    id: User.Col[str] = sqlite.Text(primary_key=True)
    email: User.Col[str] = sqlite.Text(nullable=False, unique=True)
    name: User.Col[str] = sqlite.Text(nullable=False)
    oidc_subject: User.Col[str | None] = sqlite.Text(
        nullable=True, unique=True, default=None
    )
    created_at: User.GenCol[datetime] = sqlite.Text(default=sqlite.CurrentTimestamp)
    updated_at: User.GenCol[datetime] = sqlite.Text(default=sqlite.CurrentTimestamp)


class Team[S = Pending](sqlite.Model[S, "Team[Fetched]"]):
    __tablename__ = "teams"

    id: Team.Col[str] = sqlite.Text(primary_key=True)
    name: Team.Col[str] = sqlite.Text(nullable=False, unique=True)
    global_admin: Team.Col[bool] = sqlite.Integer(nullable=False, default=False)
    created_at: Team.GenCol[datetime] = sqlite.Text(default=sqlite.CurrentTimestamp)


class TeamMember[S = Pending](sqlite.Model[S, "TeamMember[Fetched]"]):
    __tablename__ = "team_members"

    team_id: TeamMember.FKCol[Team, str] = sqlite.ForeignKey(
        Team.id, primary_key=True, on_delete="CASCADE"
    )
    user_id: TeamMember.FKCol[User, str] = sqlite.ForeignKey(
        User.id, primary_key=True, on_delete="CASCADE"
    )
    role: TeamMember.Col[str] = sqlite.Text(nullable=False, default="member")

    __indexes__: ClassVar[list[sqlite.Index[Any]]] = [
        sqlite.Index(user_id, name="idx_team_members_user_id")
    ]


class Pipeline[S = Pending](sqlite.Model[S, "Pipeline[Fetched]"]):
    __tablename__ = "pipelines"

    id: Pipeline.Col[str] = sqlite.Text(primary_key=True)
    name: Pipeline.Col[str] = sqlite.Text(nullable=False)
    team_id: Pipeline.FKCol[Team, str] = sqlite.ForeignKey(Team.id, nullable=False)
    repo_url: Pipeline.Col[str] = sqlite.Text(nullable=False)
    branch_filter: Pipeline.Col[str | None] = sqlite.Text(nullable=True, default=None)
    cron_schedule: Pipeline.Col[str | None] = sqlite.Text(nullable=True, default=None)
    config_path: Pipeline.Col[str] = sqlite.Text(
        nullable=False, default="danubefile.py"
    )
    worker_image: Pipeline.Col[str] = sqlite.Text(nullable=False)
    # Value type is non-optional `int`: snekql pins it to the (non-None) default,
    # and the column is never read back as NULL because the default always fills
    # it. The column stays DB-nullable (`nullable=True`) to match data-model.md.
    max_duration_seconds: Pipeline.Col[int] = sqlite.Integer(
        nullable=True, default=3600
    )
    workspace_size_gb: Pipeline.Col[int] = sqlite.Integer(nullable=True, default=5)
    created_at: Pipeline.GenCol[datetime] = sqlite.Text(default=sqlite.CurrentTimestamp)
    updated_at: Pipeline.GenCol[datetime] = sqlite.Text(default=sqlite.CurrentTimestamp)


class PipelinePermission[S = Pending](sqlite.Model[S, "PipelinePermission[Fetched]"]):
    __tablename__ = "pipeline_permissions"

    pipeline_id: PipelinePermission.FKCol[Pipeline, str] = sqlite.ForeignKey(
        Pipeline.id, primary_key=True, on_delete="CASCADE"
    )
    team_id: PipelinePermission.FKCol[Team, str] = sqlite.ForeignKey(
        Team.id, primary_key=True, on_delete="CASCADE"
    )
    level: PipelinePermission.Col[str] = sqlite.Text(nullable=False)

    __indexes__: ClassVar[list[sqlite.Index[Any]]] = [
        sqlite.Index(team_id, name="idx_pipeline_permissions_team_id")
    ]


class Job[S = Pending](sqlite.Model[S, "Job[Fetched]"]):
    __tablename__ = "jobs"

    id: Job.Col[str] = sqlite.Text(primary_key=True)
    pipeline_id: Job.FKCol[Pipeline, str] = sqlite.ForeignKey(
        Pipeline.id, nullable=False
    )
    trigger_type: Job.Col[str] = sqlite.Text(nullable=False)
    trigger_ref: Job.Col[str | None] = sqlite.Text(nullable=True, default=None)
    status: Job.Col[str] = sqlite.Text(nullable=False, default="pending")
    runner_id: Job.Col[str | None] = sqlite.Text(nullable=True, default=None)
    workspace_path: Job.Col[str | None] = sqlite.Text(nullable=True, default=None)
    started_at: Job.Col[datetime | None] = sqlite.Text(nullable=True, default=None)
    finished_at: Job.Col[datetime | None] = sqlite.Text(nullable=True, default=None)
    log_path: Job.Col[str | None] = sqlite.Text(nullable=True, default=None)
    failure_reason: Job.Col[str | None] = sqlite.Text(nullable=True, default=None)
    created_at: Job.GenCol[datetime] = sqlite.Text(default=sqlite.CurrentTimestamp)

    __indexes__: ClassVar[list[sqlite.Index[Any]]] = [
        sqlite.Index(pipeline_id, name="idx_jobs_pipeline_id"),
        sqlite.Index(status, name="idx_jobs_status"),
    ]


class Step[S = Pending](sqlite.Model[S, "Step[Fetched]"]):
    __tablename__ = "steps"

    id: Step.Col[str] = sqlite.Text(primary_key=True)
    job_id: Step.FKCol[Job, str] = sqlite.ForeignKey(
        Job.id, nullable=False, on_delete="CASCADE"
    )
    name: Step.Col[str] = sqlite.Text(nullable=False)
    sequence: Step.Col[int] = sqlite.Integer(nullable=False)
    command: Step.Col[str] = sqlite.Text(nullable=False)
    # `run` | `build` | `push` discriminator (`danube.domain.enums.StepKind`); a
    # client default fills it on plain command steps so callers stay unchanged.
    kind: Step.Col[str] = sqlite.Text(nullable=False, default="run")
    status: Step.Col[str] = sqlite.Text(nullable=False, default="pending")
    exit_code: Step.Col[int | None] = sqlite.Integer(nullable=True, default=None)
    started_at: Step.Col[datetime | None] = sqlite.Text(nullable=True, default=None)
    finished_at: Step.Col[datetime | None] = sqlite.Text(nullable=True, default=None)
    log_offset_start: Step.Col[int | None] = sqlite.Integer(nullable=True, default=None)
    log_offset_end: Step.Col[int | None] = sqlite.Integer(nullable=True, default=None)

    __indexes__: ClassVar[list[sqlite.Index[Any]]] = [
        sqlite.Index(job_id, name="idx_steps_job_id")
    ]


class Secret[S = Pending](sqlite.Model[S, "Secret[Fetched]"]):
    __tablename__ = "secrets"

    id: Secret.Col[str] = sqlite.Text(primary_key=True)
    pipeline_id: Secret.FKCol[Pipeline, str | None] = sqlite.ForeignKey(
        Pipeline.id, nullable=True, default=None
    )
    key: Secret.Col[str] = sqlite.Text(nullable=False)
    value_encrypted: Secret.Col[bytes] = sqlite.Blob(nullable=False)
    created_at: Secret.GenCol[datetime] = sqlite.Text(default=sqlite.CurrentTimestamp)

    __indexes__: ClassVar[list[sqlite.Index[Any]]] = [
        sqlite.Index(pipeline_id, key, unique=True, name="ux_secrets_pipeline_id_key")
    ]


class Artifact[S = Pending](sqlite.Model[S, "Artifact[Fetched]"]):
    __tablename__ = "artifacts"

    id: Artifact.Col[str] = sqlite.Text(primary_key=True)
    job_id: Artifact.FKCol[Job, str] = sqlite.ForeignKey(
        Job.id, nullable=False, on_delete="CASCADE"
    )
    name: Artifact.Col[str] = sqlite.Text(nullable=False)
    path: Artifact.Col[str] = sqlite.Text(nullable=False)
    size_bytes: Artifact.Col[int] = sqlite.Integer(nullable=False)
    created_at: Artifact.GenCol[datetime] = sqlite.Text(default=sqlite.CurrentTimestamp)

    __indexes__: ClassVar[list[sqlite.Index[Any]]] = [
        sqlite.Index(job_id, name, unique=True, name="ux_artifacts_job_id_name")
    ]


class RunnerState[S = Pending](sqlite.Model[S, "RunnerState[Fetched]"]):
    __tablename__ = "runner_state"

    id: RunnerState.Col[str] = sqlite.Text(primary_key=True)
    job_id: RunnerState.FKCol[Job, str | None] = sqlite.ForeignKey(
        Job.id, nullable=True, on_delete="CASCADE", default=None
    )
    kind: RunnerState.Col[str] = sqlite.Text(nullable=False)
    external_id: RunnerState.Col[str] = sqlite.Text(nullable=False)
    status: RunnerState.Col[str] = sqlite.Text(nullable=False)
    created_at: RunnerState.GenCol[datetime] = sqlite.Text(
        default=sqlite.CurrentTimestamp
    )
    updated_at: RunnerState.GenCol[datetime] = sqlite.Text(
        default=sqlite.CurrentTimestamp
    )

    __indexes__: ClassVar[list[sqlite.Index[Any]]] = [
        sqlite.Index(job_id, name="idx_runner_state_job_id")
    ]


# Ordered parents-before-children so `scaffold(MODELS)` emits FK targets first and
# `verify(MODELS, ...)` checks the full set.
MODELS = [
    User,
    Team,
    TeamMember,
    Pipeline,
    PipelinePermission,
    Job,
    Step,
    Secret,
    Artifact,
    RunnerState,
]
