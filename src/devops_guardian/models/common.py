from pydantic import BaseModel


class RepoMetadata(BaseModel):
    """Shared metadata about a repository, used across all agents."""

    repo_url: str
    clone_path: str
    default_branch: str = "main"
