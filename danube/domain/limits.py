"""Job resource limits and the operator-set Resource Ceiling (#53, part of #45).

A pipeline may request job resource limits in its Blueprint -- how much CPU, how
much memory, how many processes, and how long the job may run. The operator caps
those requests with a server-side Resource Ceiling: a `default` applied when the
pipeline asks for nothing, and a `max` every request is clamped down to.

`ResourceRequest` is the shared, runtime-agnostic shape for all three roles (a
pipeline request, the ceiling's `default`, and the ceiling's `max`). Fields are
canonical, human-meaningful units -- CPU in cores, memory in mebibytes, a process
count, and a wall-clock timeout in seconds -- so neither this module nor the config
layer needs to know about cgroup quotas or byte arithmetic; the runner converts at
the Podman boundary. Every field is optional: `None` means "unspecified", which
falls back to the ceiling default (in a request) or imposes no cap (in `max`).

`ResourceCeiling.resolve` is the single clamping rule, used by both the runner (for
the cgroup limits it enforces) and the orchestrator (for the timeout it enforces):
for each field take the request value or the default, then clamp to `max`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# Constrained over (int, float) so a clamp of all-int fields returns `int` and the
# float CPU field returns `float`, keeping the per-field value types exact.
def _clamp[N: (int, float)](
    requested: N | None, default: N | None, maximum: N | None
) -> N | None:
    """Resolve one field: request value or default, then clamped to the maximum.

    `None` request falls back to `default`; a `None` maximum imposes no cap.
    """
    value = requested if requested is not None else default
    if value is not None and maximum is not None:
        return min(value, maximum)
    return value


class ResourceRequest(BaseModel):
    """Requested (or default/max) job resource limits in canonical units.

    Immutable and rejects unknown fields so a typo in a Blueprint or server-config
    `[limits]` table fails loudly rather than being silently ignored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # CPU cores (fractions allowed, e.g. 1.5); memory in mebibytes; max process
    # count; wall-clock job timeout in seconds. The runner maps cores/MiB onto
    # cgroup quota/bytes; the orchestrator enforces the timeout.
    cpu: float | None = Field(default=None, gt=0)
    memory_mb: int | None = Field(default=None, gt=0)
    pids: int | None = Field(default=None, gt=0)
    timeout_seconds: int | None = Field(default=None, gt=0)


class ResourceCeiling(BaseModel):
    """Operator-set Resource Ceiling: the `default` and `max` for job limits.

    `default` fills any limit a pipeline does not request; `max` is the hard cap
    every resolved limit is clamped to. Configured via the server `[limits]` table
    (`docs/configuration/server-config.md`), never the Blueprint.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: ResourceRequest = Field(default_factory=ResourceRequest)
    max: ResourceRequest = Field(default_factory=ResourceRequest)

    @classmethod
    def from_table(cls, table: Mapping[str, Any] | None) -> ResourceCeiling:
        """Build from a server `[limits]` table, merged over `DEFAULT_CEILING`.

        The `default`/`max` sub-tables are merged field-by-field over the built-in
        ceiling, so an operator may set only the fields they care about (e.g. just
        `[limits.max]`) without dropping the built-in conservative `default`. An
        unknown key or a non-positive value fails loudly via the model validators.
        """
        table = table or {}
        return cls(
            default=_merge_request(DEFAULT_CEILING.default, table.get("default")),
            max=_merge_request(DEFAULT_CEILING.max, table.get("max")),
        )

    def resolve(self, request: ResourceRequest) -> ResourceRequest:
        """Apply the ceiling to ``request``: fall back to `default`, clamp to `max`.

        Returns the effective per-field limits. A field that is unspecified in the
        request, the default, and the max stays `None` (no limit enforced).
        """
        return ResourceRequest(
            cpu=_clamp(request.cpu, self.default.cpu, self.max.cpu),
            memory_mb=_clamp(
                request.memory_mb, self.default.memory_mb, self.max.memory_mb
            ),
            pids=_clamp(request.pids, self.default.pids, self.max.pids),
            timeout_seconds=_clamp(
                request.timeout_seconds,
                self.default.timeout_seconds,
                self.max.timeout_seconds,
            ),
        )


def _merge_request(
    base: ResourceRequest, table: Mapping[str, Any] | None
) -> ResourceRequest:
    """Overlay a config sub-table onto a base request, keeping base where unset."""
    if not table:
        return base
    override = ResourceRequest.model_validate(table)
    set_fields = {k: v for k, v in override.model_dump().items() if v is not None}
    return base.model_copy(update=set_fields)


# Built-in ceiling used when the operator configures no `[limits]` table. The
# `default` mirrors the runner's historical conservative caps (2 CPUs, 2 GiB, 512
# pids, a 1-hour timeout), so a job that requests nothing is limited exactly as
# before this feature landed. `max` is unset (no cap), so existing behaviour is
# unchanged until an operator opts into a ceiling.
DEFAULT_CEILING = ResourceCeiling(
    default=ResourceRequest(cpu=2.0, memory_mb=2048, pids=512, timeout_seconds=3600),
    max=ResourceRequest(),
)
