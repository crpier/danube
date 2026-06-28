"""Exceptions raised while syncing the Blueprint repository.

`BlueprintValidationError` carries every problem found in one pass (a list of
messages) so an operator sees all schema/cross-reference failures at once rather
than fixing them one commit at a time. A validation failure aborts the whole
sync before any database write, so the active configuration is left untouched.
"""

from __future__ import annotations


class BlueprintError(Exception):
    """Base class for every Blueprint sync failure."""


class BlueprintValidationError(BlueprintError):
    """A Blueprint failed schema or cross-reference validation.

    ``problems`` lists every issue found in the failing checkout; the message
    joins them for display.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


class BlueprintSourceError(BlueprintError):
    """Fetching the Blueprint repository (clone/pull) failed."""
