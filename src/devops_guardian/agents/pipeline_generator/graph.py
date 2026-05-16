"""LangGraph state graph for the Pipeline Generator agent.

All pipelines are generated in a SINGLE phase:

  CI pipeline (with test, coverage, and sonarqube as jobs) + Security pipeline.

This uses ``needs:`` dependencies within one file so all jobs are visible
on the branch.  After the fix loop validates everything, a restructure
step splits the combined CI file into separate production files with
proper ``workflow_run`` triggers.

Generate → write → push → wait → fix loop → restructure → PR.
"""

from __future__ import annotations

import datetime
import difflib
import json as _json
import logging
import os
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from devops_guardian.agents.pipeline_generator.generators.pipelines import (
    classify_pipeline_error,
    fix_pipeline,
    generate_ci,
    generate_container_scanning,
    generate_coverage_job,
    generate_e2e_tests,
    generate_formatting,
    generate_iac_security,
    generate_iac_validation,
    generate_integration_tests,
    generate_k8s_checks,
    generate_license_compliance,
    generate_linting,
    generate_performance_tests,
    generate_sast,
    generate_secret_scanning,
    generate_security,
    generate_sonarqube_job,
    generate_vulnerability_scanning,
    restructure_pipelines,
)
from devops_guardian.agents.pipeline_generator.generators.platform import resolve_platform
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.models.pipeline import PipelineConfig, PipelineFile, PipelineResult
from devops_guardian.utils.github_ops import (
    create_pull_request,
    get_failed_run_logs,
    wait_for_workflow_runs,
)
from devops_guardian.utils.repo_ops import (
    cleanup_repo,
    clone_repo,
    commit_and_push,
    create_branch,
)
from devops_guardian.utils.fix_knowledge import load_fix_knowledge, save_fix_knowledge
from devops_guardian.utils.run_logger import RunLogger

logger = logging.getLogger(__name__)

MAX_FIX_ATTEMPTS = int(os.environ.get("MAX_FIX_ATTEMPTS", "3"))
_MAX_PHASE = 1  # Single phase — everything generated at once

# Phase → keywords used to identify workflow runs that belong to that phase
_PHASE_KEYWORDS: dict[int, set[str]] = {
    1: {
        "ci", "security", "build", "test", "pipeline",
        "formatting", "format", "lint", "linting",
        "secret", "vulnerability", "license", "compliance",
        "container", "scanning", "iac", "k8s", "kubernetes",
        "sast", "e2e", "integration", "performance",
        "coverage", "sonarqube", "sonar",
    },
}


class PipelineState(TypedDict):
    analysis: dict[str, Any]
    config: dict[str, Any]       # PipelineConfig — user-selected generation prefs
    platform: str
    pipelines: list[dict[str, Any]]
    clone_path: str
    branch_name: str
    pull_request_url: str
    fix_attempts: int            # per-phase counter — reset on phase advance
    all_passed: bool             # True when current phase's runs all passed
    failed_runs: list[dict[str, Any]]
    passed_files: list[str]      # filenames of pipelines that already passed
    fix_history: dict[str, list[dict[str, str]]]
    last_push_had_changes: bool
    current_phase: int           # 1, 2, 3
    phase_failed: bool           # True when a phase exhausted MAX_FIX_ATTEMPTS
    run_dir: str                 # path to the run output directory (empty = no logging)
    progress_callback: Callable[..., None] | None  # (step, message, **detail)
    result: dict[str, Any]


def _emit(state: PipelineState, step: str, message: str, **detail: Any) -> None:
    """Send a progress event if a callback is registered."""
    cb = state.get("progress_callback")
    if cb:
        cb(step, message, **detail)


def _get_logger(state: PipelineState) -> RunLogger | None:
    """Return a RunLogger for Agent 2 if run_dir is set.

    Caches the instance so attach()/detach() operate on the same file handler.
    """
    run_dir = state.get("run_dir", "")
    if not run_dir:
        return None
    if run_dir not in _logger_cache:
        _logger_cache[run_dir] = RunLogger(run_dir, "agent2")
    return _logger_cache[run_dir]


# Module-level cache so every graph node reuses the same RunLogger instance.
_logger_cache: dict[str, RunLogger] = {}


def _compute_diff_summary(before: str, after: str, max_lines: int = 50) -> str:
    """Produce a concise unified diff between two YAML strings."""
    diff = list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="before",
        tofile="after",
        n=1,
    ))
    if not diff:
        return "(no changes)"
    result = [line.rstrip("\n") for line in diff[2:]]  # skip --- / +++ headers
    if len(result) > max_lines:
        result = result[:max_lines] + [f"... ({len(result) - max_lines} more lines)"]
    return "\n".join(result)


_SECRET_ATTEMPT_THRESHOLD = 2  # skip after this many attempts for missing_secret
_TEST_FAILURE_COE_THRESHOLD = 2  # require N consecutive test_failure classifications before COE

# Pipelines where test_failure → continue-on-error is valid.
# Security/infra pipelines failing ≠ test_failure; they need real fixes.
_TEST_FAILURE_COE_FILES = {"ci", "coverage", "sonarqube", "linting", "formatting"}


def _allows_continue_on_error(filename: str) -> bool:
    """Return True if this pipeline file should use continue-on-error for test_failure."""
    stem = Path(filename).stem.lower()
    return any(kw in stem for kw in _TEST_FAILURE_COE_FILES)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _pipelines_for_phase(
    pipelines: list[dict[str, Any]], phase: int,
) -> list[dict[str, Any]]:
    """Return pipelines whose description matches the given phase."""
    keywords = _PHASE_KEYWORDS.get(phase, set())
    return [
        p for p in pipelines
        if any(kw in p.get("description", "").lower() for kw in keywords)
    ]


