"""Detect test frameworks, test types, directories, config, and commands."""

from __future__ import annotations

import json
from pathlib import Path

from devops_guardian.models.analysis import TestFrameworkInfo, TestInfo

TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "test_suite", "e2e", "integration", "functional"}

TEST_FILE_PATTERNS = {
    "test_", "_test.", ".test.", ".spec.", "_spec.", "tests.", "Test.",
}

COVERAGE_FILES = {
    ".coveragerc": ".coveragerc",
    "coverage.config": "coverage.config",
    ".nycrc": ".nycrc",
    ".nycrc.json": ".nycrc.json",
    "jest.config.js": "jest.config.js",
    "jest.config.ts": "jest.config.ts",
    "pytest.ini": "pytest.ini",
    "setup.cfg": "setup.cfg",
    ".c8rc.json": ".c8rc.json",
    "codecov.yml": "codecov.yml",
    ".codecov.yml": ".codecov.yml",
    "tox.ini": "tox.ini",
    ".coveralls.yml": ".coveralls.yml",
}

# ── Framework → category & config file detection ────────────────────────────

# Python
PY_TEST_DEPS: dict[str, tuple[str, str]] = {
    "pytest": ("pytest", "unit"),
    "unittest": ("unittest", "unit"),
    "nose2": ("nose2", "unit"),
    "hypothesis": ("Hypothesis", "unit"),
    "tox": ("tox", "other"),
    "behave": ("Behave", "e2e"),
    "robot": ("Robot Framework", "e2e"),
    "robotframework": ("Robot Framework", "e2e"),
    "locust": ("Locust", "performance"),
    "selenium": ("Selenium", "e2e"),
    "playwright": ("Playwright", "e2e"),
    "pytest-playwright": ("Playwright", "e2e"),
    "pytest-bdd": ("pytest-bdd", "e2e"),
    "factory-boy": ("Factory Boy", "unit"),
    "faker": ("Faker", "unit"),
    "coverage": ("coverage", "other"),
    "pytest-cov": ("pytest-cov", "other"),
    "bandit": ("Bandit", "security"),
    "safety": ("Safety", "security"),
    "flake8": ("Flake8", "linting"),
    "pylint": ("Pylint", "linting"),
    "mypy": ("mypy", "linting"),
    "ruff": ("Ruff", "linting"),
    "black": ("Black", "linting"),
    "isort": ("isort", "linting"),
}

# JavaScript / TypeScript
NPM_TEST_DEPS: dict[str, tuple[str, str]] = {
    "jest": ("Jest", "unit"),
    "mocha": ("Mocha", "unit"),
    "vitest": ("Vitest", "unit"),
    "jasmine": ("Jasmine", "unit"),
    "ava": ("Ava", "unit"),
    "cypress": ("Cypress", "e2e"),
    "@playwright/test": ("Playwright", "e2e"),
    "playwright": ("Playwright", "e2e"),
    "puppeteer": ("Puppeteer", "e2e"),
    "selenium-webdriver": ("Selenium", "e2e"),
    "nightwatch": ("Nightwatch", "e2e"),
    "testcafe": ("TestCafe", "e2e"),
    "supertest": ("Supertest", "integration"),
    "@testing-library/react": ("React Testing Library", "unit"),
    "@testing-library/vue": ("Vue Testing Library", "unit"),
    "@testing-library/angular": ("Angular Testing Library", "unit"),
    "sinon": ("Sinon", "unit"),
    "chai": ("Chai", "unit"),
    "k6": ("k6", "performance"),
    "artillery": ("Artillery", "performance"),
    "eslint": ("ESLint", "linting"),
    "prettier": ("Prettier", "linting"),
    "storybook": ("Storybook", "other"),
    "@storybook/react": ("Storybook", "other"),
}

