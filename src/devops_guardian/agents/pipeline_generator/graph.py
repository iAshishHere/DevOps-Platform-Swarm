"""LangGraph state graph for the Pipeline Generator agent.

Execution is **phased** so that downstream pipelines are only generated
after their upstream dependency is confirmed green:

  Phase 1 — CI + Security  (independent, push-triggered)
  Phase 2 — Coverage       (depends on CI)
  Phase 3 — SonarQube      (depends on Coverage)

Each phase runs its own generate → write → push → wait → fix loop.
If a phase cannot be fixed within ``MAX_FIX_ATTEMPTS``, all later phases
are skipped.
"""

from __future__ import annotations

import datetime
import difflib
import json as _json
import logging
import os
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from devops_guardian.agents.pipeline_generator.generators.pipelines import (
    classify_pipeline_error,
    fix_pipeline,
    generate_ci,
    generate_coverage_job,
    generate_security,
    generate_sonarqube_job,
    restructure_pipelines,
)
from devops_guardian.agents.pipeline_generator.generators.platform import resolve_platform
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.models.pipeline import PipelineFile, PipelineResult
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
_MAX_PHASE = 3

# Phase → keywords used to identify pipelines that belong to that phase
# Phases 2/3 modify the CI file (add jobs), so their runs are the CI workflow.
_PHASE_KEYWORDS: dict[int, set[str]] = {
    1: {"ci", "security"},
    2: {"ci"},       # Coverage is a job inside the CI workflow
    3: {"ci"},       # SonarQube is also a job inside the CI workflow
}


class PipelineState(TypedDict):
    analysis: dict[str, Any]
    platform: str
    pipelines: list[dict[str, Any]]
    clone_path: str
    branch_name: str
    pull_request_url: str
    fix_attempts: int            # per-phase counter — reset on phase advance
    all_passed: bool             # True when current phase's runs all passed
    failed_runs: list[dict[str, Any]]
    fix_history: dict[str, list[dict[str, str]]]
    last_push_had_changes: bool
    current_phase: int           # 1, 2, 3
    phase_failed: bool           # True when a phase exhausted MAX_FIX_ATTEMPTS
    run_dir: str                 # path to the run output directory (empty = no logging)
    result: dict[str, Any]


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

    keyword_groups = [
        ["sonarqube", "sonar"],
        ["coverage"],
        ["security"],
        ["ci"],
    ]

    for kws in keyword_groups:
        if any(kw in wf_lower for kw in kws):
            for filename, pdata in pipelines_by_file.items():
                fname_lower = filename.lower()
                desc_lower = pdata.get("description", "").lower()
                if any(kw in fname_lower or kw in desc_lower for kw in kws):
                    return pdata

    for filename, pdata in pipelines_by_file.items():
        stem = Path(filename).stem.lower()
        if stem in wf_lower or wf_lower in stem:
            return pdata
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
    clone_path = clone_repo(analysis.repo_url)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch_name = f"devops-guardian/pipelines-{ts}"
    create_branch(clone_path, branch_name)

    rl = _get_logger(state)
    if rl:
        rl.attach()
        logger.info("Agent 2 — cloned %s, branch %s", analysis.repo_url, branch_name)

    return {"clone_path": clone_path, "branch_name": branch_name}


def resolve_platform_node(state: PipelineState) -> dict:
    """Determine which CI/CD platform to target."""
    analysis = RepoAnalysis(**state["analysis"])
    platform = resolve_platform(analysis)

    rl = _get_logger(state)
    if rl:
        rl.save_platform(platform)
        logger.info("Resolved CI/CD platform: %s", platform)

    return {"platform": platform}


# ── Phase-aware generation ──────────────────────────────────────────────────


