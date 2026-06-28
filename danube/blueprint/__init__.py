"""Blueprint Syncer: GitOps configuration sync into SQLite.

Polls the Blueprint Git repository for declarative JSON config, validates it
(schema plus cross-references), and applies accepted changes transactionally into
the snekql models. A failed sync leaves the active configuration untouched. See
`docs/architecture/components.md` (Blueprint Syncer) and
`docs/configuration/blueprint-reference.md`.
"""

from danube.blueprint.apply import (
    BlueprintDiff,
    EntityChange,
    apply_blueprint,
)
from danube.blueprint.errors import (
    BlueprintError,
    BlueprintSourceError,
    BlueprintValidationError,
)
from danube.blueprint.parser import Blueprint, parse_blueprint
from danube.blueprint.source import BlueprintSource, GitBlueprintSource
from danube.blueprint.syncer import BlueprintSyncer

__all__ = [
    "Blueprint",
    "BlueprintDiff",
    "BlueprintError",
    "BlueprintSource",
    "BlueprintSourceError",
    "BlueprintSyncer",
    "BlueprintValidationError",
    "EntityChange",
    "GitBlueprintSource",
    "apply_blueprint",
    "parse_blueprint",
]