def _match_pipeline_to_run(
    workflow_name: str, pipelines_by_file: dict[str, dict],
) -> dict | None:
    """Best-effort match a GitHub Actions workflow name to a pipeline file."""
    wf_lower = workflow_name.lower()

    # Order matters — more specific groups MUST come before generic ones.
    # Keywords are matched as substrings, so avoid short tokens that appear
    # inside common words (e.g. "sca" matches "scanning").
    keyword_groups = [
        ["sonarqube", "sonar"],
        ["coverage"],
        ["formatting", "format check"],
        ["linting", "lint", "static analysis"],
        ["secret scanning", "secret-scanning", "gitleaks", "trufflehog"],
        ["vulnerability", "dependency scan", "pip-audit", "npm audit", "trivy fs"],
        ["license", "compliance"],
        ["container scan", "container-scan", "image scan", "dockle"],
        ["iac validation", "iac-validation", "terraform validate", "tflint", "bicep build"],
        ["iac security", "iac-security", "checkov", "tfsec", "kics"],
        ["k8s", "kubernetes", "helm lint", "kube-score", "kube-linter"],
        ["sast", "bandit", "semgrep", "codeql", "gosec", "spotbugs"],
        ["e2e", "end-to-end", "cypress", "playwright"],
        ["integration test", "integration-test"],
        ["performance", "load test"],
        ["security"],
        ["ci"],
    ]

    for kws in keyword_groups:
        if any(kw in wf_lower for kw in kws):
            for filename, pdata in pipelines_by_file.items():
                fname_lower = filename.lower()
                desc_lower = pdata.get("description", "").lower()
                if any(kw in fname_lower or kw in desc_lower for kw in kws):
                    logger.debug(
                        "Matched workflow '%s' → %s (kw group %s)",
                        workflow_name, filename, kws,
                    )
                    return pdata

    # Fallback: stem matching
    for filename, pdata in pipelines_by_file.items():
        stem = Path(filename).stem.lower()
        if stem in wf_lower or wf_lower in stem:
            logger.debug(
                "Matched workflow '%s' → %s (stem fallback)",
                workflow_name, filename,
            )
            return pdata

    logger.debug(
        "No match for workflow '%s' in files: %s",
        workflow_name, list(pipelines_by_file.keys()),
    )
    return None


def _build_pr_body(platform: str, pipelines: list[dict]) -> str:
    """Build a markdown PR description listing all generated pipelines."""
    lines = [
        "## DevOps Guardian — Generated CI/CD Pipelines",
        "",
        f"**Target platform:** {platform}",
        "",
        "### Pipelines included",
        "",
    ]
    for p in pipelines:
        lines.append(f"- **`{p['filename']}`** — {p['description']}")
    lines += [
        "",
        "---",
        "*Generated automatically by [DevOps Guardian](https://github.com/your-org/DevOps-Platform-Swarm). "
        "Please review before merging.*",
    ]
    return "\n".join(lines)


# ── Graph nodes ──────────────────────────────────────────────────────────────


def clone_and_branch_node(state: PipelineState) -> dict:
    """Clone the repo and create a feature branch."""
    analysis = RepoAnalysis(**state["analysis"])
    _emit(state, "clone", f"Cloning {analysis.repo_url}…")
    clone_path = clone_repo(analysis.repo_url)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch_name = f"devops-guardian/pipelines-{ts}"
    create_branch(clone_path, branch_name)
    _emit(state, "clone", f"Repository cloned, branch {branch_name} created.")

    rl = _get_logger(state)
    if rl:
        rl.attach()
        logger.info("Agent 2 — cloned %s, branch %s", analysis.repo_url, branch_name)

    return {"clone_path": clone_path, "branch_name": branch_name}


def resolve_platform_node(state: PipelineState) -> dict:
    """Determine which CI/CD platform to target."""
    analysis = RepoAnalysis(**state["analysis"])
    platform = resolve_platform(analysis)
    _emit(state, "platform", f"Resolved CI/CD platform: {platform}")

    rl = _get_logger(state)
    if rl:
        rl.save_platform(platform)
        logger.info("Resolved CI/CD platform: %s", platform)

    return {"platform": platform}


# ── Phase-aware generation ──────────────────────────────────────────────────


def _filter_analysis_by_config(
    analysis: RepoAnalysis, config: PipelineConfig,
) -> RepoAnalysis:
    """Return a copy of the analysis with test frameworks filtered by
    the user-selected pipelines and test categories.

    When ``selected_pipelines`` is non-empty, only test framework categories
    whose corresponding pipeline key is selected are kept.  For example, if
    the user selected only ``["unit_tests", "secret_scanning"]``, e2e /
    integration / performance frameworks are stripped so the LLM won't
    generate jobs for them inside the CI pipeline.

    ``test_categories`` offers an additional, finer-grained filter.
    """
    frameworks = list(analysis.tests.frameworks)

    # ── Filter by selected_pipelines ────────────────────────────────
    # Map pipeline keys → test framework categories
    _PIPELINE_TO_CATEGORY = {
        "unit_tests": "unit",
        "e2e_tests": "e2e",
        "integration_tests": "integration",
        "performance_tests": "performance",
    }

    if config.selected_pipelines:
        allowed_categories: set[str] = set()
        for key, cat in _PIPELINE_TO_CATEGORY.items():
            if key in config.selected_pipelines:
                allowed_categories.add(cat)
        # Also keep categories that have no matching pipeline key (e.g. "linting")
        # so lint frameworks aren't accidentally dropped.
        known_cats = set(_PIPELINE_TO_CATEGORY.values())
        frameworks = [
            fw for fw in frameworks
            if fw.category in allowed_categories or fw.category not in known_cats
        ]

    # ── Filter by test_categories (finer-grained) ──────────────────
    if config.test_categories:
        selected = set(config.test_categories)
        frameworks = [fw for fw in frameworks if fw.category in selected]

    if frameworks == list(analysis.tests.frameworks):
        return analysis  # nothing changed

    return analysis.model_copy(
        update={"tests": analysis.tests.model_copy(
            update={"frameworks": frameworks},
        )},
    )


