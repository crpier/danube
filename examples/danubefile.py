"""Minimal sample pipeline exercised end-to-end through the Coordinator SDK.

A real `danubefile.py` lives in the user's repository and is imported by the
Coordinator container, which calls `pipeline(DanubeClient.from_env())`. This
sample is deliberately small: it runs two build steps, fetches a secret and feeds
it to a command via per-call `env`, uploads an artifact, and reports success.

Remember each `step.run` is a fresh Worker shell — chain dependent commands with
`&&` rather than relying on a previous step's working directory
(`docs/architecture/execution-model.md`, Stateless Shell Execution).
"""

from danube.sdk import DanubeClient


async def pipeline(danube: DanubeClient) -> None:
    """Run the sample build/deploy pipeline against ``danube``.

    `step.run` raises on a non-zero exit by default, so the returned exit codes are
    intentionally discarded (assigned to ``_``).
    """
    _ = await danube.step.run("echo building && make build", name="build")

    deploy_token = await danube.secrets.get("DEPLOY_TOKEN")
    _ = await danube.step.run(
        "deploy --token $DEPLOY_TOKEN",
        env={"DEPLOY_TOKEN": deploy_token},
        name="deploy",
    )

    _ = await danube.artifacts.upload("dist/app.tar.gz", name="app-bundle")
    _ = await danube.status.report("success")
