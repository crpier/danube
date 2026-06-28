"""BlueprintSyncer: poll the Blueprint repo and sync accepted changes to SQLite.

`BlueprintSyncer.sync()` is the single sync path -- fetch a checkout, parse and
validate it, and apply the result transactionally -- shared by the interval poll
loop and a manual trigger. Validation runs entirely before any write, so an
invalid Blueprint (bad schema or broken cross-reference) raises and leaves the
active configuration untouched. A successful sync logs the applied diff
(`blueprint_changes_applied_total{type=pipeline|user|team}`).

The poll loop follows the same owned-task-group pattern as `Scheduler`: the syncer
is an async context manager, and entering it starts a background task that calls
`sync()` every `sync_interval_seconds`, logging and swallowing failures so one bad
commit does not stop future syncs.
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import TYPE_CHECKING, Self

import anyio

from danube.blueprint.apply import apply_blueprint
from danube.blueprint.errors import BlueprintError
from danube.blueprint.parser import parse_blueprint

if TYPE_CHECKING:
    from anyio.abc import TaskGroup
    from snekql.sqlite import Database

    from danube.blueprint.apply import BlueprintDiff
    from danube.blueprint.source import BlueprintSource

logger = logging.getLogger("danube.blueprint")

DEFAULT_SYNC_INTERVAL_SECONDS = 60.0


class BlueprintSyncer:
    """Fetch, validate, and apply the Blueprint, on an interval or on demand."""

    def __init__(
        self,
        db: Database,
        source: BlueprintSource,
        *,
        sync_interval_seconds: float = DEFAULT_SYNC_INTERVAL_SECONDS,
    ) -> None:
        self._db = db
        self._source = source
        self._sync_interval_seconds = sync_interval_seconds
        self._task_group: TaskGroup | None = None

    async def __aenter__(self) -> Self:
        self._task_group = anyio.create_task_group()
        _ = await self._task_group.__aenter__()
        _ = self._task_group.start_soon(self._poll_loop)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        if self._task_group is None:
            return None
        self._task_group.cancel_scope.cancel()
        try:
            return await self._task_group.__aexit__(exc_type, exc, tb)
        finally:
            self._task_group = None

    async def sync(self) -> BlueprintDiff:
        """Fetch, validate, and apply the Blueprint once; return the diff.

        Raises `BlueprintValidationError` for an invalid Blueprint and
        `BlueprintSourceError` if the checkout fails; in both cases the database
        is left untouched.
        """
        checkout = await self._source.fetch()
        blueprint = parse_blueprint(checkout)
        diff = await apply_blueprint(self._db, blueprint)
        self._log_diff(diff)
        return diff

    def _log_diff(self, diff: BlueprintDiff) -> None:
        if diff.is_empty:
            logger.info("blueprint sync applied no changes")
            return
        logger.info(
            "blueprint sync applied changes: %s",
            diff.applied_counts(),
            extra={"blueprint_changes_applied_total": diff.applied_counts()},
        )
        for entity_type, change in (
            ("user", diff.users),
            ("team", diff.teams),
            ("pipeline", diff.pipelines),
        ):
            if change.total:
                logger.info(
                    "blueprint %s diff: added=%s updated=%s removed=%s",
                    entity_type,
                    change.added,
                    change.updated,
                    change.removed,
                )

    async def _poll_loop(self) -> None:
        while True:
            await anyio.sleep(self._sync_interval_seconds)
            try:
                _ = await self.sync()
            except BlueprintError:
                logger.exception("blueprint sync failed; keeping active config")
            except Exception:
                logger.exception("unexpected error during blueprint sync")
