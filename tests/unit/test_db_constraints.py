"""Constraint tests for behaviour snekql `verify` cannot see: FK enforcement,
referential cascade, composite primary keys, and unique indexes."""

from snekql.sqlite import ExecutionError, delete, insert, select
from snektest import assert_eq, assert_raises, test

from danube.db import open_database
from danube.db.models import (
    Job,
    Pipeline,
    Secret,
    Step,
    Team,
    TeamMember,
    User,
)


@test(mark="fast")
async def test_bad_foreign_key_insert_fails() -> None:
    db = await open_database(":memory:")
    try:
        with assert_raises(ExecutionError):
            async with db.transaction() as tx:
                # No pipeline "ghost" exists -> FK enforcement rejects the job.
                await tx.execute(
                    insert(Job(id="job-x", pipeline_id="ghost", trigger_type="manual"))
                )
    finally:
        await db.close()


@test(mark="fast")
async def test_duplicate_secret_pipeline_key_violates_unique() -> None:
    db = await open_database(":memory:")
    try:
        async with db.transaction() as tx:
            await tx.execute(insert(Team(id="t1", name="t", global_admin=False)))
            await tx.execute(
                insert(
                    Pipeline(
                        id="p1",
                        name="n",
                        team_id="t1",
                        repo_url="r",
                        worker_image="img",
                    )
                )
            )
            await tx.execute(
                insert(
                    Secret(id="s1", pipeline_id="p1", key="API", value_encrypted=b"a")
                )
            )

        with assert_raises(ExecutionError):
            async with db.transaction() as tx:
                await tx.execute(
                    insert(
                        Secret(
                            id="s2", pipeline_id="p1", key="API", value_encrypted=b"b"
                        )
                    )
                )
    finally:
        await db.close()


@test(mark="fast")
async def test_composite_primary_key_rejects_duplicate_pair() -> None:
    db = await open_database(":memory:")
    try:
        async with db.transaction() as tx:
            await tx.execute(insert(User(id="u1", email="u@x", name="U")))
            await tx.execute(insert(Team(id="t1", name="t", global_admin=False)))
            await tx.execute(insert(TeamMember(team_id="t1", user_id="u1")))

        with assert_raises(ExecutionError):
            async with db.transaction() as tx:
                # Same (team_id, user_id) pair -> composite PK violation.
                await tx.execute(insert(TeamMember(team_id="t1", user_id="u1")))
    finally:
        await db.close()


@test(mark="fast")
async def test_on_delete_cascade_removes_children() -> None:
    db = await open_database(":memory:")
    try:
        async with db.transaction() as tx:
            await tx.execute(insert(Team(id="t1", name="t", global_admin=False)))
            await tx.execute(
                insert(
                    Pipeline(
                        id="p1",
                        name="n",
                        team_id="t1",
                        repo_url="r",
                        worker_image="img",
                    )
                )
            )
            await tx.execute(
                insert(Job(id="j1", pipeline_id="p1", trigger_type="manual"))
            )
            await tx.execute(
                insert(Step(id="st1", job_id="j1", name="s", sequence=1, command="c"))
            )

        # Deleting the parent job cascades to its steps (FK ON DELETE CASCADE,
        # foreign_keys=ON applied by snekql).
        async with db.transaction() as tx:
            await tx.execute(delete(Job).where(Job.id.eq("j1")))

        async with db.transaction() as tx:
            remaining = await tx.fetch_all(select(Step).where(Step.job_id.eq("j1")))

        assert_eq(len(remaining), 0)
    finally:
        await db.close()
