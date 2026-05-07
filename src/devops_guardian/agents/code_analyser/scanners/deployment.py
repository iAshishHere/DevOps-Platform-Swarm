"""Detect deployment style, IaC, orchestration, and build artefacts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from devops_guardian.models.analysis import DeploymentInfo


# ── Filename → field mapping ─────────────────────────────────────────────────

_FILE_MAP: dict[str, str] = {
    # Kubernetes / Helm / Kustomize
    "Chart.yaml": "helm_charts",
    "Chart.yml": "helm_charts",
    "kustomization.yaml": "kustomize_files",
    "kustomization.yml": "kustomize_files",
    # Serverless
    "serverless.yml": "serverless_configs",
    "serverless.yaml": "serverless_configs",
    "sam.yaml": "serverless_configs",
    "sam.yml": "serverless_configs",
    "template.yaml": "serverless_configs",
    "template.yml": "serverless_configs",
    # PaaS
    "Procfile": "procfile",
    "app.yaml": "app_yaml",
    "app.yml": "app_yaml",
    # IaC
    "Pulumi.yaml": "pulumi_files",
    "Pulumi.yml": "pulumi_files",
    # Build
    "Makefile": "makefile",
    "makefile": "makefile",
    "GNUmakefile": "makefile",
    # Env
    "nginx.conf": "nginx_configs",
    "supervisord.conf": "supervisor_configs",
}

_EXTENSION_MAP: dict[str, str] = {
    ".tf": "terraform_files",
    ".bicep": "bicep_files",
    ".sln": "solution_files",
}

_NAME_CONTAINS: dict[str, str] = {
    "build.sh": "build_scripts",
    "build.ps1": "build_scripts",
    "build.cmd": "build_scripts",
    "deploy.sh": "build_scripts",
    "deploy.ps1": "build_scripts",
    ".env.example": "env_files",
    ".env.sample": "env_files",
    ".env.template": "env_files",
}


def _is_k8s_manifest(repo_root: str, fp: str) -> bool:
    """Quick heuristic: YAML file containing 'apiVersion' and 'kind'."""
    path = Path(repo_root) / fp
    if not path.suffix.lower() in (".yaml", ".yml"):
        return False
    # Only check files in directories that look k8s-related
    lower_parts = [p.lower() for p in Path(fp).parts]
    k8s_dirs = {"k8s", "kubernetes", "manifests", "deploy", "deployment", "deployments", "infra", "infrastructure"}
    if not any(d in k8s_dirs for d in lower_parts):
        return False
    try:
        content = path.read_text(errors="ignore")[:500]
        return "apiVersion" in content and "kind" in content
    except OSError:
        return False


def _infer_style(info: DeploymentInfo) -> Literal[
    "containerized", "serverless", "vm-based", "paas", "static-site", "unknown"
]:
    """Infer the deployment style from collected signals."""
    if info.kubernetes_manifests or info.helm_charts or info.kustomize_files or info.container_orchestration:
        return "containerized"
    if info.serverless_configs:
        return "serverless"
    if info.procfile or info.app_yaml:
        return "paas"
    if info.ansible_files or info.systemd_units or info.supervisor_configs:
        return "vm-based"
    # If there are Dockerfiles but no orchestration, still containerized
    # (caller sets is_containerized separately)
    return "unknown"


def detect_deployment(repo_root: str, file_paths: list[str], has_dockerfile: bool) -> DeploymentInfo:
    """Scan the repo for deployment-related files and infer deployment style."""
    result: dict[str, list[str]] = {}

    for fp in file_paths:
        name = Path(fp).name
        ext = Path(fp).suffix.lower()

        # Exact filename match
        if name in _FILE_MAP:
            field = _FILE_MAP[name]
            result.setdefault(field, []).append(fp)

        # Extension match
        if ext in _EXTENSION_MAP:
            field = _EXTENSION_MAP[ext]
            result.setdefault(field, []).append(fp)

        # Name-contains match
        for pattern, field in _NAME_CONTAINS.items():
            if name == pattern:
                result.setdefault(field, []).append(fp)

        # CloudFormation templates (JSON with AWSTemplateFormatVersion)
        if name.lower() in ("cloudformation.json", "cloudformation.yaml", "cfn-template.json", "cfn-template.yaml"):
            result.setdefault("cloudformation_files", []).append(fp)

        # Ansible
        if "ansible" in fp.lower() and ext in (".yaml", ".yml"):
            result.setdefault("ansible_files", []).append(fp)

        # .csproj / .sln
        if ext == ".csproj":
            result.setdefault("solution_files", []).append(fp)

        # systemd unit files
        if ext == ".service":
            result.setdefault("systemd_units", []).append(fp)

        # Kubernetes manifests
        if _is_k8s_manifest(repo_root, fp):
            result.setdefault("kubernetes_manifests", []).append(fp)

    # Sort all lists
    result = {k: sorted(v) for k, v in result.items()}

    # Determine container orchestration
    orchestration: list[str] = []
    if result.get("helm_charts") or result.get("kubernetes_manifests") or result.get("kustomize_files"):
        orchestration.append("Kubernetes")
    if has_dockerfile:
        # Check for compose — these get set by docker scanner but we double-check here
        compose_names = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
        if any(Path(fp).name.lower() in compose_names for fp in file_paths):
            orchestration.append("Docker Compose")

    info = DeploymentInfo(
        is_containerized=has_dockerfile or bool(orchestration),
        container_orchestration=sorted(set(orchestration)),
        **result,
    )
    info.deployment_style = _infer_style(info)

    return info
