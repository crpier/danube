"""Serve the built frontend SPA from the Master's FastAPI app.

`mount_spa` wires a directory of built static assets (the frontend `dist/`) onto
the app so the Master answers `/` and any client-side route with the SPA's
`index.html`, while real files under the directory (JS/CSS/etc.) are served
directly. It is mounted *after* every API router, so the explicit JSON routes
always win; the SPA catch-all only fires for otherwise-unmatched paths.

Paths owned by the backend (`/api`, `/health`, `/metrics`, `/rpc`, `/webhooks`)
are answered 404 by the fallback rather than being handed the SPA shell, so a
bad API request still reads as a 404 to the client instead of an HTML page.

When the directory holds no `index.html` (no build present) `mount_spa` is a
no-op: `/` stays unmounted (404), which keeps API-only and development runs
working without a frontend build.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse

# Path prefixes the backend owns; the SPA fallback must never answer these. Bare
# names (no trailing slash) cover both the exact route and its sub-paths
# (`health`, `health/ready`); `api/` etc. carry the slash since they are always
# nested.
_RESERVED_PREFIXES = ("api", "health", "metrics", "rpc/", "webhooks/")


def _resolve_within(root: Path, relative: str) -> Path | None:
    """Resolve `relative` under `root`, or `None` if it escapes the directory.

    Guards against path traversal (`../`) so the static file server can only
    hand out files that actually live inside the built SPA directory.
    """
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if candidate == root_resolved or root_resolved in candidate.parents:
        return candidate
    return None


def mount_spa(app: FastAPI, directory: Path) -> None:
    """Serve the built SPA in `directory` at `/` with client-side-route fallback.

    No-op when `directory/index.html` is absent.
    """
    index = directory / "index.html"
    if not index.is_file():
        return

    async def serve_index() -> FileResponse:
        return FileResponse(index)

    async def serve_path(full_path: str) -> FileResponse:
        if full_path.startswith(_RESERVED_PREFIXES):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        candidate = _resolve_within(directory, full_path)
        if candidate is not None and candidate.is_file():
            return FileResponse(candidate)
        # Any other path is a client-side route: hand back the SPA shell.
        return FileResponse(index)

    app.add_api_route("/", serve_index, include_in_schema=False)
    app.add_api_route("/{full_path:path}", serve_path, include_in_schema=False)
