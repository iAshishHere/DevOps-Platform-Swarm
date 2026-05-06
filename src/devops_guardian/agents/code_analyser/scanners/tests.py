"""Detect test frameworks, test directories, and coverage config."""

from __future__ import annotations

import json
from pathlib import Path

from devops_guardian.models.analysis import TestInfo

TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "test_suite"}

COVERAGE_FILES = {
    ".coveragerc",
    "coverage.config",
    ".nycrc",
    ".nycrc.json",
    "jest.config.js",
    "jest.config.ts",
    "pytest.ini",
    "setup.cfg",
    ".c8rc.json",
    "codecov.yml",
    ".codecov.yml",
}


def detect_tests(repo_root: str, file_paths: list[str]) -> TestInfo:
    """Detect test frameworks and directories."""
    root = Path(repo_root)
    frameworks: set[str] = set()
    test_dirs: set[str] = set()

    # Find test directories
    for fp in file_paths:
        parts = Path(fp).parts
        for part in parts:
            if part.lower() in TEST_DIR_NAMES:
                test_dirs.add(part)

    # Detect coverage config
    has_coverage = any(Path(fp).name in COVERAGE_FILES for fp in file_paths)

    # Detect from package.json
    pkg = root / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(errors="ignore"))
            all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "jest" in all_deps:
                frameworks.add("Jest")
            if "mocha" in all_deps:
                frameworks.add("Mocha")
            if "vitest" in all_deps:
                frameworks.add("Vitest")
            if "cypress" in all_deps:
                frameworks.add("Cypress")
            if "playwright" in all_deps or "@playwright/test" in all_deps:
                frameworks.add("Playwright")
        except (json.JSONDecodeError, OSError):
            pass

    # Detect from Python files
    req_files = ["requirements.txt", "requirements-dev.txt", "pyproject.toml"]
    for rf in req_files:
        path = root / rf
        if path.exists():
            content = path.read_text(errors="ignore").lower()
            if "pytest" in content:
                frameworks.add("pytest")
            if "unittest" in content:
                frameworks.add("unittest")
            if "tox" in content:
                frameworks.add("tox")

    # Detect from Java
    for fname in ["pom.xml", "build.gradle", "build.gradle.kts"]:
        path = root / fname
        if path.exists():
            content = path.read_text(errors="ignore").lower()
            if "junit" in content:
                frameworks.add("JUnit")
            if "testng" in content:
                frameworks.add("TestNG")
            if "mockito" in content:
                frameworks.add("Mockito")

    return TestInfo(
        frameworks=sorted(frameworks),
        test_directories=sorted(test_dirs),
        has_coverage_config=has_coverage,
    )
