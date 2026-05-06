"""Detect cloud provider usage from dependencies, IaC, and config files."""

from __future__ import annotations

import json
from pathlib import Path

AWS_MARKERS = {"boto3", "aws-cdk", "@aws-sdk", "serverless", "sam.yaml", "template.yaml", "terraform/aws"}
AZURE_MARKERS = {"@azure/", "azure-", "Microsoft.Azure", "azure-pipelines", "bicep", "terraform/azurerm"}
GCP_MARKERS = {"@google-cloud/", "google-cloud-", "firebase", "app.yaml", "terraform/google"}


def _check_file_content(repo_root: str, filename: str, markers: set[str]) -> bool:
    path = Path(repo_root) / filename
    if not path.exists():
        return False
    content = path.read_text(errors="ignore").lower()
    return any(m.lower() in content for m in markers)


def detect_cloud_providers(repo_root: str, file_paths: list[str]) -> list[str]:
    """Return list of detected cloud providers."""
    providers: set[str] = set()

    # Check file paths for IaC / config markers
    all_paths = " ".join(file_paths).lower()
    if any(m.lower() in all_paths for m in ["aws", "cdk.json", "sam", "cloudformation"]):
        providers.add("AWS")
    if any(m.lower() in all_paths for m in ["azure", "bicep", "azuredeploy"]):
        providers.add("Azure")
    if any(m.lower() in all_paths for m in ["gcp", "firebase", "app.yaml"]):
        providers.add("GCP")

    # Check dependency files
    dep_files = ["package.json", "requirements.txt", "pyproject.toml", "pom.xml", "build.gradle"]
    for df in dep_files:
        path = Path(repo_root) / df
        if not path.exists():
            continue
        content = path.read_text(errors="ignore").lower()
        if any(m.lower() in content for m in AWS_MARKERS):
            providers.add("AWS")
        if any(m.lower() in content for m in AZURE_MARKERS):
            providers.add("Azure")
        if any(m.lower() in content for m in GCP_MARKERS):
            providers.add("GCP")

    return sorted(providers)