# Java / JVM
JAVA_TEST_DEPS: dict[str, tuple[str, str]] = {
    "junit": ("JUnit", "unit"),
    "testng": ("TestNG", "unit"),
    "mockito": ("Mockito", "unit"),
    "assertj": ("AssertJ", "unit"),
    "selenium": ("Selenium", "e2e"),
    "cucumber": ("Cucumber", "e2e"),
    "rest-assured": ("REST Assured", "integration"),
    "gatling": ("Gatling", "performance"),
    "jmeter": ("JMeter", "performance"),
    "archunit": ("ArchUnit", "other"),
    "jacoco": ("JaCoCo", "other"),
    "spotbugs": ("SpotBugs", "security"),
    "checkstyle": ("Checkstyle", "linting"),
    "pmd": ("PMD", "linting"),
}

# .NET
DOTNET_TEST_DEPS: dict[str, tuple[str, str]] = {
    "xunit": ("xUnit", "unit"),
    "nunit": ("NUnit", "unit"),
    "mstest": ("MSTest", "unit"),
    "moq": ("Moq", "unit"),
    "fluentassertions": ("FluentAssertions", "unit"),
    "specflow": ("SpecFlow", "e2e"),
    "playwright": ("Playwright", "e2e"),
    "selenium": ("Selenium", "e2e"),
    "nbomber": ("NBomber", "performance"),
    "coverlet": ("Coverlet", "other"),
}

# Config files per framework
FRAMEWORK_CONFIGS: dict[str, list[str]] = {
    "Jest": ["jest.config.js", "jest.config.ts", "jest.config.mjs"],
    "Vitest": ["vitest.config.ts", "vitest.config.js"],
    "Cypress": ["cypress.config.js", "cypress.config.ts", "cypress.json"],
    "Playwright": ["playwright.config.ts", "playwright.config.js"],
    "pytest": ["pytest.ini", "pyproject.toml", "setup.cfg", "conftest.py"],
    "tox": ["tox.ini"],
    "Storybook": [".storybook/main.js", ".storybook/main.ts"],
    "ESLint": [".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml", "eslint.config.js", "eslint.config.mjs"],
    "Prettier": [".prettierrc", ".prettierrc.js", ".prettierrc.json", "prettier.config.js"],
    "Ruff": ["ruff.toml", "pyproject.toml"],
    "Robot Framework": ["robot.yaml"],
}


def _find_files(file_paths: list[str], filename: str) -> list[str]:
    return [fp for fp in file_paths if Path(fp).name == filename]


def _detect_test_files(file_paths: list[str]) -> list[str]:
    """Find files that look like test files."""
    test_files: list[str] = []
    for fp in file_paths:
        name = Path(fp).name
        if any(pat in name for pat in TEST_FILE_PATTERNS):
            test_files.append(fp)
    return sorted(test_files)


def _detect_framework_configs(file_paths: list[str], framework: str) -> list[str]:
    """Find config files for a given framework."""
    patterns = FRAMEWORK_CONFIGS.get(framework, [])
    configs: list[str] = []
    for fp in file_paths:
        for pat in patterns:
            if fp.endswith(pat) or Path(fp).name == pat:
                configs.append(fp)
    return sorted(set(configs))


def _infer_test_commands(frameworks: list[TestFrameworkInfo], dep_files: list[str]) -> list[str]:
    """Infer the most likely test commands from detected test frameworks."""
    commands: list[str] = []
    fw_names = {f.name for f in frameworks}

    # Django
    manage_pys = [fp for fp in dep_files if Path(fp).name == "manage.py"]
    if manage_pys:
        mp = manage_pys[0]
        mp_dir = str(Path(mp).parent)
        prefix = f"cd {mp_dir} && " if mp_dir != "." else ""
        if "pytest" in fw_names:
            commands.append(f"{prefix}pytest")
        else:
            commands.append(f"{prefix}python manage.py test")

    elif "pytest" in fw_names:
        commands.append("pytest")
    elif "unittest" in fw_names:
        commands.append("python -m unittest discover")

    if "Jest" in fw_names:
        commands.append("npx jest")
    if "Vitest" in fw_names:
        commands.append("npx vitest run")
    if "Mocha" in fw_names:
        commands.append("npx mocha")
    if "Cypress" in fw_names:
        commands.append("npx cypress run")
    if "Playwright" in fw_names:
        commands.append("npx playwright test")
    if "JUnit" in fw_names:
        commands.append("mvn test")
    if "xUnit" in fw_names or "NUnit" in fw_names or "MSTest" in fw_names:
        commands.append("dotnet test")
    if "Robot Framework" in fw_names:
        commands.append("robot .")
    if "Behave" in fw_names:
        commands.append("behave")
    if "tox" in fw_names:
        commands.append("tox")

    return commands


