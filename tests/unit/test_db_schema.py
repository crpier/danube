"""Schema-level tests: migrations apply and verify clean against the models."""

from snekql.sqlite import insert, select
from snektest import assert_eq, test

from danube.db import open_database
from danube.db.models import Job, Pipeline, Step, Team


@test(mark="fast")
async def test_migrate_and_verify_strict_passes() -> None:
    # open_database runs initialize -> migrate -> verify(policy="strict"); it
    # raises SchemaVerificationError on any drift, so reaching the round-trip
    # below is the assertion that the models and scaffolded schema agree.
    db = await open_database(":memory:")
    try:
        async with db.transaction() as tx:
            await tx.execute(insert(Team(id="team-0", name="ops", global_admin=False)))
            team = await tx.fetch_one(select(Team).where(Team.id.eq("team-0")))
        assert_eq(team.name, "ops")
    finally:
        await db.close()


@test(mark="fast")
async def test_pipeline_job_step_round_trip() -> None:
    db = await open_database(":memory:")
    try:
        async with db.transaction() as tx:
            await tx.execute(insert(Team(id="team-1", name="dev", global_admin=True)))
            await tx.execute(
                insert(
                    Pipeline(
                        id="pipe-1",
                        name="build",
                        team_id="team-1",
                        repo_url="https://example.test/repo.git",
                        worker_image="docker.io/library/python:3.14",
                    )
                )
            )
            await tx.execute(
                insert(
                    Job(
                        id="job-1",
                        pipeline_id="pipe-1",
                        trigger_type="manual",
                    )
                )
            )
            await tx.execute(
                insert(
                    Step(
                        id="step-1",
                        job_id="job-1",
                        name="compile",
                        sequence=1,
                        command="make",
                    )
                )
            )

        async with db.transaction() as tx:
            job = await tx.fetch_one(select(Job).where(Job.id.eq("job-1")))
            step = await tx.fetch_one(select(Step).where(Step.job_id.eq("job-1")))

        assert_eq(job.pipeline_id, "pipe-1")
        assert_eq(job.status, "pending")  # client default applied at construction
        assert_eq(step.name, "compile")
        assert_eq(step.sequence, 1)
        assert_eq(step.kind, "run")  # client default applied at construction
    finally:
        await db.close()


@test(mark="fast")
async def test_step_kind_build_round_trips() -> None:
    db = await open_database(":memory:")
    try:
        async with db.transaction() as tx:
            await tx.execute(insert(Team(id="team-2", name="ci", global_admin=True)))
            await tx.execute(
                insert(
                    Pipeline(
                        id="pipe-2",
                        name="image",
                        team_id="team-2",
                        repo_url="https://example.test/repo.git",
                        worker_image="docker.io/library/python:3.14",
                    )
                )
            )
            await tx.execute(
                insert(Job(id="job-2", pipeline_id="pipe-2", trigger_type="manual"))
            )
            await tx.execute(
                insert(
                    Step(
                        id="step-2",
                        job_id="job-2",
                        name="build-image",
                        sequence=1,
                        command="build app:abc",
                        kind="build",
                    )
                )
            )

        async with db.transaction() as tx:
            step = await tx.fetch_one(select(Step).where(Step.id.eq("step-2")))

        assert_eq(step.kind, "build")
    finally:
        await db.close()