def generate_phase_node(state: PipelineState) -> dict:
    """Generate pipelines according to the user-selected config.

    Respects ``PipelineConfig.selected_pipelines`` — only generates pipeline
    types the user opted into.  Test frameworks are filtered to match
    the selected categories before being sent to the LLM.
    """
    config = PipelineConfig(**state.get("config", {}))
    analysis_raw = RepoAnalysis(**state["analysis"])
    analysis = _filter_analysis_by_config(analysis_raw, config)
    platform = state["platform"]

    all_pipelines: list[dict[str, Any]] = []
    enabled: list[str] = []

    _emit(state, "generate", "Generating pipelines…")

    # Helper to find tools for a pipeline key from the available options
    # (tools were set in available_options_from_analysis).  We reconstruct
    # them here from the analysis so generate_phase_node is self-contained.
    def _tools_for(key: str) -> list[str]:
        """Return the tool names associated with a pipeline option key."""
        from devops_guardian.models.pipeline import available_options_from_analysis as _derive
        opts = _derive(analysis_raw)
        for opt in opts.pipeline_options:
            if opt.key == key:
                return opt.tools
        return []

    # ── CI pipeline (test/build job) ────────────────────────────────────
    if config.has("unit_tests"):
        ci_pipeline = generate_ci(analysis, platform, config)
        logger.info("  → Base CI pipeline generated.")
        _emit(state, "generate", "CI pipeline generated (test/build job).")
        ci_content = ci_pipeline.content

        # ── Coverage job (added into CI file) ───────────────────────────
        if config.has("coverage"):
            ci_with_coverage = generate_coverage_job(analysis, platform, ci_content)
            logger.info("  → Coverage job added to CI pipeline.")
            _emit(state, "generate", "Coverage job added to CI pipeline.")
            ci_content = ci_with_coverage.content
            enabled.append("coverage")

        # ── SonarQube job (added into CI file, depends on coverage) ─────
        if config.has("sonarqube"):
            ci_combined = generate_sonarqube_job(analysis, platform, ci_content)
            logger.info("  → SonarQube job added to CI pipeline.")
            _emit(state, "generate", "SonarQube job added to CI pipeline.")
            ci_content = ci_combined.content
            enabled.append("sonarqube")

        # Use the latest content (could be base, +coverage, +sonar)
        final_ci = PipelineFile(
            filename=ci_pipeline.filename,
            content=ci_content,
            description=ci_pipeline.description,
        )
        all_pipelines.append(final_ci.model_dump())
        enabled.append("unit_tests")

    # ── Standalone pipelines ────────────────────────────────────────────

    _STANDALONE_GENERATORS = {
        "formatting": lambda: generate_formatting(analysis, platform, _tools_for("formatting")),
        "linting": lambda: generate_linting(analysis, platform, _tools_for("linting")),
        "secret_scanning": lambda: generate_secret_scanning(analysis, platform),
        "vulnerability_scanning": lambda: generate_vulnerability_scanning(analysis, platform, _tools_for("vulnerability_scanning")),
        "license_compliance": lambda: generate_license_compliance(analysis, platform),
        "container_scanning": lambda: generate_container_scanning(analysis, platform),
        "iac_validation": lambda: generate_iac_validation(analysis, platform),
        "iac_security": lambda: generate_iac_security(analysis, platform),
        "k8s_checks": lambda: generate_k8s_checks(analysis, platform),
        "sast": lambda: generate_sast(analysis, platform, _tools_for("sast")),
        "e2e_tests": lambda: generate_e2e_tests(analysis, platform, _tools_for("e2e_tests")),
        "integration_tests": lambda: generate_integration_tests(analysis, platform, _tools_for("integration_tests")),
        "performance_tests": lambda: generate_performance_tests(analysis, platform, _tools_for("performance_tests")),
        # Legacy "security" key — the combined security pipeline
        "security": lambda: generate_security(analysis, platform),
    }

    for key, gen_fn in _STANDALONE_GENERATORS.items():
        if not config.has(key):
            continue
        # Skip "security" if user chose granular security pipelines instead
        if key == "security" and any(
            config.has(k) for k in ["sast", "secret_scanning", "vulnerability_scanning",
                                     "container_scanning", "license_compliance"]
        ):
            continue

        try:
            pipeline = gen_fn()
            all_pipelines.append(pipeline.model_dump())
            enabled.append(key)
            _emit(state, "generate", f"{pipeline.description} generated.")
            logger.info("  → %s pipeline generated.", key)
        except Exception as e:
            logger.error("Failed to generate %s pipeline: %s", key, e)
            _emit(state, "generate", f"Failed to generate {key}: {e}")

    logger.info(
        "Generated pipelines: %s (test categories: %s)",
        enabled,
        config.test_categories or "all discovered",
    )

    # Save generated YAML to log dir
    rl = _get_logger(state)
    if rl:
        for p in all_pipelines:
            rl.save_generated_pipeline(1, p)

    return {"pipelines": all_pipelines}


def write_files_node(state: PipelineState) -> dict:
    """Write all generated pipeline files into the cloned repo."""
    repo_root = Path(state["clone_path"])
    for p in state["pipelines"]:
        filepath = repo_root / p["filename"]
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(p["content"])
    return {}


