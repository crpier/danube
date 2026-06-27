"""Database engine wiring for Danube.

`open_database` is the single entrypoint that turns a path into a live, verified
`Database`: it connects, replays the migration chain, and verifies the live
schema against the models under the strict policy.

PRAGMAs: the data model requires `journal_mode=WAL`, `synchronous=NORMAL`,
`foreign_keys=ON`, and `busy_timeout=5000`. `sqlite.Config` exposes no pragma
settings; snekql applies (and re-asserts) all four on every pooled connection in
its connection-settings layer (`snekql/sqlite/settings.py`), so `open_database`
does not set them itself. WAL/synchronous are no-ops on `:memory:` databases.
"""

from pathlib import Path

from snekql import sqlite
from snekql.sqlite import Database

from danube.db.migrations import MIGRATIONS
from danube.db.models import MODELS


async def open_database(path: Path | str) -> Database:
    """Open, migrate, and verify the Danube database at ``path``.

    ``path`` is a filesystem path or the literal ``":memory:"``. The returned
    ``Database`` is an async context manager; close it with ``await db.close()``
    or use it under ``async with``.
    """
    # sqlite.Config.database is `Path | Literal[":memory:"]`: a bare path string
    # is rejected, so coerce filesystem paths to Path and pass ":memory:" through.
    database: Path | str = ":memory:" if path == ":memory:" else Path(path)
    db = await Database.initialize(sqlite.Config(database=database))
    await db.migrate(MIGRATIONS)
    await db.verify(MODELS, policy="strict")
    return db
