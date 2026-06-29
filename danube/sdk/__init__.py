"""Danube Coordinator SDK: the API a `danubefile.py` pipeline uses.

Pipeline code drives a job through a `DanubeClient`, whose namespaces mirror the
control-plane operations:

    danube = DanubeClient.from_env()
    await danube.step.run("npm ci && npm test")
    image = await danube.images.build(f"myapp:{danube.context.sha or 'latest'}")
    token = await danube.secrets.get("DEPLOY_TOKEN")
    await danube.artifacts.upload("dist/app.tar.gz", name="app-bundle")
    await danube.status.report("success")

The read-only `context` namespace carries the run metadata the Master plumbs in
(`danube.context.sha` / `.branch` / `.pipeline` / `.trigger_type`), so a pipeline
can disambiguate builds without an extra RPC.

Every command runs in a fresh Worker shell (see `StepApi`). On the Coordinator
image this package is published as the top-level ``danube`` distribution; in this
monorepo it lives at `danube.sdk` alongside the Master.
"""

from danube.sdk.client import (
    ArtifactsApi,
    BuildError,
    BuiltImage,
    DanubeClient,
    ImagesApi,
    JobContext,
    RpcClient,
    RpcError,
    SecretsApi,
    StatusApi,
    StepApi,
    StepError,
    StepResult,
    UploadedArtifact,
)

__all__ = [
    "ArtifactsApi",
    "BuildError",
    "BuiltImage",
    "DanubeClient",
    "ImagesApi",
    "JobContext",
    "RpcClient",
    "RpcError",
    "SecretsApi",
    "StatusApi",
    "StepApi",
    "StepError",
    "StepResult",
    "UploadedArtifact",
]
