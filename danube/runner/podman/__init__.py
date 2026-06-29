"""Podman (libpod) adapter: the only Danube module that speaks Podman.

Everything above the adapter depends on the `PodmanAPI` protocol and the small
Danube spec/result dataclasses defined here, never on Podman wire formats.
"""

from danube.runner.podman.adapter import (
    DEFAULT_API_VERSION,
    BuildResult,
    BuildSpec,
    ContainerSpec,
    ExecInspect,
    ExecOutput,
    ExecSpec,
    Mount,
    PodmanAdapter,
    PodmanAPI,
    PodmanError,
    PodmanVersion,
    PodSpec,
    ResourceLimits,
    ResourceSummary,
    build_async_client,
    default_socket_path,
    demultiplex_stream,
    parse_build_stream,
    socket_available,
    tar_build_context,
)

__all__ = [
    "DEFAULT_API_VERSION",
    "BuildResult",
    "BuildSpec",
    "ContainerSpec",
    "ExecInspect",
    "ExecOutput",
    "ExecSpec",
    "Mount",
    "PodSpec",
    "PodmanAPI",
    "PodmanAdapter",
    "PodmanError",
    "PodmanVersion",
    "ResourceLimits",
    "ResourceSummary",
    "build_async_client",
    "default_socket_path",
    "demultiplex_stream",
    "parse_build_stream",
    "socket_available",
    "tar_build_context",
]