def generate_phase_node(state: PipelineState) -> dict:
    """Generate pipelines for the current phase only.

    Phase 1: Create CI + Security as separate files.
    Phase 2: Add a coverage job to the existing CI file.
    Phase 3: Add a SonarQube job to the existing CI file.
    """
    phase = state.get("current_phase", 1)
    analysis = RepoAnalysis(**state["analysis"])
    platform = state["platform"]

    pipelines = list(state["pipelines"])
    new_pipelines: list[dict] = []

    if phase == 1:
        logger.info("Phase 1 — generating CI + Security pipelines.")
        new_pipelines.append(generate_ci(analysis, platform).model_dump())
        new_pipelines.append(generate_security(analysis, platform).model_dump())
    elif phase == 2:
        logger.info("Phase 2 — adding coverage job to CI pipeline.")
        # Find the existing CI pipeline
        ci_idx, ci_entry = next(
            ((i, p) for i, p in enumerate(pipelines)
             if "ci" in p.get("description", "").lower()
             and "security" not in p.get("description", "").lower()),
            (None, None),
        )
        if ci_entry is None:
            logger.error("Phase 2 — cannot find CI pipeline to add coverage job!")
            return {"pipelines": pipelines}
        updated = generate_coverage_job(analysis, platform, ci_entry["content"])
        pipelines[ci_idx] = updated.model_dump()
    elif phase == 3:
        logger.info("Phase 3 — adding SonarQube job to CI pipeline.")
        ci_idx, ci_entry = next(
            ((i, p) for i, p in enumerate(pipelines)
             if "ci" in p.get("description", "").lower()
             and "security" not in p.get("description", "").lower()),
            (None, None),
        )
        if ci_entry is None:
            logger.error("Phase 3 — cannot find CI pipeline to add SonarQube job!")
            return {"pipelines": pipelines}
        updated = generate_sonarqube_job(analysis, platform, ci_entry["content"])
        pipelines[ci_idx] = updated.model_dump()
    else:
        logger.warning("Unknown phase %d — nothing to generate.", phase)

    # Save generated YAML to log dir
    rl = _get_logger(state)
    if rl:
        if new_pipelines:
            for p in new_pipelines:
                rl.save_generated_pipeline(phase, p)
        elif phase in (2, 3):
            ci_entry = next(
                (p for p in pipelines
                 if "ci" in p.get("description", "").lower()
                 and "security" not in p.get("description", "").lower()),
                None,
            )
            if ci_entry:
                rl.save_generated_pipeline(phase, ci_entry)

    return {"pipelines": pipelines + new_pipelines}


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
    else:
        commit_msg = f"fix: auto-fix phase {phase} pipelines (attempt {attempt})\n\n{phase_names}"

    pushed = commit_and_push(state["clone_path"], branch_name, commit_msg)
    if not pushed and attempt > 0:
        logger.warning(
            "Nothing changed after fix attempt %d (phase %d) — "
            "LLM produced identical YAML. Skipping wait cycle.",
            attempt, phase,
        )
        return {"all_passed": False, "last_push_had_changes": False}

    return {"last_push_had_changes": True}


def create_pr_node(state: PipelineState) -> dict:
    """Create a pull request after all phases have completed."""
    analysis = RepoAnalysis(**state["analysis"])
    pr_body = _build_pr_body(state["platform"], state["pipelines"])
    pr_url = create_pull_request(
        repo_url=analysis.repo_url,
        branch_name=state["branch_name"],
        title="feat: Add CI/CD pipelines (DevOps Guardian)",
        body=pr_body,
    )
    logger.info("Pull request created: %s", pr_url)

    rl = _get_logger(state)
    if rl:
        rl.save_pr(pr_url, state["pipelines"])

    return {"pull_request_url": pr_url}


