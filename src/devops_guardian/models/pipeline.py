"""Output models for Agent 2: Pipeline Generator."""

from __future__ import annotations

from pydantic import BaseModel

from devops_guardian.models.analysis import RepoAnalysis


class PipelineFile(BaseModel):
    """A single generated pipeline / config file."""

    filename: str
    content: str
    description: str


class PipelineOption(BaseModel):
    """A single pipeline capability the user can toggle on/off.

    Returned by ``available_options_from_analysis`` and shown in the UI.
    """

    key: str              # unique id: "unit_tests", "formatting", "container_scan", …
    label: str            # human-readable: "Unit Tests", "Code Formatting", …
    description: str      # one-liner for the UI tooltip
    category: str         # grouping: "testing", "quality", "security", "infrastructure"
    tools: list[str] = [] # detected tools: ["pytest", "jest"], informational
    enabled: bool = True  # default recommendation


class PipelineConfig(BaseModel):
    """User-selected pipeline generation preferences.

    ``selected_pipelines`` lists the pipeline keys the user chose.
    An empty list means "generate everything that was offered" (backward-compat).
    """

    selected_pipelines: list[str] = []   # e.g. ["unit_tests", "linting", "container_scan"]
    test_categories: list[str] = []      # e.g. ["unit", "e2e"] — empty = all discovered

    # ── Legacy boolean helpers (derived from selected_pipelines) ────────
    @property
    def enable_ci(self) -> bool:
        if not self.selected_pipelines:
            return True
        return "unit_tests" in self.selected_pipelines

    @property
    def enable_coverage(self) -> bool:
        if not self.selected_pipelines:
            return True
        return "coverage" in self.selected_pipelines

    @property
    def enable_sonarqube(self) -> bool:
        if not self.selected_pipelines:
            return True
        return "sonarqube" in self.selected_pipelines

    @property
    def enable_security(self) -> bool:
        """True if ANY security-category pipeline is selected."""
        if not self.selected_pipelines:
            return True
        sec_keys = {
            "secret_scanning", "vulnerability_scanning",
            "license_compliance", "sast", "container_scanning",
        }
        return bool(sec_keys & set(self.selected_pipelines))

    def has(self, key: str) -> bool:
        """Check whether a pipeline key was selected (empty = all)."""
        if not self.selected_pipelines:
            return True
        return key in self.selected_pipelines


# ── Pipeline option categories ───────────────────────────────────────────────

_CATEGORY_LABELS: dict[str, str] = {
    "testing": "Testing",
    "quality": "Code Quality",
    "security": "Security",
    "infrastructure": "Infrastructure",
}


class TestType(BaseModel):
    """A single test type discovered in the repo (kept for backward compat)."""

    category: str
    label: str
    frameworks: list[str] = []


class AvailableOptions(BaseModel):
    """Full set of pipeline options the UI presents to the user.

    Derived from Agent 1 output.  Each ``PipelineOption`` represents one
    pipeline the user can toggle.
    """

    pipeline_options: list[PipelineOption] = []

    # Kept for backward compatibility with existing frontend
    test_types: list[TestType] = []


# ── Builder ──────────────────────────────────────────────────────────────────