def push_and_pr_node(state: PipelineState) -> dict:
    """Commit and push the branch. PR is deferred until all phases pass."""
    branch_name = state["branch_name"]
    phase = state.get("current_phase", 1)

    attempt = state.get("fix_attempts", 0)
    phase_pipelines = _pipelines_for_phase(state["pipelines"], phase)
    phase_names = ", ".join(p["description"] for p in phase_pipelines)

    if attempt == 0:
        commit_msg = f"feat: add phase {phase} pipelines via DevOps Guardian\n\n{phase_names}"
        _emit(state, "push", "Pushing generated pipelines to branch…")
    else:
        commit_msg = f"fix: auto-fix phase {phase} pipelines (attempt {attempt})\n\n{phase_names}"
        _emit(state, "push", f"Pushing fix attempt {attempt}…")

    pushed = commit_and_push(state["clone_path"], branch_name, commit_msg)
    if not pushed:
        if attempt > 0:
            logger.info(
                "No file changes to push (phase %d, attempt %d).",
                phase, attempt,
            )
        return {"last_push_had_changes": False}

    return {"last_push_had_changes": True}


def create_pr_node(state: PipelineState) -> dict:
    """Create a pull request after all phases have completed."""
    _emit(state, "pr", "Creating pull request…")
    analysis = RepoAnalysis(**state["analysis"])
    pr_body = _build_pr_body(state["platform"], state["pipelines"])
    pr_url = create_pull_request(
        repo_url=analysis.repo_url,
        branch_name=state["branch_name"],
        title="feat: Add CI/CD pipelines (DevOps Guardian)",
        body=pr_body,
    )
    logger.info("Pull request created: %s", pr_url)
    _emit(state, "pr", f"Pull request created: {pr_url}", url=pr_url)

    rl = _get_logger(state)
    if rl:
        rl.save_pr(pr_url, state["pipelines"])

    return {"pull_request_url": pr_url}


def restructure_node(state: PipelineState) -> dict:
    """Split the combined CI pipeline into separate production files.

    After all phases pass, the CI file contains build+test, coverage, and
    sonarqube as jobs.  This node restructures them into independent files
    with proper workflow_run triggers for production use.

    Validates that every job in the combined pipeline appears in the output.
    Retries once on validation failure, then falls back to the combined file.
    """
    analysis = RepoAnalysis(**state["analysis"])
    platform = state["platform"]
    pipelines = list(state["pipelines"])

    # Find the combined CI pipeline
    ci_entry = next(
        (p for p in pipelines
         if "ci" in p.get("description", "").lower()
         and "security" not in p.get("description", "").lower()),
        None,
    )
    if ci_entry is None:
        logger.warning("Restructure — cannot find CI pipeline to split. Skipping.")
        return {}

    logger.info("Restructuring combined CI pipeline into separate production files…")
    _emit(state, "restructure", "Restructuring pipelines into separate production files…")
    rl = _get_logger(state)

    max_restructure_attempts = 2
    split_files = None

    for attempt in range(1, max_restructure_attempts + 1):
        try:
            split_files = restructure_pipelines(analysis, platform, ci_entry["content"])
            break  # Validation passed inside restructure_pipelines
        except Exception as e:
            logger.warning(
                "Restructure attempt %d/%d failed: %s",
                attempt, max_restructure_attempts, e,
            )
            if attempt >= max_restructure_attempts:
                logger.error("Restructure failed after %d attempts — keeping combined file.", attempt)
                _emit(state, "restructure", "Restructure validation failed — keeping combined pipeline.")
                return {}

    if split_files is None:
        return {}

    # Replace the combined CI entry with the split files, keep security untouched
    new_pipelines = [p for p in pipelines if p != ci_entry]
    for sf in split_files:
        new_pipelines.append(sf.model_dump())

    logger.info(
        "Restructured into %d files: %s",
        len(split_files),
        [f.filename for f in split_files],
    )

    if rl:
        for sf in split_files:
            rl.save_generated_pipeline("restructure", sf.model_dump())

    return {"pipelines": new_pipelines}


# ── Self-healing feedback loop nodes ────────────────────────────────────────