def restructure_node(state: PipelineState) -> dict:
    """Split the combined CI pipeline into separate production files.

    After all phases pass, the CI file contains build+test, coverage, and
    sonarqube as jobs.  This node restructures them into independent files
    with proper workflow_run triggers for production use.
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
    rl = _get_logger(state)

    try:
        split_files = restructure_pipelines(analysis, platform, ci_entry["content"])
    except Exception as e:
        logger.error("Restructure failed: %s — keeping combined file.", e)
        if rl:
            logger.info("Restructure error detail: %s", str(e))
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
    phase_pipelines = _pipelines_for_phase(state["pipelines"], phase)

    logger.info(
        "Phase %d — waiting for workflow(s) on branch %s …",
        phase, state["branch_name"],
    )

    # All pipelines are push-triggered (phases 2/3 modify the CI file, no cascade)
    total_pushed = len(set(p["filename"] for p in state["pipelines"]))

    runs = wait_for_workflow_runs(
        analysis.repo_url,
        state["branch_name"],
        expected_count=total_pushed,
        cascade_workflow_names=None,  # No cascade needed — all push-triggered
    )

    # Only judge the CURRENT phase's workflows — ignore other re-runs
    phase_keywords = _PHASE_KEYWORDS.get(phase, set())
    phase_runs = [
        r for r in runs
        if any(kw in r["name"].lower() for kw in phase_keywords)
    ]
    other_runs = [r for r in runs if r not in phase_runs]

    failed = [r for r in phase_runs if r["conclusion"] != "success"]
    all_passed = len(failed) == 0 and len(phase_runs) > 0

    if all_passed:
        logger.info(
            "Phase %d — all %d workflow(s) passed. %s",
            phase, len(phase_runs),
            "(+ %d other re-runs)" % len(other_runs) if other_runs else "",
        )
    elif not phase_runs:
        logger.warning(
            "Phase %d — no matching workflow runs found (expected keywords: %s). "
            "Saw %d total run(s): %s",
            phase, phase_keywords, len(runs), [r["name"] for r in runs],
        )
    else:
        logger.warning(
            "Phase %d — %d/%d workflow(s) failed: %s",
            phase, len(failed), len(phase_runs), [r["name"] for r in failed],
        )

    # Log run results
    rl = _get_logger(state)
    if rl:
        rl.save_run_results(phase, runs)

    return {"all_passed": all_passed, "failed_runs": failed}


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
        logger.info("Sending %s to LLM for fix (attempt %d) …", pipeline_obj.filename, attempt)

        # ── Step 1: Classify the error ──────────────────────────────────
        category = classify_pipeline_error(pipeline_obj, run_info["name"], error_log)
        logger.info("Error classified as: %s (workflow: %s)", category, run_info["name"])

        prev = fix_history.get(pipeline_obj.filename, [])

        # ── Step 2: Decide action based on classification ───────────────
        if category == "app_code":
            # App code issue — skip immediately, no YAML fix will help
            logger.warning(
                "Skipping %s — classified as app_code issue. "
                "Adding continue-on-error to the failing step.",
                pipeline_obj.filename,
            )
            fixed = fix_pipeline(
                analysis, platform, pipeline_obj, run_info["name"], error_log,
                fix_attempt=attempt,
                use_continue_on_error=True,
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
                "classification": "app_code",
                "note": "Skipped — app code issue, added continue-on-error",
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

        if category == "missing_secret":
            # Count how many times we've already tried to fix this as missing_secret
            secret_attempts = sum(
                1 for e in prev if e.get("classification") == "missing_secret"
            )
            if secret_attempts >= _SECRET_ATTEMPT_THRESHOLD:
                logger.warning(
                    "Skipping %s — missing_secret after %d attempts. "
                    "Adding continue-on-error with comment.",
                    pipeline_obj.filename, secret_attempts,
                )
                fixed = fix_pipeline(
                    analysis, platform, pipeline_obj, run_info["name"], error_log,
                    fix_attempt=attempt,
                    use_continue_on_error=True,
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


def advance_phase_node(state: PipelineState) -> dict:
    """Move to the next phase and reset per-phase counters."""
    phase = state.get("current_phase", 1)
    next_phase = phase + 1
    logger.info("Advancing to phase %d.", next_phase)

    rl = _get_logger(state)
    if rl:
        rl.save_phase_outcome(phase, passed=True, attempts_used=state.get("fix_attempts", 0))

    return {
        "current_phase": next_phase,
        "fix_attempts": 0,
        "all_passed": False,
        "failed_runs": [],
        "last_push_had_changes": True,
    }


# ── Conditional edges ───────────────────────────────────────────────────────


def after_push(state: PipelineState) -> str:
    """After push_and_pr: skip wait if nothing was pushed."""
    if not state.get("last_push_had_changes", True):
        if state.get("fix_attempts", 0) >= MAX_FIX_ATTEMPTS:
            return "phase_done_fail"
        return "fix_pipelines"
    return "wait_for_runs"


def should_retry(state: PipelineState) -> str:
    """After wait_for_runs: retry, advance, or give up."""
    if state.get("all_passed", False):
        phase = state.get("current_phase", 1)
        if phase >= _MAX_PHASE:
            return "all_done"       # last phase passed — go to cleanup
        return "phase_done_pass"    # advance to next phase

    # If no phase runs were found AND no failures, the pipeline never triggered —
    # don't waste attempts retrying, treat as phase failure.
    failed_runs = state.get("failed_runs", [])
    if not failed_runs:
        phase = state.get("current_phase", 1)
        logger.warning(
            "Phase %d — no workflow runs found for this phase. "
            "The pipeline likely didn't trigger (workflow_run cascade issue). "
            "Marking phase as failed.",
            phase,
        )
        rl = _get_logger(state)
        if rl:
            rl.save_phase_outcome(phase, passed=False,
                                  attempts_used=state.get("fix_attempts", 0))
        return "phase_done_fail"

    if state.get("fix_attempts", 0) >= MAX_FIX_ATTEMPTS:
        phase = state.get("current_phase", 1)
        skipped = list(range(phase + 1, _MAX_PHASE + 1))
        if skipped:
            logger.warning(
                "Phase %d failed after %d attempts. Skipping downstream phases %s.",
                phase, MAX_FIX_ATTEMPTS, skipped,
            )
        else:
            logger.warning(
                "Phase %d (last) failed after %d attempts.", phase, MAX_FIX_ATTEMPTS,
            )
        # Log phase failure
        rl = _get_logger(state)
        if rl:
            rl.save_phase_outcome(phase, passed=False, attempts_used=MAX_FIX_ATTEMPTS)
        return "phase_done_fail"
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
    graph.add_node("advance_phase", advance_phase_node)
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
        "phase_done_fail": "cleanup",
    })

    # After wait → retry fix, advance phase, or finish
    graph.add_conditional_edges("wait_for_runs", should_retry, {
        "fix_pipelines": "fix_pipelines",
        "phase_done_pass": "advance_phase",
        "phase_done_fail": "cleanup",
        "all_done": "restructure",
    })

    # Fix loop: fix → write → push → (back to wait/retry via after_push)
    graph.add_edge("fix_pipelines", "write_files")

    # Phase advanced → generate next phase's pipelines
    graph.add_edge("advance_phase", "generate_phase")

    # After all phases pass → restructure into production files → push → PR
    graph.add_edge("restructure", "write_restructured")
    graph.add_edge("write_restructured", "push_restructured")
    graph.add_edge("push_restructured", "create_pr")

    # PR created → cleanup
    graph.add_edge("create_pr", "cleanup")

    graph.add_edge("cleanup", "assemble")
    graph.add_edge("assemble", END)

    return graph


def run_pipeline_generator(
    analysis: RepoAnalysis, run_dir: str = "",
) -> PipelineResult:
    """Run the full pipeline generation and return structured output."""
    graph = build_graph()
    app = graph.compile()

    initial_state: PipelineState = {
        "analysis": analysis.model_dump(),
        "platform": "",
        "pipelines": [],
        "clone_path": "",
        "branch_name": "",
        "pull_request_url": "",
        "fix_attempts": 0,
        "all_passed": False,
        "failed_runs": [],
        "fix_history": {},
        "last_push_had_changes": True,
        "current_phase": 1,
        "phase_failed": False,
        "run_dir": run_dir,
        "result": {},
    }

    final_state = app.invoke(initial_state)
    return PipelineResult(**final_state["result"])
