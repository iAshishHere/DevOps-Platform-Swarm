"""Resolve the target CI/CD platform and its file-path conventions."""

from __future__ import annotations

from devops_guardian.models.analysis import RepoAnalysis

# Maps platform name → default workflow directory / file path prefix
PLATFORM_PATHS: dict[str, str] = {
    "GitHub Actions": ".github/workflows",
    "GitLab CI": "",
    "Azure Pipelines": "",
    "Jenkins": "",
    "CircleCI": ".circleci",
    "Bitbucket Pipelines": "",
}

# Default file names per platform for a single-file pipeline
PLATFORM_FILENAMES: dict[str, str] = {
    "GitHub Actions": "{name}.yml",
    "GitLab CI": ".gitlab-ci.yml",
    "Azure Pipelines": "azure-pipelines.yml",
    "Jenkins": "Jenkinsfile",
    "CircleCI": "config.yml",
    "Bitbucket Pipelines": "bitbucket-pipelines.yml",
}


def resolve_platform(analysis: RepoAnalysis) -> str:
    """Pick the CI/CD platform to generate for.

    If the repo already has a CI/CD setup, use the first detected platform.
    Otherwise default to GitHub Actions.
    """
    if analysis.cicd:
        return analysis.cicd[0].platform or "GitHub Actions"
    return "GitHub Actions"


def pipeline_filepath(platform: str, name: str) -> str:
    """Return the conventional file path for a pipeline on the given platform."""
    prefix = PLATFORM_PATHS.get(platform, "")
    template = PLATFORM_FILENAMES.get(platform, "{name}.yml")
    filename = template.format(name=name)
    if prefix:
        return f"{prefix}/{filename}"
    return filename