def wait_for_runs_node(state: PipelineState) -> dict:
    """Wait for the current phase's workflows to complete on the branch."""
    analysis = RepoAnalysis(**state["analysis"])
    phase = state.get("current_phase", 1)

    _emit(state, "waiting", "Waiting for workflow runs to complete…")
    logger.info(
        "Phase %d — waiting for workflow(s) on branch %s …",
        phase, state["branch_name"],
    )

    # All pipelines are push-triggered
    total_pushed = len(set(p["filename"] for p in state["pipelines"]))

    runs = wait_for_workflow_runs(
        analysis.repo_url,
        state["branch_name"],
        expected_count=total_pushed,
        cascade_workflow_names=None,
    )

    # Only judge the CURRENT phase's workflows — ignore other re-runs
    phase_keywords = _PHASE_KEYWORDS.get(phase, set())
    phase_runs = [
        r for r in runs
        if any(kw in r["name"].lower() for kw in phase_keywords)
    ]
    other_runs = [r for r in runs if r not in phase_runs]

    failed = [r for r in phase_runs if r["conclusion"] != "success"]
    passed = [r for r in phase_runs if r["conclusion"] == "success"]
    all_passed = len(failed) == 0 and len(phase_runs) > 0

    # ── Track which pipeline *files* have already passed ────────────
    pipelines_by_file = {p["filename"]: p for p in state["pipelines"]}
    already_passed = list(state.get("passed_files", []))
    for r in passed:
        matched = _match_pipeline_to_run(r["name"], pipelines_by_file)
        if matched and matched["filename"] not in already_passed:
            already_passed.append(matched["filename"])

    # Emit individual per-run results so the UI can update cards granularly
    for r in passed:
        _emit(state, "run_result", f"{r['name']}", workflow=r["name"], conclusion="success")
    for r in failed:
        _emit(state, "run_result", f"{r['name']}", workflow=r["name"], conclusion=r.get("conclusion", "failure"))

    if all_passed:
        _emit(state, "waiting", f"All {len(phase_runs)} workflow(s) passed!", passed=True)
        logger.info(
            "Phase %d — all %d workflow(s) passed. %s",
            phase, len(phase_runs),
            "(+ %d other re-runs)" % len(other_runs) if other_runs else "",
        )
    elif not phase_runs:
        _emit(state, "waiting", "No matching workflow runs found.", passed=False)
        logger.warning(
            "Phase %d — no matching workflow runs found (expected keywords: %s). "
            "Saw %d total run(s): %s",
            phase, phase_keywords, len(runs), [r["name"] for r in runs],
        )
    else:
        failed_names = [r["name"] for r in failed]
        passed_names = [r["name"] for r in passed]
        msg_parts = [f"{len(failed)}/{len(phase_runs)} workflow(s) failed: {', '.join(failed_names)}"]
        if passed_names:
            msg_parts.append(f"Passed: {', '.join(passed_names)}")
        _emit(state, "waiting", " | ".join(msg_parts), passed=False)
        logger.warning(
            "Phase %d — %d/%d workflow(s) failed: %s | Passed: %s",
            phase, len(failed), len(phase_runs), failed_names, passed_names,
        )

    # Log run results
    rl = _get_logger(state)
    if rl:
        rl.save_run_results(phase, runs)

    return {
        "all_passed": all_passed,
        "failed_runs": failed,
        "passed_files": already_passed,
    }


