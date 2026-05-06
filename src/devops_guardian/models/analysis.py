from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LanguageInfo(BaseModel):
    name: str
    percentage: float
    file_count: int


class DockerInfo(BaseModel):
    has_dockerfile: bool = False
    has_compose: bool = False
    dockerfiles: list[str] = []
    base_images: list[str] = []


class TestInfo(BaseModel):
    frameworks: list[str] = []
    test_directories: list[str] = []
    has_coverage_config: bool = False


class CICDInfo(BaseModel):
    platform: str | None = None
    config_files: list[str] = []


class RepoAnalysis(BaseModel):
    """Output schema for Agent 1: Code Analyser."""

    repo_url: str
    languages: list[LanguageInfo] = []
    frameworks: list[str] = []
    architecture: Literal[
        "monorepo", "microservices", "single-service", "monolith", "unknown"
    ] = "unknown"
    docker: DockerInfo = DockerInfo()
    tests: TestInfo = TestInfo()
    package_managers: list[str] = []
    cloud_providers: list[str] = []
    cicd: list[CICDInfo] = []
