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
    compose_files: list[str] = []
    base_images: list[str] = []
    exposed_ports: list[str] = []


class TestFrameworkInfo(BaseModel):
    """A single detected test framework with its category and config files."""

    name: str
    category: Literal[
        "unit", "integration", "e2e", "performance", "security", "linting", "other"
    ] = "unit"
    config_files: list[str] = []


class TestInfo(BaseModel):
    frameworks: list[TestFrameworkInfo] = []
    test_directories: list[str] = []
    test_files: list[str] = []
    has_coverage_config: bool = False
    coverage_config_files: list[str] = []
    test_commands: list[str] = []      # e.g. ["pytest", "python manage.py test"]


class CICDInfo(BaseModel):
    platform: str | None = None
    config_files: list[str] = []


class PackageManagerInfo(BaseModel):
    name: str
    files: list[str] = []


class InstalledPackage(BaseModel):
    """A single dependency parsed from a dependency manifest."""

    name: str
    version: str = ""     # version constraint as written, e.g. ">=2.0,<3" or "^18.0.0"
    dev: bool = False      # True if dev/test dependency


class InstalledPackages(BaseModel):
    """All dependencies parsed from every detected manifest file."""

    python: list[InstalledPackage] = []
    npm: list[InstalledPackage] = []
    java: list[InstalledPackage] = []
    go: list[InstalledPackage] = []
    ruby: list[InstalledPackage] = []
    rust: list[InstalledPackage] = []
    dotnet: list[InstalledPackage] = []
    php: list[InstalledPackage] = []


class DependencyFiles(BaseModel):
    """Paths to dependency / config files found in the repository."""

    requirements: list[str] = []
    pyproject: list[str] = []
    pipfile: list[str] = []
    setup_py: list[str] = []
    setup_cfg: list[str] = []
    package_json: list[str] = []
    pom_xml: list[str] = []
    build_gradle: list[str] = []
    go_mod: list[str] = []
    cargo_toml: list[str] = []
    gemfile: list[str] = []
    composer_json: list[str] = []
    pubspec_yaml: list[str] = []
    manage_py: list[str] = []
    csproj: list[str] = []
    sln: list[str] = []


class DeploymentInfo(BaseModel):
    """Deployment-related metadata discovered from the repository."""

    # Containerisation
    is_containerized: bool = False
    container_orchestration: list[str] = []   # e.g. ["Kubernetes", "Docker Compose", "Docker Swarm"]
    kubernetes_manifests: list[str] = []       # paths to k8s YAML / Helm charts
    helm_charts: list[str] = []               # paths to Chart.yaml
    kustomize_files: list[str] = []           # paths to kustomization.yaml

    # Serverless / PaaS
    serverless_configs: list[str] = []        # serverless.yml, SAM template, etc.
    procfile: list[str] = []                  # Heroku Procfile
    app_yaml: list[str] = []                  # GCP App Engine app.yaml

    # IaC
    terraform_files: list[str] = []
    bicep_files: list[str] = []
    cloudformation_files: list[str] = []
    pulumi_files: list[str] = []
    ansible_files: list[str] = []

    # Build artefacts / solution files
    solution_files: list[str] = []            # .sln
    makefile: list[str] = []                  # Makefile
    build_scripts: list[str] = []             # build.sh, build.ps1, etc.

    # Environment config
    env_files: list[str] = []                 # .env.example, .env.sample
    nginx_configs: list[str] = []             # nginx.conf
    supervisor_configs: list[str] = []        # supervisord.conf
    systemd_units: list[str] = []             # *.service files

    # Deployment style inference
    deployment_style: Literal[
        "containerized", "serverless", "vm-based", "paas", "static-site", "unknown"
    ] = "unknown"


class GitHubFeatures(BaseModel):
    """GitHub repository features detected via the API."""

    code_scanning_enabled: bool = False
    dependabot_alerts_enabled: bool = False
    secret_scanning_enabled: bool = False
    secret_scanning_push_protection: bool = False
    is_private: bool = False
    default_branch: str = "main"
    actions_secrets: list[str] = []
    actions_variables: list[str] = []  # repo-level non-secret variables
    environment_vars: dict[str, list[str]] = {}  # {env_name: [var_names]}


class DetectedEnvVar(BaseModel):
    """An environment variable reference found in source code."""

    name: str
    files: list[str] = []


class DetectedEnvVars(BaseModel):
    """Environment variables detected from the repository source code."""

    required: list[DetectedEnvVar] = []
    optional: list[DetectedEnvVar] = []
    total_env_var_count: int = 0


class RepoAnalysis(BaseModel):
    """Output schema for Agent 1: Code Analyser."""

    repo_url: str
    branch: str = ""  # branch that was analysed (empty = default branch)
    languages: list[LanguageInfo] = []
    frameworks: list[str] = []
    architecture: Literal[
        "monorepo", "microservices", "single-service", "monolith", "unknown"
    ] = "unknown"
    docker: DockerInfo = DockerInfo()
    tests: TestInfo = TestInfo()
    installed_packages: InstalledPackages = InstalledPackages()
    package_managers: list[PackageManagerInfo] = []
    dependency_files: DependencyFiles = DependencyFiles()
    deployment: DeploymentInfo = DeploymentInfo()
    cloud_providers: list[str] = []
    cicd: list[CICDInfo] = []
    github_features: GitHubFeatures = GitHubFeatures()
    env_vars: DetectedEnvVars = DetectedEnvVars()