def fix_pipelines_node(state: PipelineState) -> dict:
    """Fetch logs for each failed run in the current phase, ask the LLM to
    fix, and rewrite files.  Only touches pipelines belonging to the current
    phase.
    """
    analysis = RepoAnalysis(**state["analysis"])
    platform = state["platform"]
    phase = state.get("current_phase", 1)
    attempt = state.get("fix_attempts", 0) + 1

    failed_runs = state.get("failed_runs", [])
    if not failed_runs:
        logger.info("Phase %d — no failed runs to fix.", phase)
        return {"fix_attempts": attempt}

    _emit(
        state, "fix",
        f"Fixing {len(failed_runs)} failed pipeline(s) (attempt {attempt}/{MAX_FIX_ATTEMPTS})…",
        attempt=attempt, max_attempts=MAX_FIX_ATTEMPTS,
    )
    logger.info(
        "Phase %d — fixing %d failed pipeline(s) (attempt %d) …",
        phase, len(failed_runs), attempt,
    )

    pipelines_by_file: dict[str, dict] = {p["filename"]: p for p in state["pipelines"]}
    fix_history: dict[str, list[dict[str, str]]] = dict(state.get("fix_history", {}))
    updated_pipelines = list(state["pipelines"])
    fixed_count = 0
    rl = _get_logger(state)

    for run_info in failed_runs:
        logger.info(
            "Fetching logs for failed workflow: %s (run %s)",
            run_info["name"], run_info["run_id"],
        )
        try:
            error_log = get_failed_run_logs(analysis.repo_url, run_info["run_id"])
        except Exception as e:
            logger.warning("Could not fetch logs for '%s': %s", run_info["name"], e)
            error_log = f"(log download failed: {e})"

        matched_pipeline = _match_pipeline_to_run(run_info["name"], pipelines_by_file)
        if not matched_pipeline:
            logger.warning(
                "Could not match workflow '%s' to a pipeline file, skipping.",
                run_info["name"],
            )
            if rl:
                rl.save_fix_skip(phase, attempt, run_info["name"], "no matching pipeline file")
            continue

        pipeline_obj = PipelineFile(**matched_pipeline)

        # ── Skip pipelines that already passed in a prior run ───────────
        if pipeline_obj.filename in state.get("passed_files", []):
            logger.info(
                "Skipping %s — already passed in a previous run.",
                pipeline_obj.filename,
            )
            if rl:
                rl.save_fix_skip(phase, attempt, run_info["name"], "already passed")
            continue

        logger.info("Sending %s to LLM for fix (attempt %d) …", pipeline_obj.filename, attempt)

        # ── Step 1: Classify the error ──────────────────────────────────
        category = classify_pipeline_error(pipeline_obj, run_info["name"], error_log)
        logger.info("Error classified as: %s (workflow: %s)", category, run_info["name"])

        prev = fix_history.get(pipeline_obj.filename, [])

        # Build run history for LLM context — shows every run attempt & outcome
        run_history = []
        for entry in prev:
            run_history.append({
                "status": "failed",
                "classification": entry.get("classification", ""),
                "error_summary": entry.get("error_log", "")[:1000],
                "fix_applied": entry.get("diff_summary", entry.get("note", "")),
            })
        # Add current run
        run_history.append({
            "status": "failed",
            "classification": category,
            "error_summary": error_log[:1000],
            "fix_applied": "(pending — this is the current failure to fix)",
        })

        # ── Step 2: Decide action based on classification ───────────────

        # Count how many consecutive times this file has been classified as test_failure
        consecutive_tf = 0
        for entry in reversed(prev):
            if entry.get("classification") == "test_failure":
                consecutive_tf += 1
            else:
                break

        if (
            category == "test_failure"
            and _allows_continue_on_error(pipeline_obj.filename)
            and consecutive_tf >= _TEST_FAILURE_COE_THRESHOLD
        ):
            # The LLM has confirmed test_failure multiple times — the tool truly ran
            # and the failures are genuine code bugs.  Apply continue-on-error.
            logger.info(
                "Pipeline %s classified as test_failure %d consecutive times — "
                "the tool is running, applying continue-on-error.",
                pipeline_obj.filename, consecutive_tf + 1,
            )
            fixed = fix_pipeline(
                analysis, platform, pipeline_obj, run_info["name"], error_log,
                fix_attempt=attempt,
                use_continue_on_error=True,
                run_history=run_history,
            )
            if rl:
                rl.save_fix_attempt(
                    phase=phase, attempt=attempt,
                    workflow_name=run_info["name"],
                    filename=pipeline_obj.filename,
                    error_log=error_log,
                    yaml_before=pipeline_obj.content,
                    yaml_after=fixed.content,
                    identical=False,
                )
            diff_summary = _compute_diff_summary(pipeline_obj.content, fixed.content)
            fix_history.setdefault(pipeline_obj.filename, []).append({
                "error_log": error_log[:3000],
                "yaml_before": pipeline_obj.content[:3000],
                "yaml_after": fixed.content[:3000],
                "diff_summary": diff_summary,
                "classification": "test_failure",
                "note": "Pipeline OK — test/lint/coverage failure, added continue-on-error",
            })
            # Write the updated YAML
            filepath = Path(state["clone_path"]) / fixed.filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(fixed.content)
            for i, p in enumerate(updated_pipelines):
                if p["filename"] == fixed.filename:
                    updated_pipelines[i] = fixed.model_dump()
                    break
            fixed_count += 1
            continue

        if category == "test_failure" and not _allows_continue_on_error(pipeline_obj.filename):
            # Security/scanning pipelines should never get continue-on-error.
            # Reclassify as pipeline_config so the LLM does a real fix.
            logger.info(
                "Pipeline %s classified as test_failure but is a security/scanning "
                "pipeline — reclassifying as pipeline_config for a real fix.",
                pipeline_obj.filename,
            )
            category = "pipeline_config"

        if category == "test_failure" and consecutive_tf < _TEST_FAILURE_COE_THRESHOLD:
            # First time(s) classified as test_failure — try a normal fix first.
            # The LLM might be wrong, or the issue might be fixable in the pipeline.
            logger.info(
                "Pipeline %s classified as test_failure (count %d/%d) — "
                "trying a normal pipeline fix before resorting to continue-on-error.",
                pipeline_obj.filename, consecutive_tf + 1, _TEST_FAILURE_COE_THRESHOLD,
            )
            category = "pipeline_config"

        if category == "missing_secret":
            # Count how many times we've already tried to fix this as missing_secret
            secret_attempts = sum(
                1 for e in prev if e.get("classification") == "missing_secret"
            )
            if secret_attempts >= _SECRET_ATTEMPT_THRESHOLD:
                use_coe = _allows_continue_on_error(pipeline_obj.filename)
                logger.warning(
                    "Skipping %s — missing_secret after %d attempts. "
                    "%s",
                    pipeline_obj.filename, secret_attempts,
                    "Adding continue-on-error." if use_coe
                    else "Attempting normal fix (COE not allowed for this pipeline type).",
                )
                fixed = fix_pipeline(
                    analysis, platform, pipeline_obj, run_info["name"], error_log,
                    fix_attempt=attempt,
                    use_continue_on_error=use_coe,
                    run_history=run_history,
                )
                if rl:
                    rl.save_fix_attempt(
                        phase=phase, attempt=attempt,
                        workflow_name=run_info["name"],
                        filename=pipeline_obj.filename,
                        error_log=error_log,
                        yaml_before=pipeline_obj.content,
                        yaml_after=fixed.content,
                        identical=False,
                    )
                diff_summary = _compute_diff_summary(pipeline_obj.content, fixed.content)
                fix_history.setdefault(pipeline_obj.filename, []).append({
                    "error_log": error_log[:3000],
                    "yaml_before": pipeline_obj.content[:3000],
                    "yaml_after": fixed.content[:3000],
                    "diff_summary": diff_summary,
                    "classification": "missing_secret",
                    "note": "Skipped — missing secret/env var, added continue-on-error",
                })
                filepath = Path(state["clone_path"]) / fixed.filename
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.write_text(fixed.content)
                for i, p in enumerate(updated_pipelines):
                    if p["filename"] == fixed.filename:
                        updated_pipelines[i] = fixed.model_dump()
                        break
                fixed_count += 1
                continue
            # Otherwise, let it try — maybe the LLM can reference a secret
            logger.info(
                "missing_secret detected for %s (attempt %d/%d) — "
                "trying pipeline fix before giving up.",
                pipeline_obj.filename, secret_attempts + 1, _SECRET_ATTEMPT_THRESHOLD,
            )

        # ── Step 3: Normal pipeline_config fix (or first missing_secret attempt)
        # Split history: non-identical = applied fixes, identical = failed attempts
        applied = [e for e in prev if e.get("yaml_after") and not e.get("note")]
        failed_prev = [e for e in prev if e.get("note")]
        # Prepend cross-execution knowledge for this pipeline file
        knowledge = load_fix_knowledge(analysis.repo_url)
        prior = [k for k in knowledge if k.get("filename") == pipeline_obj.filename]
        all_applied = prior + applied

        fixed = fix_pipeline(
            analysis, platform, pipeline_obj, run_info["name"], error_log,
            fix_attempt=attempt,
            previous_attempts=failed_prev if failed_prev else None,
            applied_fixes=all_applied if all_applied else None,
            run_history=run_history,
        )

        # Detect when LLM produces identical YAML (no real fix)
        identical = fixed.content.strip() == pipeline_obj.content.strip()

        # Save detailed artefacts to log dir
        if rl:
            rl.save_fix_attempt(
                phase=phase,
                attempt=attempt,
                workflow_name=run_info["name"],
                filename=pipeline_obj.filename,
                error_log=error_log,
                yaml_before=pipeline_obj.content,
                yaml_after=fixed.content,
                identical=identical,
            )

        if identical:
            logger.warning(
                "LLM returned identical YAML for '%s' — attempt %d produced no change.",
                pipeline_obj.filename, attempt,
            )
            fix_history.setdefault(pipeline_obj.filename, []).append({
                "error_log": error_log[:3000],
                "yaml_before": pipeline_obj.content[:3000],
                "note": "LLM produced identical YAML — no change",
                "classification": category,
            })
            continue

        diff_summary = _compute_diff_summary(pipeline_obj.content, fixed.content)
        fix_history.setdefault(pipeline_obj.filename, []).append({
            "error_log": error_log[:3000],
            "yaml_before": pipeline_obj.content[:3000],
            "yaml_after": fixed.content[:3000],
            "diff_summary": diff_summary,
            "classification": category,
        })

        filepath = Path(state["clone_path"]) / fixed.filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(fixed.content)

        for i, p in enumerate(updated_pipelines):
            if p["filename"] == fixed.filename:
                updated_pipelines[i] = fixed.model_dump()
                break

        fixed_count += 1

    logger.info("Phase %d — fixed %d pipeline(s).", phase, fixed_count)
    return {
        "pipelines": updated_pipelines,
        "fix_attempts": attempt,
        "fix_history": fix_history,
    }


