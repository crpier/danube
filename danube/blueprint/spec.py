"""Pydantic models for the Blueprint JSON documents.

These mirror the declarative files documented in
`docs/configuration/blueprint-reference.md`: each file is a Kubernetes-style
document with `apiVersion`, `kind`, `metadata`, and `spec`. Pydantic is the JSON
Schema validator the issue calls for -- a missing required field or a wrong type
fails construction, which the parser turns into a `BlueprintValidationError`.

Only the fields Danube persists (users, teams, pipelines) are modelled precisely.
Unknown keys are ignored rather than rejected so the appliance does not break when
the Blueprint carries server/runner/networking settings this component does not
own (those live in `config.json` and have no SQLite table yet).

The wire `apiVersion` key is exposed as `api_version` via an alias to keep the
Python field snake_case; documents are validated with `populate_by_name` so the
JSON `apiVersion` key is accepted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from danube.domain.enums import PermissionLevel


class _Document(BaseModel):
    """Common envelope: every Blueprint file carries these top-level keys."""

    model_config = ConfigDict(populate_by_name=True)

    api_version: str = Field(alias="apiVersion")


class _NamedMetadata(BaseModel):
    name: str


class UserSpec(BaseModel):
    email: str
    password_hash: str | None = None


class UserDocument(_Document):
    kind: Literal["User"]
    metadata: _NamedMetadata
    spec: UserSpec


class TeamSpec(BaseModel):
    members: list[str] = Field(default_factory=list[str])
    global_admin: bool = False


class TeamDocument(_Document):
    kind: Literal["Team"]
    metadata: _NamedMetadata
    spec: TeamSpec


class PipelineMetadata(BaseModel):
    name: str
    team: str


class PipelineTrigger(BaseModel):
    on: str
    schedule: str | None = None
    branches: list[str] | None = None


class WorkerSpec(BaseModel):
    image: str


class PermissionSpec(BaseModel):
    team: str
    level: PermissionLevel


class PipelineSpec(BaseModel):
    repository: str
    branch_filter: list[str] | None = None
    triggers: list[PipelineTrigger] = Field(default_factory=list[PipelineTrigger])
    script: str = "danubefile.py"
    max_duration_seconds: int = 3600
    workspace_size_gb: int = 5
    worker: WorkerSpec
    permissions: list[PermissionSpec] = Field(default_factory=list[PermissionSpec])


class PipelineDocument(_Document):
    kind: Literal["Pipeline"]
    metadata: PipelineMetadata
    spec: PipelineSpec


class ConfigDocument(_Document):
    kind: Literal["Config"]
    metadata: _NamedMetadata
    # `spec` intentionally unmodelled: global server/runner/networking settings
    # have no SQLite table in this component, so they are validated only as a
    # present JSON object.
    spec: dict[str, object]