def detect_tests(repo_root: str, file_paths: list[str]) -> TestInfo:
    """Detect test frameworks, categories, directories, files, config, and commands."""
    root = Path(repo_root)
    frameworks: dict[str, TestFrameworkInfo] = {}  # name → info (dedup)
    test_dirs: set[str] = set()

    # ── Find test directories ────────────────────────────────────────────
    for fp in file_paths:
        parts = Path(fp).parts
        for part in parts:
            if part.lower() in TEST_DIR_NAMES:
                test_dirs.add(part)

    # ── Coverage config ──────────────────────────────────────────────────
    coverage_configs: list[str] = []
    for fp in file_paths:
        if Path(fp).name in COVERAGE_FILES:
            coverage_configs.append(fp)

    # ── Detect from package.json (all of them) ───────────────────────────
    for fp in _find_files(file_paths, "package.json"):
        pkg = root / fp
        try:
            data = json.loads(pkg.read_text(errors="ignore"))
        except (json.JSONDecodeError, OSError):
            continue

        all_deps = data.get("dependencies", {})
        dev_deps = data.get("devDependencies", {})
        merged = {**all_deps, **dev_deps}

        for dep, (name, cat) in NPM_TEST_DEPS.items():
            if dep in merged:
                if name not in frameworks:
                    configs = _detect_framework_configs(file_paths, name)
                    frameworks[name] = TestFrameworkInfo(name=name, category=cat, config_files=configs)

    # ── Detect from Python dependency files ──────────────────────────────
    py_dep_files = []
    for fname in ["requirements.txt", "requirements-dev.txt", "requirements-test.txt",
                   "pyproject.toml", "setup.cfg", "Pipfile", "setup.py"]:
        py_dep_files.extend(_find_files(file_paths, fname))
    # Also find requirements*.txt anywhere
    for fp in file_paths:
        if Path(fp).name.startswith("requirements") and fp.endswith(".txt") and fp not in py_dep_files:
            py_dep_files.append(fp)

    for rf in py_dep_files:
        path = root / rf
        if not path.exists():
            continue
        content = path.read_text(errors="ignore").lower()
        for dep, (name, cat) in PY_TEST_DEPS.items():
            if dep in content and name not in frameworks:
                configs = _detect_framework_configs(file_paths, name)
                frameworks[name] = TestFrameworkInfo(name=name, category=cat, config_files=configs)

    # ── Detect from Java build files ─────────────────────────────────────
    for fname in ["pom.xml", "build.gradle", "build.gradle.kts"]:
        for fp in _find_files(file_paths, fname):
            path = root / fp
            if not path.exists():
                continue
            content = path.read_text(errors="ignore").lower()
            for dep, (name, cat) in JAVA_TEST_DEPS.items():
                if dep in content and name not in frameworks:
                    configs = _detect_framework_configs(file_paths, name)
                    frameworks[name] = TestFrameworkInfo(name=name, category=cat, config_files=configs)

    # ── Detect from .NET project files ───────────────────────────────────
    for fp in file_paths:
        if fp.endswith(".csproj"):
            path = root / fp
            if not path.exists():
                continue
            content = path.read_text(errors="ignore").lower()
            for dep, (name, cat) in DOTNET_TEST_DEPS.items():
                if dep in content and name not in frameworks:
                    configs = _detect_framework_configs(file_paths, name)
                    frameworks[name] = TestFrameworkInfo(name=name, category=cat, config_files=configs)

    # ── Test files & commands ────────────────────────────────────────────
    test_files = _detect_test_files(file_paths)
    fw_list = sorted(frameworks.values(), key=lambda f: f.name)
    test_commands = _infer_test_commands(fw_list, file_paths)

    return TestInfo(
        frameworks=fw_list,
        test_directories=sorted(test_dirs),
        test_files=test_files,
        has_coverage_config=len(coverage_configs) > 0,
        coverage_config_files=sorted(coverage_configs),
        test_commands=test_commands,
    )
