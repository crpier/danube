import base64

from github import Github
from pydantic import HttpUrl


class GithubAdapter:
    def __init__(self, token) -> None:
        self._github = Github(token)

    def get_repo_file(self, repo_url: HttpUrl, file_path: str):
        assert repo_url.path
        repo_name = repo_url.path.lstrip("/")
        content_file = self._github.get_repo(repo_name).get_contents("pyproject.toml")
        assert content_file
        assert not isinstance(content_file, list)
        assert content_file.content
        return base64.b64decode(content_file.content).decode("utf-8")
