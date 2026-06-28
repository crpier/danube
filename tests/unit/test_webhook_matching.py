"""Tests for webhook repo-URL normalization.

The clone forms (https, scp-style ssh, ssh://, trailing slash, ``.git`` suffix,
mixed case) must collapse to one canonical ``host/path`` so a pipeline configured
with any form matches an event carrying another. `pipeline_matches` itself (which
needs a database-fetched `Pipeline[Fetched]`) is covered end-to-end in the webhook
API integration tests.
"""

from snektest import Param, assert_eq, test

from danube.webhooks import normalize_repo_url


@test(
    [
        Param(value="https://github.com/acme/app.git", name="https_dot_git"),
        Param(value="https://github.com/acme/app", name="https_plain"),
        Param(value="git@github.com:acme/app.git", name="ssh_scp"),
        Param(value="ssh://git@github.com/acme/app.git", name="ssh_url"),
        Param(value="HTTPS://GitHub.com/ACME/app/", name="trailing_slash_case"),
    ],
    mark="fast",
)
def test_clone_forms_normalize_to_same_repo(value: str) -> None:
    assert_eq(normalize_repo_url(value), "github.com/acme/app")


@test(mark="fast")
def test_distinct_repos_do_not_collide() -> None:
    assert normalize_repo_url("https://github.com/acme/app.git") != normalize_repo_url(
        "https://github.com/acme/other.git"
    )
