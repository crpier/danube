"""Domain enums whose values match the SQLite text stored by the persistence
layer. They subclass `enum.StrEnum` so each member compares equal to its DB
string and serializes back to the same text without conversion."""

from enum import StrEnum


class JobStatus(StrEnum):
    """Lifecycle state of a job, stored in `jobs.status`."""

    PENDING = "pending"
    SCHEDULING = "scheduling"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    """Lifecycle state of a single step, stored in `steps.status`."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


class TriggerType(StrEnum):
    """How a job was triggered, stored in `jobs.trigger_type`."""

    WEBHOOK = "webhook"
    CRON = "cron"
    MANUAL = "manual"


class PermissionLevel(StrEnum):
    """Access level a team holds on a pipeline, stored in
    `pipeline_permissions.level`. Sourced from the Blueprint `permissions` list."""

    ADMIN = "admin"
    WRITE = "write"
    READ = "read"
