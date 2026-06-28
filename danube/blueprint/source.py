"""Blueprint source: where the syncer gets a local checkout of the config repo.

`BlueprintSource` is the small interface the syncer depends on -- ``fetch()``
returns a local directory holding the current Blueprint files. Keeping it a
protocol lets the parse/apply logic be tested against a plain directory while the
real `GitBlueprintSource` is covered by a Git integration test.

`GitBlueprintSource` clones the configured repository on first fetch and
fast-forward pulls it thereafter, driving the `git` CLI through an asyncio
subprocess (the "or equivalent" the issue allows, with no extra dependency). The
git executable is resolved to an absolute path up front so a partial-path lookup
cannot pick up an attacker-controlled binary.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from danube.blueprint.errors import BlueprintSourceError

if TYPE_CHECKING:
    from pathlib import Path


@runtime_checkable
class BlueprintSource(Protocol):
    """A source of Blueprint files, resolvable to a local checkout directory."""

    async def fetch(self) -> Path:
        """Return a local directory holding the current Blueprint files."""
        ...


class GitBlueprintSource:
    """Clone/pull a Git Blueprint repository into a local checkout directory."""

    def __init__(self, repo_url: str, checkout_dir: Path) -> None:
        self._repo_url = repo_url
        self._checkout_dir = checkout_dir

    async def fetch(self) -> Path:
        """Clone the repo on first call, fast-forward pull on later calls.

        Returns the checkout directory. Raises `BlueprintSourceError` if the
        underlying git command fails.
        """
        if (self._checkout_dir / ".git").is_dir():
            await self._git("-C", str(self._checkout_dir), "pull", "--ff-only")
        else:
            self._checkout_dir.parent.mkdir(parents=True, exist_ok=True)
            await self._git("clone", self._repo_url, str(self._checkout_dir))
        return self._checkout_dir

    async def _git(self, *args: str) -> None:
        git = shutil.which("git")
        if git is None:
            msg = "git executable not found on PATH"
            raise BlueprintSourceError(msg)
        process = await asyncio.create_subprocess_exec(
            git,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            msg = f"git {args[0]} failed ({process.returncode}): {detail}"
            raise BlueprintSourceError(msg)
