"""Detect package managers and dependency file locations."""

from __future__ import annotations

from pathlib import Path

from devops_guardian.models.analysis import DependencyFiles, PackageManagerInfo

PACKAGE_MANAGER_FILES: dict[str, str] = {
    "package-lock.json": "npm",
    "yarn.lock": "Yarn",
    "pnpm-lock.yaml": "pnpm",
    "bun.lockb": "Bun",
    "requirements.txt": "pip",
    "Pipfile.lock": "Pipenv",
    "Pipfile": "Pipenv",
    "poetry.lock": "Poetry",
    "uv.lock": "uv",
    "pdm.lock": "PDM",
    "go.sum": "Go Modules",
    "Cargo.lock": "Cargo",
    "Gemfile.lock": "Bundler",
    "composer.lock": "Composer",
    "pubspec.lock": "pub (Dart)",
    "gradle.lockfile": "Gradle",
    "pom.xml": "Maven",
    "build.gradle": "Gradle",
    "build.gradle.kts": "Gradle",
    "Package.resolved": "Swift PM",
    "mix.lock": "Mix (Elixir)",
    "package.json": "npm",
    "pyproject.toml": "pip",
}

# Maps: filename → DependencyFiles field name
DEPENDENCY_FILE_MAP: dict[str, str] = {
    "requirements.txt": "requirements",
    "pyproject.toml": "pyproject",
    "Pipfile": "pipfile",
    "setup.py": "setup_py",
    "setup.cfg": "setup_cfg",
    "package.json": "package_json",
    "pom.xml": "pom_xml",
    "build.gradle": "build_gradle",
    "build.gradle.kts": "build_gradle",
    "go.mod": "go_mod",
    "Cargo.toml": "cargo_toml",
    "Gemfile": "gemfile",
    "composer.json": "composer_json",
    "pubspec.yaml": "pubspec_yaml",
    "manage.py": "manage_py",
}


def detect_package_managers(file_paths: list[str]) -> list[PackageManagerInfo]:
    """Return list of detected package managers with their file paths."""
    managers: dict[str, list[str]] = {}

    for fp in file_paths:
        name = Path(fp).name
        # Also match requirements-*.txt and requirements/*.txt
        if name.startswith("requirements") and name.endswith(".txt"):
            managers.setdefault("pip", []).append(fp)
        elif name.endswith(".csproj"):
            managers.setdefault("NuGet", []).append(fp)
        elif name in PACKAGE_MANAGER_FILES:
            mgr = PACKAGE_MANAGER_FILES[name]
            managers.setdefault(mgr, []).append(fp)

    return sorted(
        [PackageManagerInfo(name=mgr, files=sorted(files)) for mgr, files in managers.items()],
        key=lambda m: m.name,
    )


def detect_dependency_files(file_paths: list[str]) -> DependencyFiles:
    """Scan the file tree and locate all dependency / config files."""
    result: dict[str, list[str]] = {}

    for fp in file_paths:
        name = Path(fp).name
        # Handle requirements*.txt specially (could be requirements-dev.txt etc.)
        if name.startswith("requirements") and name.endswith(".txt"):
            result.setdefault("requirements", []).append(fp)
        elif name.endswith(".csproj"):
            result.setdefault("csproj", []).append(fp)
        elif name.endswith(".sln"):
            result.setdefault("sln", []).append(fp)
        elif name in DEPENDENCY_FILE_MAP:
            field = DEPENDENCY_FILE_MAP[name]
            result.setdefault(field, []).append(fp)

    return DependencyFiles(**{k: sorted(v) for k, v in result.items()})
