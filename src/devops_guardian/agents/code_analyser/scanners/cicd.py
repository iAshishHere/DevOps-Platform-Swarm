"""Detect existing CI/CD pipeline configurations."""

from __future__ import annotations

from pathlib import Path

from devops_guardian.models.analysis import CICDInfo

CICD_PATTERNS: dict[str, list[str]] = {
    "GitHub Actions": [".github/workflows/"],
    "Jenkins": ["Jenkinsfile"],
    "GitLab CI": [".gitlab-ci.yml"],
    "CircleCI": [".circleci/config.yml"],
    "Azure Pipelines": ["azure-pipelines.yml"],
    "Bitbucket Pipelines": ["bitbucket-pipelines.yml"],
    "Travis CI": [".travis.yml"],
    "Drone CI": [".drone.yml"],
    "Tekton": [".tekton/"],
    "ArgoCD": ["argocd/", "argo-cd/"],
}


def detect_cicd(file_paths: list[str]) -> list[CICDInfo]:
    """Return detected CI/CD platforms and their config files."""
    results: list[CICDInfo] = []

    for platform, patterns in CICD_PATTERNS.items():
        matched_files: list[str] = []
        for fp in file_paths:
            normalised = fp.replace("\\", "/")
            for pattern in patterns:
                if pattern.endswith("/"):
                    if normalised.startswith(pattern) or f"/{pattern}" in normalised:
                        matched_files.append(fp)
                else:
                    if Path(fp).name == pattern or normalised.endswith(pattern):
                        matched_files.append(fp)

        if matched_files:
            results.append(
                CICDInfo(platform=platform, config_files=sorted(set(matched_files)))
            )

    return results
