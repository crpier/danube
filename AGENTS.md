# AGENTS

## Project Summary

Danube is a self-hosted, single-host CI/CD appliance built in Python. It runs a Master process that orchestrates ephemeral rootless Podman job pods through a runner abstraction. Each job uses a Coordinator container for Python pipeline code and a Worker container for build commands. Configuration is managed via a GitOps Blueprint repository.

## Key Documentation

- Architecture overview: `docs/architecture/overview.md`
- Components: `docs/architecture/components.md`
- Execution model: `docs/architecture/execution-model.md`
- Local runner: `docs/architecture/local-runner.md`
- Security: `docs/architecture/security.md`
- Observability: `docs/architecture/observability.md`
- Networking: `docs/architecture/networking.md`
- Data model: `docs/architecture/data-model.md`
- Blueprint config: `docs/configuration/blueprint-reference.md`
- Server config: `docs/configuration/server-config.md`

## Development Notes

- Python 3.14+, UV, and snektest are standard tools.
- Follow the architecture docs before implementing new components.
- If the architecture of the application changes during implementation, update the docs accordingly.

## GitHub workflow

- This project uses GitHub issues for tracking work.
- Do not hand-edit GitHub URLs or assume issue state; query with `gh issue view/list` when needed.
- Implementation work should reference the relevant GitHub issue.
- When starting a new unit of work, stash any uncommitted changes, run `git fetch`, then create a new branch from the latest `origin/main`.
- All work should be done in a branch, and when a unit of work is complete, open a PR against `main`. Only merge the PR if explicitly told to do so.
- When creating or editing a PR body with `gh`, write the markdown to a temporary file and use `--body-file`; do not pass multiline markdown through `--body`. Verify the rendered body with `gh pr view` afterward.
- When doing feature/bug-fixing/refactoring or any code-related work, use TDD.

## Testing and validation

- Use `snektest` for tests.
  - For snektest usage documentation, read its installed distribution metadata with `importlib.metadata.distribution("snektest").read_text("METADATA")`; the `METADATA` file embeds snektest's README.
- Use `pyright` for static typing validation.
- Use `ruff` for linting and formatting checks.

### Validation gate (keep `main` clean)

- `main` must always pass every check. Never commit, push, open, or merge a PR until all of the checks below pass clean from the repo root:
  - `uv run pyright` — 0 errors.
  - `uv run ruff check .` — all checks passed.
  - `uv run ruff format --check .` — no files would be reformatted.
  - `uv run python -m snektest tests/` — all tests pass.
- Run the gate against the full changed surface, not just files you touched — formatting/typing issues often surface in neighbours. If any check fails, fix it before proceeding rather than committing and following up.
- Do not silence findings by relaxing the strict `pyright`/`ruff` config for production code. Fix the code. Config relaxations are only acceptable for genuine test-only false positives, scoped to `tests/` (ruff `per-file-ignores`, pyright `executionEnvironments`), and must be commented with the reason.

## Interacting with databases

- Use `snekql` for interacting with the database.
  - For `snekql` usage documentation, read its installed distribution metadata with `importlib.metadata.distribution("snekql").read_text("METADATA")`; the `METADATA` file embeds snekql's README.
