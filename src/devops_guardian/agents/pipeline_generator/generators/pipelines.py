"""Generate CI/CD pipeline YAML via LLM for each pipeline type."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

from devops_guardian.agents.pipeline_generator.generators.platform import pipeline_filepath
from devops_guardian.agents.pipeline_generator.prompts import (
    CI_PIPELINE_PROMPT,
    CLASSIFY_ERROR_PROMPT,
    CONTAINER_SCANNING_PIPELINE_PROMPT,
    COVERAGE_JOB_PROMPT,
    COVERAGE_PIPELINE_PROMPT,
    E2E_PIPELINE_PROMPT,
    FIX_PIPELINE_PROMPT,
    FORMATTING_PIPELINE_PROMPT,
    IAC_SECURITY_PIPELINE_PROMPT,
    IAC_VALIDATION_PIPELINE_PROMPT,
    INTEGRATION_TEST_PIPELINE_PROMPT,
    K8S_CHECKS_PIPELINE_PROMPT,
    LICENSE_COMPLIANCE_PIPELINE_PROMPT,
    LINTING_PIPELINE_PROMPT,
    PERFORMANCE_TEST_PIPELINE_PROMPT,
    PIPELINE_SYSTEM_PROMPT,
    RESTRUCTURE_PROMPT,
    SAST_PIPELINE_PROMPT,
    SECRET_SCANNING_PIPELINE_PROMPT,
    SECURITY_PIPELINE_PROMPT,
    SONARQUBE_JOB_PROMPT,
    SONARQUBE_PIPELINE_PROMPT,
    VULNERABILITY_SCANNING_PIPELINE_PROMPT,
)
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.models.pipeline import PipelineConfig, PipelineFile
from devops_guardian.utils.llm import get_llm


def _format_env_vars(analysis: RepoAnalysis) -> str:
    """Build a human-readable list of required env vars and their availability."""
    ev = analysis.env_vars
    ghf = analysis.github_features
    secrets = set(ghf.actions_secrets)
    variables = set(ghf.actions_variables)
    # Flatten all env-level vars into one set
    env_level_vars: set[str] = set()
    for var_list in ghf.environment_vars.values():
        env_level_vars.update(var_list)

    # Detect env-bundle secrets (a single secret that holds multiple KEY=VALUE pairs)
    _ENV_BUNDLE_NAMES = {
        "ENV", "env", ".env", "DOTENV", "dotenv", "ENVIRONMENT",
        "environment", "ENV_FILE", "env_file", "ENV_VARS", "env_vars",
    }
    env_bundle_secret: str | None = None
    for s in ghf.actions_secrets:
        if s in _ENV_BUNDLE_NAMES:
            env_bundle_secret = s
            break

    if not ev.required and not ev.optional:
        return "  (none detected)"

    def _availability(name: str) -> tuple[str, str]:
        """Return (status_str, pipeline_action) for a var."""
        avail: list[str] = []
        if name in secrets:
            avail.append("GitHub secret")
        if name in variables:
            avail.append("GitHub variable")
        if name in env_level_vars:
            avail.append("GitHub environment")

        if avail:
            status = f"✓ available: {', '.join(avail)}"
            if name in secrets:
                action = f"→ use ${{{{ secrets.{name} }}}}"
            elif name in variables:
                action = f"→ use ${{{{ vars.{name} }}}}"
            else:
                action = "→ available at runtime"
        elif env_bundle_secret:
            status = f"? may be inside bundle secret '{env_bundle_secret}'"
            action = f"→ load from ${{{{ secrets.{env_bundle_secret} }}}} (see ENV BUNDLE instructions)"
        else:
            status = "✗ NOT available anywhere"
            action = "→ ADD env: with placeholder comment for user"
        return status, action

    lines: list[str] = []

    if env_bundle_secret:
        lines.append(
            f"  ENV BUNDLE SECRET DETECTED: '{env_bundle_secret}' — this secret likely "
            f"contains multiple KEY=VALUE pairs (like a .env file). For any required var "
            f"not available as its own secret, add a step to write this secret to a .env "
            f"file and source it."
        )
        lines.append("")

    if ev.required:
        lines.append("  REQUIRED (app will crash without these):")
        for v in ev.required:
            status, action = _availability(v.name)
            lines.append(
                f"    - {v.name}  [{status}]  {action}  "
                f"(used in: {', '.join(v.files[:3])})"
            )

    if ev.optional:
        lines.append("  OPTIONAL (have defaults or use os.environ.get with fallback):")
        for v in ev.optional[:15]:
            status, action = _availability(v.name)
            line = f"    - {v.name}  [{status}]"
            if "available" in status:
                line += f"  {action}"
            lines.append(line)
        if len(ev.optional) > 15:
            lines.append(f"    … and {len(ev.optional) - 15} more")

    # Summarise total
    lines.append(f"\n  Total env vars detected: {ev.total_env_var_count}")

    return "\n".join(lines)


def _format_dependency_details(analysis: RepoAnalysis) -> str:
    """Build a human-readable list of all dependency file paths."""
    dep = analysis.dependency_files
    lines: list[str] = []

    def _add(label: str, paths: list[str]) -> None:
        if paths:
            for p in paths:
                lines.append(f"  - {label}: {p}")

    _add("requirements.txt", dep.requirements)
    _add("pyproject.toml", dep.pyproject)
    _add("Pipfile", dep.pipfile)
    _add("setup.py", dep.setup_py)
    _add("setup.cfg", dep.setup_cfg)
    _add("package.json", dep.package_json)
    _add("pom.xml", dep.pom_xml)
    _add("build.gradle", dep.build_gradle)
    _add("go.mod", dep.go_mod)
    _add("Cargo.toml", dep.cargo_toml)
    _add("Gemfile", dep.gemfile)
    _add("composer.json", dep.composer_json)
    _add("pubspec.yaml", dep.pubspec_yaml)
    _add("manage.py (Django)", dep.manage_py)
    _add(".csproj", dep.csproj)
    _add(".sln", dep.sln)

    return "\n".join(lines) if lines else "  (none detected)"


def _format_context(analysis: RepoAnalysis) -> dict[str, str]:
    """Build the common template variables from a RepoAnalysis."""
    dep = analysis.deployment
    ghf = analysis.github_features
    return {
        "default_branch": os.environ.get("DEFAULT_BRANCH", "main"),
        "languages": ", ".join(l.name for l in analysis.languages) or "none",
        "frameworks": ", ".join(analysis.frameworks) or "none",
        "package_managers": ", ".join(pm.name for pm in analysis.package_managers) or "none",
        "architecture": analysis.architecture,
        "has_dockerfile": str(analysis.docker.has_dockerfile),
        "has_compose": str(analysis.docker.has_compose),
        "base_images": ", ".join(analysis.docker.base_images) or "none",
        "dockerfiles": ", ".join(analysis.docker.dockerfiles) or "none",
        "test_frameworks": ", ".join(f"{t.name} ({t.category})" for t in analysis.tests.frameworks) or "none",
        "test_directories": ", ".join(analysis.tests.test_directories) or "none",
        "has_coverage_config": str(analysis.tests.has_coverage_config),
        "cloud_providers": ", ".join(analysis.cloud_providers) or "none",
        "dependency_file_details": _format_dependency_details(analysis),
        # Deployment / IaC context
        "terraform_files": ", ".join(dep.terraform_files) or "none",
        "bicep_files": ", ".join(dep.bicep_files) or "none",
        "cloudformation_files": ", ".join(dep.cloudformation_files) or "none",
        "pulumi_files": ", ".join(dep.pulumi_files) or "none",
        "ansible_files": ", ".join(dep.ansible_files) or "none",
        "kubernetes_manifests": ", ".join(dep.kubernetes_manifests) or "none",
        "helm_charts": ", ".join(dep.helm_charts) or "none",
        "kustomize_files": ", ".join(dep.kustomize_files) or "none",
        # GitHub repo features
        "code_scanning_enabled": str(ghf.code_scanning_enabled),
        "dependabot_enabled": str(ghf.dependabot_alerts_enabled),
        "secret_scanning_enabled": str(ghf.secret_scanning_enabled),
        "is_private_repo": str(ghf.is_private),
        # GitHub Actions secrets (names only)
        "actions_secrets": ", ".join(ghf.actions_secrets) if ghf.actions_secrets else "(none configured)",
        # GitHub Actions variables (non-secret, repo-level)
        "actions_variables": ", ".join(ghf.actions_variables) if ghf.actions_variables else "(none configured)",
        # Required environment variables detected in source code
        "required_env_vars": _format_env_vars(analysis),
    }


def _call_llm(prompt_template: str, context: dict[str, str]) -> str:
    """Send a prompt to the LLM and return the raw YAML string."""
    llm = get_llm()
    user_prompt = prompt_template.format(**context)
    response = llm.invoke([
        {"role": "system", "content": PIPELINE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ])
    content = response.content.strip()
    # Strip markdown fences if the LLM wraps them anyway
    if content.startswith("```"):
        lines = content.splitlines()
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines)
    return content


def _get_ci_dependency_config(platform: str) -> str:
    """Return the platform-specific config snippet for depending on the CI workflow."""
    if platform == "GitHub Actions":
        return "For GitHub Actions, use 'workflow_run' trigger that runs after the 'CI' workflow completes successfully, OR use 'needs: ci' if in the same workflow file."
    if platform == "GitLab CI":
        return "For GitLab CI, use 'needs:' to depend on the CI job."
    return "Configure this pipeline to run only after the CI pipeline passes."


def _get_coverage_dependency_config(platform: str) -> str:
    """Return the platform-specific config snippet for depending on the coverage workflow."""
    if platform == "GitHub Actions":
        return "For GitHub Actions, use 'workflow_run' trigger that runs after the 'Coverage' workflow completes successfully, OR use 'needs: coverage' if in the same workflow file."
    if platform == "GitLab CI":
        return "For GitLab CI, use 'needs:' to depend on the coverage job."
    return "Configure this pipeline to run only after the coverage pipeline passes."


def generate_ci(analysis: RepoAnalysis, platform: str, config: "PipelineConfig | None" = None) -> PipelineFile:
    """Generate the main CI pipeline."""
    ctx = _format_context(analysis)
    ctx["platform"] = platform

    # Build the jobs instruction list based on what the user selected.
    # When config is None or selected_pipelines is empty, include everything.
    job_lines: list[str] = []
    include_all = config is None or not config.selected_pipelines

    if include_all or config.has("linting"):
        job_lines.append("- Run linting / static checks appropriate for the detected languages and frameworks.")
    if include_all or config.has("unit_tests"):
        job_lines.append("- Run the test suite using the detected test frameworks.")
    if include_all:
        job_lines.append("- Build the application (compile, bundle, or docker build as appropriate).")

    ctx["ci_jobs_instructions"] = "\n".join(job_lines)

    yaml_content = _call_llm(CI_PIPELINE_PROMPT, ctx)
    return PipelineFile(
        filename=pipeline_filepath(platform, "ci"),
        content=yaml_content,
        description="CI pipeline — lint, test, build",
    )


def generate_coverage(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    """Generate a code-coverage pipeline."""
    ctx = _format_context(analysis)
    ctx["platform"] = platform
    ctx["ci_dependency_config"] = _get_ci_dependency_config(platform)
    yaml_content = _call_llm(COVERAGE_PIPELINE_PROMPT, ctx)
    return PipelineFile(
        filename=pipeline_filepath(platform, "coverage"),
        content=yaml_content,
        description="Code coverage collection and reporting (depends on CI)",
    )


def generate_sonarqube(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    """Generate a SonarQube integration pipeline."""
    ctx = _format_context(analysis)
    ctx["platform"] = platform
    ctx["coverage_dependency_config"] = _get_coverage_dependency_config(platform)
    yaml_content = _call_llm(SONARQUBE_PIPELINE_PROMPT, ctx)
    return PipelineFile(
        filename=pipeline_filepath(platform, "sonarqube"),
        content=yaml_content,
        description="SonarQube analysis and quality gate (depends on Coverage)",
    )


def generate_security(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    """Generate a security scanning pipeline."""
    ctx = _format_context(analysis)
    ctx["platform"] = platform
    yaml_content = _call_llm(SECURITY_PIPELINE_PROMPT, ctx)
    return PipelineFile(
        filename=pipeline_filepath(platform, "security"),
        content=yaml_content,
        description="Security scanning — SAST, SCA, container scan, secret detection",
    )


# ── Generic standalone pipeline generator ─────────────────────────────────────

def _generate_standalone(
    analysis: RepoAnalysis,
    platform: str,
    prompt_template: str,
    filename_key: str,
    description: str,
    extra_ctx: dict[str, str] | None = None,
) -> PipelineFile:
    """Generate a standalone pipeline from a prompt template."""
    ctx = _format_context(analysis)
    ctx["platform"] = platform
    if extra_ctx:
        ctx.update(extra_ctx)
    yaml_content = _call_llm(prompt_template, ctx)
    return PipelineFile(
        filename=pipeline_filepath(platform, filename_key),
        content=yaml_content,
        description=description,
    )


def generate_formatting(analysis: RepoAnalysis, platform: str, tools: list[str]) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, FORMATTING_PIPELINE_PROMPT, "formatting",
        "Code formatting checks",
        extra_ctx={"detected_tools": ", ".join(tools) or "auto-detect"},
    )


def generate_linting(analysis: RepoAnalysis, platform: str, tools: list[str]) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, LINTING_PIPELINE_PROMPT, "linting",
        "Linting / static analysis",
        extra_ctx={"detected_tools": ", ".join(tools) or "auto-detect"},
    )


def generate_secret_scanning(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, SECRET_SCANNING_PIPELINE_PROMPT, "secret-scanning",
        "Secret scanning — Gitleaks",
    )


def generate_vulnerability_scanning(analysis: RepoAnalysis, platform: str, tools: list[str]) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, VULNERABILITY_SCANNING_PIPELINE_PROMPT, "vulnerability-scanning",
        "Dependency vulnerability scanning — Trivy, SCA",
        extra_ctx={"detected_tools": ", ".join(tools) or "trivy"},
    )


def generate_license_compliance(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, LICENSE_COMPLIANCE_PIPELINE_PROMPT, "license-compliance",
        "License compliance scanning",
    )


def generate_container_scanning(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, CONTAINER_SCANNING_PIPELINE_PROMPT, "container-scanning",
        "Container image scanning — Trivy, Dockle",
    )


def generate_iac_validation(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, IAC_VALIDATION_PIPELINE_PROMPT, "iac-validation",
        "IaC validation — fmt, validate, tflint",
    )


def generate_iac_security(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, IAC_SECURITY_PIPELINE_PROMPT, "iac-security",
        "IaC security scanning — Checkov, tfsec",
    )


def generate_k8s_checks(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, K8S_CHECKS_PIPELINE_PROMPT, "k8s-checks",
        "Kubernetes manifest validation — helm lint, kube-score",
    )


def generate_sast(analysis: RepoAnalysis, platform: str, tools: list[str]) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, SAST_PIPELINE_PROMPT, "sast",
        "SAST — static application security testing",
        extra_ctx={"detected_tools": ", ".join(tools) or "semgrep"},
    )


def generate_e2e_tests(analysis: RepoAnalysis, platform: str, tools: list[str]) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, E2E_PIPELINE_PROMPT, "e2e-tests",
        "E2E tests",
        extra_ctx={"detected_tools": ", ".join(tools) or "auto-detect"},
    )


def generate_integration_tests(analysis: RepoAnalysis, platform: str, tools: list[str]) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, INTEGRATION_TEST_PIPELINE_PROMPT, "integration-tests",
        "Integration tests",
        extra_ctx={"detected_tools": ", ".join(tools) or "auto-detect"},
    )


def generate_performance_tests(analysis: RepoAnalysis, platform: str, tools: list[str]) -> PipelineFile:
    return _generate_standalone(
        analysis, platform, PERFORMANCE_TEST_PIPELINE_PROMPT, "performance-tests",
        "Performance / load tests",
        extra_ctx={"detected_tools": ", ".join(tools) or "auto-detect"},
    )


# ── Job-append generators (testing mode) ─────────────────────────────────────


def generate_coverage_job(
    analysis: RepoAnalysis, platform: str, current_ci_yaml: str,
) -> PipelineFile:
    """Add a coverage job to the existing CI pipeline file.

    Returns a PipelineFile with the same filename as the CI pipeline but with
    the coverage job appended.
    """
    ctx = _format_context(analysis)
    ctx["platform"] = platform
    ctx["current_ci_yaml"] = current_ci_yaml
    yaml_content = _call_llm(COVERAGE_JOB_PROMPT, ctx)
    return PipelineFile(
        filename=pipeline_filepath(platform, "ci"),
        content=yaml_content,
        description="CI pipeline — lint, test, build + coverage",
    )


def generate_sonarqube_job(
    analysis: RepoAnalysis, platform: str, current_ci_yaml: str,
) -> PipelineFile:
    """Add a SonarQube job to the existing CI pipeline file.

    Returns a PipelineFile with the same filename as the CI pipeline but with
    the sonarqube job appended (depends on coverage).
    """
    ctx = _format_context(analysis)
    ctx["platform"] = platform
    ctx["current_ci_yaml"] = current_ci_yaml
    yaml_content = _call_llm(SONARQUBE_JOB_PROMPT, ctx)
    return PipelineFile(
        filename=pipeline_filepath(platform, "ci"),
        content=yaml_content,
        description="CI pipeline — lint, test, build + coverage + sonarqube",
    )


# ── Restructure (split combined file into production files) ───────────────────


def restructure_pipelines(
    analysis: RepoAnalysis, platform: str, combined_yaml: str,
) -> list[PipelineFile]:
    """Split a combined CI pipeline into separate production-ready files.

    Uses the LLM to produce separate files with proper workflow_run triggers.
    Returns a list of PipelineFile (ci, coverage, sonarqube).
    """
    import json

    import yaml as _yaml

    default_branch = os.environ.get("DEFAULT_BRANCH", "main")

    # ── Parse the combined YAML to discover actual job names ────────────
    try:
        parsed = _yaml.safe_load(combined_yaml)
        job_names = list((parsed or {}).get("jobs", {}).keys())
    except Exception:
        job_names = []

    job_count = len(job_names)

    # Determine which output files are needed based on actual jobs
    has_coverage = any("coverage" in j.lower() for j in job_names)
    has_sonarqube = any("sonar" in j.lower() for j in job_names)

    file_instructions_parts = [
        "1. CI pipeline (.github/workflows/ci.yml) — build, test, and linting jobs.",
    ]
    file_count = 1
    if has_coverage:
        file_count += 1
        file_instructions_parts.append(
            f"{file_count}. Coverage pipeline (.github/workflows/coverage.yml) — "
            "triggered by CI success."
        )
    if has_sonarqube:
        file_count += 1
        file_instructions_parts.append(
            f"{file_count}. SonarQube pipeline (.github/workflows/sonarqube.yml) — "
            "triggered by Coverage success."
        )
    file_instructions = "\n".join(file_instructions_parts)

    ctx = {
        "platform": platform,
        "combined_yaml": combined_yaml,
        "count": str(file_count),
        "file_instructions": file_instructions,
        "default_branch": default_branch,
        "job_names": ", ".join(job_names) if job_names else "(could not parse)",
        "job_count": str(job_count),
    }
    raw = _call_llm(RESTRUCTURE_PROMPT, ctx)

    # Parse the JSON array from LLM
    try:
        files_data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from surrounding text
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            files_data = json.loads(raw[start:end])
        else:
            raise ValueError(f"LLM did not return valid JSON for restructure: {raw[:500]}")

    descriptions = {
        "ci": "CI pipeline — lint, test, build",
        "coverage": "Code coverage collection and reporting (depends on CI)",
        "sonarqube": "SonarQube analysis and quality gate (depends on Coverage)",
    }

    result: list[PipelineFile] = []
    for item in files_data:
        filename = item["filename"]
        content = item["content"]
        # Determine description from filename
        desc = "Generated pipeline"
        for key, d in descriptions.items():
            if key in filename.lower():
                desc = d
                break
        result.append(PipelineFile(filename=filename, content=content, description=desc))

    # ── Validate: every input job must appear in some output file ────────
    if job_names:
        output_jobs: set[str] = set()
        for pf in result:
            try:
                parsed_out = _yaml.safe_load(pf.content)
                output_jobs.update((parsed_out or {}).get("jobs", {}).keys())
            except Exception:
                pass
        missing = set(job_names) - output_jobs
        if missing:
            logger.warning(
                "Restructure DROPPED jobs: %s — falling back to combined pipeline.",
                missing,
            )
            raise ValueError(
                f"Restructure dropped {len(missing)} job(s): {missing}. "
                "This is not a faithful split."
            )

    return result


def classify_pipeline_error(
    pipeline: PipelineFile,
    workflow_name: str,
    error_log: str,
) -> str:
    """Ask the LLM to classify an error as pipeline_config, missing_secret, or test_failure.

    Returns one of: 'pipeline_config', 'missing_secret', 'test_failure'.
    Falls back to 'pipeline_config' if the LLM response is unexpected.
    """
    llm = get_llm()
    prompt = CLASSIFY_ERROR_PROMPT.format(
        workflow_name=workflow_name,
        filename=pipeline.filename,
        current_yaml=pipeline.content,
        error_log=error_log[-15000:],  # last 15K chars for classification
    )
    response = llm.invoke([
        {"role": "system", "content": "You are a CI/CD error analysis expert. Respond with exactly one word."},
        {"role": "user", "content": prompt},
    ])
    category = response.content.strip().lower().replace(".", "")
    valid = {"pipeline_config", "missing_secret", "test_failure"}
    if category not in valid:
        # If the LLM returned something unexpected, default to pipeline_config
        return "pipeline_config"
    return category


def fix_pipeline(
    analysis: RepoAnalysis,
    platform: str,
    pipeline: PipelineFile,
    workflow_name: str,
    error_log: str,
    fix_attempt: int = 1,
    previous_attempts: list[dict] | None = None,
    applied_fixes: list[dict] | None = None,
    use_continue_on_error: bool = False,
    run_history: list[dict] | None = None,
) -> PipelineFile:
    """Send the failing pipeline YAML + error logs to the LLM for a fix.

    *previous_attempts* — fix cycles where the LLM produced identical YAML
    (no real change).  Shown as "don't repeat" examples.

    *applied_fixes* — fix cycles where YAML actually changed, with diffs.
    Shown as "preserve these changes" instructions so the LLM doesn't
    regress earlier fixes.

    *use_continue_on_error* — if True, the failing step has resisted 3+
    consecutive fix attempts, likely because the app code itself is broken.
    Instructs the LLM to add ``continue-on-error: true`` to that step.
    """
    ctx = _format_context(analysis)
    ctx["platform"] = platform
    ctx["workflow_name"] = workflow_name
    ctx["filename"] = pipeline.filename
    ctx["current_yaml"] = pipeline.content
    ctx["error_log"] = error_log
    ctx["fix_attempt"] = str(fix_attempt)

    # Build previous-attempts section (identical / no-change attempts)
    if previous_attempts:
        parts = ["\n### Previous fix attempts (these already FAILED — do NOT repeat):"]
        for i, pa in enumerate(previous_attempts, 1):
            parts.append(f"\n#### Attempt {i}:")
            parts.append(f"Error:\n```\n{pa['error_log'][:3000]}\n```")
            yaml_text = pa.get('yaml_before', pa.get('yaml', ''))[:3000]
            parts.append(f"YAML that was tried:\n```yaml\n{yaml_text}\n```")
        ctx["previous_attempts_section"] = "\n".join(parts) + "\n"
    else:
        ctx["previous_attempts_section"] = ""

    # Build applied-fixes section (diffs to preserve)
    if applied_fixes:
        parts = [
            "\n### Previously applied fixes (PRESERVE these changes — do NOT revert):",
            "Each change below was made to fix a specific error. Keep these changes",
            "in your output unless a change directly causes the CURRENT error.\n",
        ]
        for i, af in enumerate(applied_fixes, 1):
            error_text = af.get('error_summary', af.get('error_log', ''))[:1000]
            diff_text = af.get('diff_summary', '(no diff available)')
            parts.append(f"#### Applied Fix {i}:")
            parts.append(f"Error that was fixed:\n```\n{error_text}\n```")
            parts.append(f"Changes made:\n```diff\n{diff_text}\n```\n")
        ctx["applied_fixes_section"] = "\n".join(parts) + "\n"
    else:
        ctx["applied_fixes_section"] = ""

    # If classified as test_failure or exhausted missing_secret attempts, instruct to skip
    if use_continue_on_error:
        ctx["continue_on_error_section"] = (
            "\n### IMPORTANT — continue-on-error authorised for this pipeline:\n"
            "The tool in this pipeline EXECUTED SUCCESSFULLY and PRODUCED OUTPUT, \n"
            "but the results show test assertion failures in the APPLICATION CODE \n"
            "that can ONLY be fixed by changing .py/.js/.ts/etc source files — \n"
            "NOT by changing the pipeline YAML.\n\n"
            "BEFORE adding continue-on-error, VERIFY the error log shows:\n"
            "  - The tool actually ran (not 'command not found' or 'module not found')\n"
            "  - The tool found and executed tests (not 'no tests collected')\n"
            "  - Some tests PASSED and some FAILED (e.g. '30 passed, 6 failed')\n"
            "  - The failures are assertion errors in test code, NOT missing packages\n\n"
            "If the above conditions are met:\n"
            "1. Add `continue-on-error: true` to the specific step that is failing.\n"
            "2. Add a comment: # NON-BLOCKING: X/Y tests pass — failing tests need app code fix\n"
            "3. Do NOT remove the step — keep it visible but non-blocking.\n"
            "4. Do NOT change any other part of the pipeline.\n\n"
            "If the conditions are NOT met (tool didn't run, package missing, config error):\n"
            "  IGNORE this section and FIX THE ROOT CAUSE instead — install the package,\n"
            "  fix the path, add the configuration. Do NOT use continue-on-error.\n"
        )
    else:
        ctx["continue_on_error_section"] = ""

    # Build run-history section — shows the agent ALL runs it has made
    if run_history:
        parts = [
            "\n### Complete run history for this pipeline:",
            "Below is every run attempt and its outcome so far. Use this to understand",
            "the FULL trajectory of fixes and avoid repeating failed strategies.\n",
        ]
        for i, rh in enumerate(run_history, 1):
            status = rh.get("status", "unknown")
            classification = rh.get("classification", "")
            error_summary = rh.get("error_summary", "")[:1000]
            fix_applied = rh.get("fix_applied", "")
            parts.append(f"#### Run {i}: {status}")
            if classification:
                parts.append(f"Classification: {classification}")
            if error_summary:
                parts.append(f"Error:\n```\n{error_summary}\n```")
            if fix_applied:
                parts.append(f"Fix applied: {fix_applied}")
            parts.append("")
        ctx["run_history_section"] = "\n".join(parts) + "\n"
    else:
        ctx["run_history_section"] = ""

    fixed_yaml = _call_llm(FIX_PIPELINE_PROMPT, ctx)
    return PipelineFile(
        filename=pipeline.filename,
        content=fixed_yaml,
        description=pipeline.description,
    )
