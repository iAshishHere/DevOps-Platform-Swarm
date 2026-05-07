"""Output models for Agent 2: Pipeline Generator."""

from __future__ import annotations

from pydantic import BaseModel


class PipelineFile(BaseModel):
    """A single generated pipeline / config file."""

    filename: str
    content: str
    description: str


class PipelineResult(BaseModel):
    """Output schema for Agent 2: Pipeline Generator."""

    repo_url: str
    platform: str  # e.g. "GitHub Actions", "GitLab CI"
    pipelines: list[PipelineFile] = []
    branch_name: str = ""
    pull_request_url: str = ""
    fix_attempts: int = 0          # how many fix cycles were executed
    max_fix_attempts: int = 3      # configurable cap
    all_passed: bool = False       # True when every workflow run succeeded
