"""Output models for Agent 2: Pipeline Generator."""

from __future__ import annotations

from pydantic import BaseModel

from devops_guardian.models.analysis import RepoAnalysis


class PipelineFile(BaseModel):
    """A single generated pipeline / config file."""

    filename: str
    content: str
    description: str


class PipelineConfig(BaseModel):
    """User-selected pipeline generation preferences.

    All flags default to ``True`` so that callers who don't pass a config
    get the full pipeline set (backward-compatible).
    """

    enable_ci: bool = True
    enable_coverage: bool = True
    enable_sonarqube: bool = True
    enable_security: bool = True
    test_categories: list[str] = []  # e.g. ["unit", "e2e"] — empty = all discovered


_CATEGORY_LABELS: dict[str, str] = {
    "unit": "Unit Testing",
    "integration": "Integration Testing",
    "e2e": "E2E Testing",
    "performance": "Performance Testing",
    "security": "Security Testing",
    "linting": "Linting",
    "other": "Other",
}


class TestType(BaseModel):
    """A single test type discovered in the repo."""

    category: str       # e.g. "unit", "e2e"
    label: str          # e.g. "Unit Testing", "E2E Testing"
    frameworks: list[str] = []  # e.g. ["Django TestCase"] — informational


class AvailableOptions(BaseModel):
    """Options the UI can present to the user, derived from Agent 1 output.

    Every field here is *already* discovered — the UI should only show
    these, never invent options that Agent 1 didn't find.
    """

    test_types: list[TestType] = []
    enable_coverage: bool = False       # True when test frameworks exist
    enable_sonarqube: bool = False      # True when test frameworks exist
    enable_security: bool = True        # always available


def available_options_from_analysis(analysis: RepoAnalysis) -> AvailableOptions:
    """Derive the set of user-facing options from Agent 1's discovery."""
    cat_to_frameworks: dict[str, list[str]] = {}
    for fw in analysis.tests.frameworks:
        cat_to_frameworks.setdefault(fw.category, []).append(fw.name)

    has_tests = len(analysis.tests.frameworks) > 0

    test_types = [
        TestType(
            category=cat,
            label=_CATEGORY_LABELS.get(cat, cat.title()),
            frameworks=fws,
        )
        for cat, fws in sorted(cat_to_frameworks.items())
    ]

    return AvailableOptions(
        test_types=test_types,
        enable_coverage=has_tests,
        enable_sonarqube=has_tests,
        enable_security=True,
    )


class PipelineResult(BaseModel):
    """Output schema for Agent 2: Pipeline Generator."""

    repo_url: str
    platform: str  # e.g. "GitHub Actions", "GitLab CI"
    pipelines: list[PipelineFile] = []
    branch_name: str = ""
    pull_request_url: str = ""
    fix_attempts: int = 0          # how many fix cycles were executed
    max_fix_attempts: int = 3      # configurable cap
    all_passed: bool = False       # True when every workflow run succeeded