# ── Conditional edges ───────────────────────────────────────────────────────


def after_push(state: PipelineState) -> str:
    """After push_and_pr: skip wait if nothing was pushed."""
    if not state.get("last_push_had_changes", True):
        if state.get("fix_attempts", 0) >= MAX_FIX_ATTEMPTS:
            return "give_up"
        return "fix_pipelines"
    return "wait_for_runs"


def should_retry(state: PipelineState) -> str:
    """After wait_for_runs: retry fix, finish, or give up."""
    if state.get("all_passed", False):
        return "all_done"

    # If no runs were found AND no failures, the pipeline never triggered.
    failed_runs = state.get("failed_runs", [])
    if not failed_runs:
        logger.warning(
            "No workflow runs found. The pipeline likely didn't trigger. "
            "Marking as failed.",
        )
        rl = _get_logger(state)
        if rl:
            rl.save_phase_outcome(1, passed=False,
                                  attempts_used=state.get("fix_attempts", 0))
        return "give_up"

    if state.get("fix_attempts", 0) >= MAX_FIX_ATTEMPTS:
        logger.warning(
            "Failed after %d fix attempts. Creating PR with current state.",
            MAX_FIX_ATTEMPTS,
        )
        rl = _get_logger(state)
        if rl:
            rl.save_phase_outcome(1, passed=False, attempts_used=MAX_FIX_ATTEMPTS)
        return "give_up"
    return "fix_pipelines"


def cleanup_node(state: PipelineState) -> dict:
    """Remove the cloned repo from disk."""
    rl = _get_logger(state)
    if rl:
        logger.info("Agent 2 — done. Cleaning up cloned repo.")
        rl.detach()
    # Persist fix knowledge for future runs
    fix_history = state.get("fix_history", {})
    if fix_history:
        analysis = RepoAnalysis(**state["analysis"])
        save_fix_knowledge(analysis.repo_url, fix_history)
    # Clear the cached logger so subsequent runs don't reuse it
    run_dir = state.get("run_dir", "")
    _logger_cache.pop(run_dir, None)
    cleanup_repo(state["clone_path"])
    return {}


# ── Final combined verification ─────────────────────────────────────────────


def final_verify_push_node(state: PipelineState) -> dict:
    """Push all pipelines (with a marker comment) to trigger a final combined run."""
    _emit(state, "fix", "Running final combined verification — pushing all pipelines…")
    logger.info("Final verify — pushing all pipelines for combined run.")
    from datetime import datetime

    # Touch each pipeline file to ensure git detects a change
    for p in state["pipelines"]:
        fpath = Path(state["clone_path"]) / p["filename"]
        if fpath.exists():
            content = fpath.read_text()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            marker = "# final-verify-timestamp:"
            lines = content.splitlines(keepends=True)
            new_lines = [l for l in lines if marker not in l]
            new_lines.insert(0, f"{marker} {ts}\n")
            fpath.write_text("".join(new_lines))

    commit_msg = "chore: final combined verification run via DevOps Guardian"
    pushed = commit_and_push(state["clone_path"], state["branch_name"], commit_msg)
    if not pushed:
        logger.warning("Final verify push — no changes detected, forcing empty commit.")
    return {}


