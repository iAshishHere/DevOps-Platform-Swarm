"""Generate CI/CD pipeline YAML via LLM for each pipeline type."""

from __future__ import annotations

import os

from devops_guardian.agents.pipeline_generator.generators.platform import pipeline_filepath
from devops_guardian.agents.pipeline_generator.prompts import (
    CI_PIPELINE_PROMPT,
    CLASSIFY_ERROR_PROMPT,
    COVERAGE_JOB_PROMPT,
    COVERAGE_PIPELINE_PROMPT,
    FIX_PIPELINE_PROMPT,
    PIPELINE_SYSTEM_PROMPT,
    RESTRUCTURE_PROMPT,
    SECURITY_PIPELINE_PROMPT,
    SONARQUBE_JOB_PROMPT,
    SONARQUBE_PIPELINE_PROMPT,
)
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.models.pipeline import PipelineFile
from devops_guardian.utils.llm import get_llm


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
    return {
        "default_branch": os.environ.get("DEFAULT_BRANCH", "main"),
        "languages": ", ".join(l.name for l in analysis.languages) or "none",
        "frameworks": ", ".join(analysis.frameworks) or "none",
        "package_managers": ", ".join(pm.name for pm in analysis.package_managers) or "none",
        "architecture": analysis.architecture,
        "has_dockerfile": str(analysis.docker.has_dockerfile),
        "has_compose": str(analysis.docker.has_compose),
        "base_images": ", ".join(analysis.docker.base_images) or "none",
        "test_frameworks": ", ".join(f"{t.name} ({t.category})" for t in analysis.tests.frameworks) or "none",
        "test_directories": ", ".join(analysis.tests.test_directories) or "none",
        "has_coverage_config": str(analysis.tests.has_coverage_config),
        "cloud_providers": ", ".join(analysis.cloud_providers) or "none",
        "dependency_file_details": _format_dependency_details(analysis),
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


def generate_ci(analysis: RepoAnalysis, platform: str) -> PipelineFile:
    """Generate the main CI pipeline."""
    ctx = _format_context(analysis)
    ctx["platform"] = platform
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

    default_branch = os.environ.get("DEFAULT_BRANCH", "main")
    file_instructions = (
        "1. CI pipeline (.github/workflows/ci.yml) — only the build/test job.\n"
        "2. Coverage pipeline (.github/workflows/coverage.yml) — triggered by CI success.\n"
        "3. SonarQube pipeline (.github/workflows/sonarqube.yml) — triggered by Coverage success."
    )
    ctx = {
        "platform": platform,
        "combined_yaml": combined_yaml,
        "count": "3",
        "file_instructions": file_instructions,
        "default_branch": default_branch,
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

    return result


def classify_pipeline_error(
    pipeline: PipelineFile,
    workflow_name: str,
    error_log: str,
) -> str:
    """Ask the LLM to classify an error as pipeline_config, missing_secret, or app_code.

    Returns one of: 'pipeline_config', 'missing_secret', 'app_code'.
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
    valid = {"pipeline_config", "missing_secret", "app_code"}
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

    # If classified as app_code or exhausted missing_secret attempts, instruct to skip
    if use_continue_on_error:
        ctx["continue_on_error_section"] = (
            "\n### IMPORTANT — This failure CANNOT be fixed by changing pipeline YAML:\n"
            "The error is caused by either:\n"
            "  (a) An APPLICATION CODE bug (failing test, import error, etc.), OR\n"
            "  (b) A missing secret/env variable that is not configured in GitHub Actions.\n\n"
            "ACTION REQUIRED:\n"
            "1. Add `continue-on-error: true` to the specific step that is failing.\n"
            "2. Add a YAML comment above that step explaining WHY it is skipped. Examples:\n"
            "   # SKIPPED: App code issue — test assertion fails due to application bug\n"
            "   # SKIPPED: Missing secret — required token/credential not configured\n"
            "   # SKIPPED: Missing env var — required environment variable not available in CI\n"
            "3. Do NOT remove the step — keep it visible but non-blocking.\n"
            "4. Do NOT change any other part of the pipeline — only add continue-on-error\n"
            "   and the comment to the failing step(s).\n"
        )
    else:
        ctx["continue_on_error_section"] = ""

    fixed_yaml = _call_llm(FIX_PIPELINE_PROMPT, ctx)
    return PipelineFile(
        filename=pipeline.filename,
        content=fixed_yaml,
        description=pipeline.description,
    )