def available_options_from_analysis(analysis: RepoAnalysis) -> AvailableOptions:
    """Derive the full set of user-facing pipeline options from Agent 1 output."""
    options: list[PipelineOption] = []
    dep = analysis.deployment

    # ── Gather helper sets ────────────────────────────────────────────
    all_fw_names = {fw.name for fw in analysis.tests.frameworks}
    lang_names = {l.name.lower() for l in analysis.languages}
    has_tests = len(analysis.tests.frameworks) > 0
    has_docker = analysis.docker.has_dockerfile
    has_terraform = bool(dep.terraform_files)
    has_bicep = bool(dep.bicep_files)
    has_cloudformation = bool(dep.cloudformation_files)
    has_iac = has_terraform or has_bicep or has_cloudformation or bool(dep.pulumi_files)
    has_k8s = bool(dep.kubernetes_manifests) or bool(dep.helm_charts) or bool(dep.kustomize_files)

    # Detect linters/formatters from installed packages
    linters: list[str] = []
    formatters: list[str] = []
    _KNOWN_LINTERS = {
        "eslint", "pylint", "flake8", "ruff", "mypy", "checkstyle", "pmd",
        "golangci-lint", "swiftlint", "rubocop", "phpcs", "clippy",
        "tslint", "stylelint",
    }
    _KNOWN_FORMATTERS = {
        "prettier", "black", "isort", "gofmt", "rustfmt", "autopep8", "yapf",
        "google-java-format", "clang-format", "shfmt",
    }
    for fw in analysis.tests.frameworks:
        name_lower = fw.name.lower()
        if name_lower in _KNOWN_LINTERS or fw.category == "linting":
            linters.append(fw.name)
        if name_lower in _KNOWN_FORMATTERS:
            formatters.append(fw.name)

    # Check installed packages for linters/formatters not in test frameworks
    for pkg_list in [
        analysis.installed_packages.python,
        analysis.installed_packages.npm,
    ]:
        for pkg in pkg_list:
            pkg_lower = pkg.name.lower()
            if pkg_lower in _KNOWN_LINTERS and pkg.name not in linters:
                linters.append(pkg.name)
            if pkg_lower in _KNOWN_FORMATTERS and pkg.name not in formatters:
                formatters.append(pkg.name)

    # ── TESTING ──────────────────────────────────────────────────────

    # Unit tests
    unit_fws = [fw.name for fw in analysis.tests.frameworks if fw.category == "unit"]
    if unit_fws:
        options.append(PipelineOption(
            key="unit_tests", label="Unit Tests",
            description="Run automated unit tests. Outputs: pass/fail, JUnit reports.",
            category="testing", tools=unit_fws, enabled=True,
        ))

    # Integration tests
    integ_fws = [fw.name for fw in analysis.tests.frameworks if fw.category == "integration"]
    if integ_fws:
        options.append(PipelineOption(
            key="integration_tests", label="Integration Tests",
            description="Run integration tests against services/APIs.",
            category="testing", tools=integ_fws, enabled=True,
        ))

    # E2E tests
    e2e_fws = [fw.name for fw in analysis.tests.frameworks if fw.category == "e2e"]
    if e2e_fws:
        options.append(PipelineOption(
            key="e2e_tests", label="E2E Tests",
            description="Run end-to-end browser/API tests.",
            category="testing", tools=e2e_fws, enabled=True,
        ))

    # Performance tests
    perf_fws = [fw.name for fw in analysis.tests.frameworks if fw.category == "performance"]
    if perf_fws:
        options.append(PipelineOption(
            key="performance_tests", label="Performance Tests",
            description="Run load/performance tests.",
            category="testing", tools=perf_fws, enabled=False,
        ))

    # ── CODE QUALITY ─────────────────────────────────────────────────

    # Formatting
    if formatters:
        options.append(PipelineOption(
            key="formatting", label="Code Formatting",
            description="Check code formatting standards (fail on unformatted code).",
            category="quality", tools=formatters, enabled=True,
        ))
    else:
        # Suggest language-appropriate formatters even if not installed
        suggested = []
        if "python" in lang_names:
            suggested.append("black")
        if "javascript" in lang_names or "typescript" in lang_names:
            suggested.append("prettier")
        if "go" in lang_names:
            suggested.append("gofmt")
        if "rust" in lang_names:
            suggested.append("rustfmt")
        if suggested:
            options.append(PipelineOption(
                key="formatting", label="Code Formatting",
                description="Check code formatting standards (fail on unformatted code).",
                category="quality", tools=suggested, enabled=False,
            ))

    # Linting / static checks
    if linters:
        options.append(PipelineOption(
            key="linting", label="Linting / Static Checks",
            description="Run static analysis and lint checks on the codebase.",
            category="quality", tools=linters, enabled=True,
        ))
    else:
        suggested = []
        if "python" in lang_names:
            suggested.extend(["ruff", "pylint"])
        if "javascript" in lang_names or "typescript" in lang_names:
            suggested.append("eslint")
        if "go" in lang_names:
            suggested.append("golangci-lint")
        if "java" in lang_names:
            suggested.append("checkstyle")
        if suggested:
            options.append(PipelineOption(
                key="linting", label="Linting / Static Checks",
                description="Run static analysis and lint checks on the codebase.",
                category="quality", tools=suggested, enabled=False,
            ))

    # Coverage
    if has_tests:
        options.append(PipelineOption(
            key="coverage", label="Code Coverage",
            description="Collect and report test coverage metrics.",
            category="quality", tools=[], enabled=True,
        ))

    # SonarQube
    if has_tests:
        options.append(PipelineOption(
            key="sonarqube", label="SonarQube Analysis",
            description="Run SonarQube for code quality, tech debt, and quality gate.",
            category="quality", tools=["sonar-scanner"], enabled=True,
        ))

    # ── SECURITY ─────────────────────────────────────────────────────

    # SAST
    options.append(PipelineOption(
        key="sast", label="SAST",
        description="Static Application Security Testing (CodeQL, Semgrep, Bandit).",
        category="security",
        tools=_detect_sast_tools(lang_names),
        enabled=True,
    ))

    # Secret Scanning
    options.append(PipelineOption(
        key="secret_scanning", label="Secret Scanning",
        description="Detect hardcoded secrets, API keys, and credentials (Gitleaks, TruffleHog). Checks GHAS.",
        category="security", tools=["gitleaks", "trufflehog"], enabled=True,
    ))

    # Vulnerability / Dependency scanning
    options.append(PipelineOption(
        key="vulnerability_scanning", label="Vulnerability Scanning",
        description="Scan dependencies for known CVEs (Trivy, npm audit, pip-audit, Snyk).",
        category="security",
        tools=_detect_sca_tools(lang_names),
        enabled=True,
    ))

    # License Compliance
    options.append(PipelineOption(
        key="license_compliance", label="License Compliance",
        description="Check dependency licenses for policy violations.",
        category="security", tools=["license-checker", "pip-licenses", "fossa"],
        enabled=False,
    ))

    # Container Scanning (only if Dockerfile present)
    if has_docker:
        options.append(PipelineOption(
            key="container_scanning", label="Container Scanning",
            description="Scan container images for CVEs, root users, outdated OS packages.",
            category="security", tools=["trivy", "grype", "dockle"], enabled=True,
        ))

    # ── INFRASTRUCTURE ───────────────────────────────────────────────

    # IaC Validation (Terraform / Bicep / CloudFormation)
    if has_iac:
        iac_tools: list[str] = []
        if has_terraform:
            iac_tools.extend(["terraform fmt", "terraform validate", "tflint"])
        if has_bicep:
            iac_tools.extend(["bicep build", "bicep lint"])
        if has_cloudformation:
            iac_tools.extend(["cfn-lint", "aws cloudformation validate-template"])
        if dep.pulumi_files:
            iac_tools.append("pulumi preview")
        options.append(PipelineOption(
            key="iac_validation", label="IaC Validation",
            description="Validate Infrastructure as Code (formatting, linting, type checking).",
            category="infrastructure", tools=iac_tools, enabled=True,
        ))

    # IaC Security Scanning
    if has_iac:
        options.append(PipelineOption(
            key="iac_security", label="IaC Security Scanning",
            description="Scan IaC for security misconfigurations (tfsec, checkov, KICS).",
            category="infrastructure", tools=["tfsec", "checkov", "kics"], enabled=True,
        ))

    # Kubernetes Checks
    if has_k8s:
        k8s_tools = ["kube-score", "kube-linter"]
        if dep.helm_charts:
            k8s_tools.insert(0, "helm lint")
        options.append(PipelineOption(
            key="k8s_checks", label="Kubernetes Checks",
            description="Lint K8s manifests/Helm charts: privilege checks, missing limits, probes, insecure caps.",
            category="infrastructure", tools=k8s_tools, enabled=True,
        ))

    # ── Build backward-compatible test_types list ────────────────────
    cat_to_fws: dict[str, list[str]] = {}
    for fw in analysis.tests.frameworks:
        cat_to_fws.setdefault(fw.category, []).append(fw.name)

    _cat_labels = {
        "unit": "Unit Testing", "integration": "Integration Testing",
        "e2e": "E2E Testing", "performance": "Performance Testing",
        "security": "Security Testing", "linting": "Linting", "other": "Other",
    }

    test_types = [
        TestType(category=cat, label=_cat_labels.get(cat, cat.title()), frameworks=fws)
        for cat, fws in sorted(cat_to_fws.items())
    ]

    return AvailableOptions(pipeline_options=options, test_types=test_types)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _detect_sast_tools(lang_names: set[str]) -> list[str]:
    tools = []
    if "python" in lang_names:
        tools.append("bandit")
    if "javascript" in lang_names or "typescript" in lang_names:
        tools.append("semgrep")
    if "java" in lang_names or "kotlin" in lang_names:
        tools.append("spotbugs")
    if "go" in lang_names:
        tools.append("gosec")
    if "c#" in lang_names or "c" in lang_names or "c++" in lang_names:
        tools.append("codeql")
    if not tools:
        tools.append("semgrep")  # universal fallback
    return tools


def _detect_sca_tools(lang_names: set[str]) -> list[str]:
    tools = ["trivy"]
    if "python" in lang_names:
        tools.append("pip-audit")
    if "javascript" in lang_names or "typescript" in lang_names:
        tools.append("npm audit")
    if "ruby" in lang_names:
        tools.append("bundler-audit")
    if "go" in lang_names:
        tools.append("govulncheck")
    if "java" in lang_names:
        tools.append("dependency-check")
    return tools


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