def final_verify_wait_node(state: PipelineState) -> dict:
    """Wait for the final combined run and check results."""
    analysis = RepoAnalysis(**state["analysis"])
    _emit(state, "waiting", "Waiting for final combined verification run…")
    logger.info("Final verify — waiting for combined workflow runs.")

    total_pushed = len(set(p["filename"] for p in state["pipelines"]))
    runs = wait_for_workflow_runs(
        analysis.repo_url,
        state["branch_name"],
        expected_count=total_pushed,
        cascade_workflow_names=None,
    )

    phase_keywords = _PHASE_KEYWORDS.get(1, set())
    phase_runs = [
        r for r in runs
        if any(kw in r["name"].lower() for kw in phase_keywords)
    ]
    failed = [r for r in phase_runs if r["conclusion"] != "success"]
    all_passed = len(failed) == 0 and len(phase_runs) > 0

    # Emit per-run results for UI
    for r in phase_runs:
        conclusion = r.get("conclusion", "failure")
        _emit(state, "run_result", r["name"], workflow=r["name"], conclusion=conclusion)

    if all_passed:
        _emit(state, "waiting", f"Final verification passed — all {len(phase_runs)} workflows succeeded!", passed=True)
        logger.info("Final verify — all %d workflows passed.", len(phase_runs))
    else:
        failed_names = [r["name"] for r in failed]
        _emit(state, "waiting", f"Final verification: {len(failed)} workflow(s) still failing: {', '.join(failed_names)}", passed=False)
        logger.warning("Final verify — %d workflow(s) failed: %s", len(failed), failed_names)

    # Remove the timestamp markers we added and push clean state
    for p in state["pipelines"]:
        fpath = Path(state["clone_path"]) / p["filename"]
        if fpath.exists():
            content = fpath.read_text()
            marker = "# final-verify-timestamp:"
            lines = content.splitlines(keepends=True)
            cleaned = [l for l in lines if marker not in l]
            fpath.write_text("".join(cleaned))
    commit_and_push(
        state["clone_path"], state["branch_name"],
        "chore: remove verification markers",
    )

    return {"all_passed": all_passed, "failed_runs": failed}


def after_final_verify(state: PipelineState) -> str:
    """After the final combined verification — proceed to restructure or give up."""
    if state.get("all_passed", False):
        return "restructure"
    logger.warning("Final combined verification failed. Creating PR with current state.")
    return "give_up"


def assemble_node(state: PipelineState) -> dict:
    """Assemble all generated pipelines into the final result."""
    analysis = RepoAnalysis(**state["analysis"])
    result = PipelineResult(
        repo_url=analysis.repo_url,
        platform=state["platform"],
        pipelines=[PipelineFile(**p) for p in state["pipelines"]],
        branch_name=state["branch_name"],
        pull_request_url=state.get("pull_request_url", ""),
        fix_attempts=state.get("fix_attempts", 0),
        all_passed=state.get("all_passed", False),
    )
    return {"result": result.model_dump()}


# ── Build graph ──────────────────────────────────────────────────────────────


def build_graph() -> StateGraph:
    graph = StateGraph(PipelineState)

    # Nodes
    graph.add_node("clone_and_branch", clone_and_branch_node)
    graph.add_node("resolve_platform", resolve_platform_node)
    graph.add_node("generate_phase", generate_phase_node)
    graph.add_node("write_files", write_files_node)
    graph.add_node("push_and_pr", push_and_pr_node)
    graph.add_node("wait_for_runs", wait_for_runs_node)
    graph.add_node("fix_pipelines", fix_pipelines_node)
    graph.add_node("final_verify_push", final_verify_push_node)
    graph.add_node("final_verify_wait", final_verify_wait_node)
    graph.add_node("restructure", restructure_node)
    graph.add_node("write_restructured", write_files_node)
    graph.add_node("push_restructured", push_and_pr_node)
    graph.add_node("create_pr", create_pr_node)
    graph.add_node("cleanup", cleanup_node)
    graph.add_node("assemble", assemble_node)

    # ── Flow ──────────────────────────────────────────────────────────────
    graph.set_entry_point("clone_and_branch")

    graph.add_edge("clone_and_branch", "resolve_platform")
    graph.add_edge("resolve_platform", "generate_phase")
    graph.add_edge("generate_phase", "write_files")
    graph.add_edge("write_files", "push_and_pr")

    # After push → wait (or skip if nothing changed)
    graph.add_conditional_edges("push_and_pr", after_push, {
        "wait_for_runs": "wait_for_runs",
        "fix_pipelines": "fix_pipelines",
        "give_up": "cleanup",
    })

    # After wait → retry fix or finish
    graph.add_conditional_edges("wait_for_runs", should_retry, {
        "fix_pipelines": "fix_pipelines",
        "give_up": "cleanup",
        "all_done": "final_verify_push",
    })

    # Fix loop: fix → write → push → (back to wait/retry via after_push)
    graph.add_edge("fix_pipelines", "write_files")

    # Final combined verification: push → wait → decide
    graph.add_edge("final_verify_push", "final_verify_wait")
    graph.add_conditional_edges("final_verify_wait", after_final_verify, {
        "restructure": "restructure",
        "give_up": "cleanup",
    })

    # After all pass → restructure into production files → push → PR
    graph.add_edge("restructure", "write_restructured")
    graph.add_edge("write_restructured", "push_restructured")
    graph.add_edge("push_restructured", "create_pr")

    # PR created → cleanup
    graph.add_edge("create_pr", "cleanup")

    graph.add_edge("cleanup", "assemble")
    graph.add_edge("assemble", END)

    return graph


def run_pipeline_generator(
    analysis: RepoAnalysis,
    run_dir: str = "",
    config: PipelineConfig | None = None,
    progress_callback: Callable[..., None] | None = None,
) -> PipelineResult:
    """Run the full pipeline generation and return structured output."""
    if config is None:
        config = PipelineConfig()

    graph = build_graph()
    app = graph.compile()

    initial_state: PipelineState = {
        "analysis": analysis.model_dump(),
        "config": config.model_dump(),
        "platform": "",
        "pipelines": [],
        "clone_path": "",
        "branch_name": "",
        "pull_request_url": "",
        "fix_attempts": 0,
        "all_passed": False,
        "failed_runs": [],
        "passed_files": [],
        "fix_history": {},
        "last_push_had_changes": True,
        "current_phase": 1,
        "phase_failed": False,
        "run_dir": run_dir,
        "progress_callback": progress_callback,
        "result": {},
    }

    final_state = app.invoke(initial_state)
    return PipelineResult(**final_state["result"])
