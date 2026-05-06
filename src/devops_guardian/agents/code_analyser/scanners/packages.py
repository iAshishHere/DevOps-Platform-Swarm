"""Detect package managers from lockfiles and config files."""

from __future__ import annotations

from pathlib import Path

PACKAGE_MANAGER_FILES: dict[str, str] = {
    "package-lock.json": "npm",
    "yarn.lock": "Yarn",
    "pnpm-lock.yaml": "pnpm",
    "bun.lockb": "Bun",
    "requirements.txt": "pip",
    "Pipfile.lock": "Pipenv",
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
}


def detect_package_managers(file_paths: list[str]) -> list[str]:
    """Return list of detected package managers."""
    managers: set[str] = set()
    file_names = {Path(fp).name for fp in file_paths}

    for marker_file, manager in PACKAGE_MANAGER_FILES.items():
        if marker_file in file_names:
            managers.add(manager)

    return sorted(managers)
