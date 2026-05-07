"""GitHub API operations using PyGitHub."""

from __future__ import annotations

import io
import os
import re
import time
import zipfile

import requests as _requests
from github import Github


def _get_github() -> Github:
    """Return an authenticated Github client."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token or token == "your-github-token":
        raise RuntimeError(
            "GITHUB_TOKEN is not set. A GitHub PAT with 'Contents: Write', "
            "'Pull requests: Write', and 'Actions: Read' permissions is required."
        )
    return Github(token)


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract owner and repo name from a GitHub HTTPS URL."""
    match = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", repo_url)
    if not match:
        raise ValueError(f"Cannot parse GitHub owner/repo from URL: {repo_url}")
    return match.group(1), match.group(2)


def create_pull_request(
    repo_url: str,
    branch_name: str,
    title: str,
    body: str,
    base: str | None = None,
) -> str:
    """Create a pull request on GitHub and return its URL."""
    if base is None:
        base = os.environ.get("DEFAULT_BRANCH", "main")
    owner, repo_name = _parse_owner_repo(repo_url)
    gh = _get_github()
    repo = gh.get_repo(f"{owner}/{repo_name}")
    pr = repo.create_pull(title=title, body=body, head=branch_name, base=base)
    return pr.html_url


# ── GitHub Actions helpers ───────────────────────────────────────────────────

_POLL_INTERVAL = int(os.environ.get("PIPELINE_POLL_INTERVAL", "30"))
_MAX_WAIT = int(os.environ.get("PIPELINE_WAIT_TIMEOUT", "900"))


def wait_for_workflow_runs(
    repo_url: str,
    branch_name: str,
    *,
    max_wait: int = _MAX_WAIT,
    poll_interval: int = _POLL_INTERVAL,
    expected_count: int = 0,
    cascade_workflow_names: list[str] | None = None,
) -> list[dict]:
    """Wait for the *latest* workflow run per workflow on *branch_name* to finish.

    Fetches ALL runs (push, workflow_run, etc.) — not filtered by event type.
    After all visible runs complete, waits one extra cycle to catch cascade
    workflows (e.g. Coverage triggered by CI completing).

    If *cascade_workflow_names* is given, also polls for workflow_run-triggered
    runs that won't appear under branch filtering (GitHub doesn't associate
    them with the source branch). These are searched repo-wide and matched
    by workflow name + recency.

    Returns a list of dicts (one per workflow): {name, conclusion, html_url, run_id}.
    """
    import datetime as _dt

    owner, repo_name = _parse_owner_repo(repo_url)
    gh = _get_github()
    repo = gh.get_repo(f"{owner}/{repo_name}")
    # Timestamp to scope cascade run searches — only runs created after this point
    search_start = _dt.datetime.now(_dt.timezone.utc)

    elapsed = 0
    # Give GitHub a moment to pick up the push and trigger workflows
    time.sleep(min(20, max_wait))
    elapsed += 20

    settled_count = 0  # consecutive polls where all runs are complete & count stable

    while elapsed < max_wait:
        runs = list(repo.get_workflow_runs(branch=branch_name))

        # Also search for cascade workflows (workflow_run triggered) that
        # don't appear under the branch filter
        if cascade_workflow_names:
            branch_run_names = {r.name for r in runs}
            for wf_name in cascade_workflow_names:
                if wf_name not in branch_run_names:
                    # Search all recent runs for this workflow
                    all_runs = list(repo.get_workflow_runs())
                    for r in all_runs:
                        if (r.name == wf_name
                                and r.created_at >= search_start
                                and r.name not in branch_run_names):
                            runs.append(r)
                            branch_run_names.add(r.name)
                            break

        if not runs:
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue

        latest = _latest_per_workflow(runs)

        if all(r.status == "completed" for r in latest.values()):
            # Wait one extra cycle to catch cascade-triggered workflows
            # that may not have started yet (e.g. Coverage after CI completes)
            if settled_count >= 1 and len(latest) >= max(expected_count, 1):
                return [
                    {
                        "name": r.name,
                        "conclusion": r.conclusion,
                        "html_url": r.html_url,
                        "run_id": r.id,
                    }
                    for r in latest.values()
                ]
            settled_count += 1
        else:
            settled_count = 0

        time.sleep(poll_interval)
        elapsed += poll_interval

    # Timed out — return whatever we have
    runs = list(repo.get_workflow_runs(branch=branch_name))
    if cascade_workflow_names:
        branch_run_names = {r.name for r in runs}
        for wf_name in cascade_workflow_names:
            if wf_name not in branch_run_names:
                all_runs = list(repo.get_workflow_runs())
                for r in all_runs:
                    if (r.name == wf_name
                            and r.created_at >= search_start
                            and r.name not in branch_run_names):
                        runs.append(r)
                        branch_run_names.add(r.name)
                        break

    latest = _latest_per_workflow(runs)
    return [
        {
            "name": r.name,
            "conclusion": r.conclusion or "timed_out",
            "html_url": r.html_url,
            "run_id": r.id,
        }
        for r in latest.values()
    ]


def _latest_per_workflow(runs: list) -> dict:
    """Given a list of WorkflowRun objects, keep only the most recent per workflow name."""
    latest: dict = {}
    for r in runs:
        existing = latest.get(r.name)
        if existing is None or r.created_at > existing.created_at:
            latest[r.name] = r
    return latest


def get_failed_run_logs(repo_url: str, run_id: int) -> str:
    """Download and return the logs for a specific workflow run.

    Returns the concatenated log text (truncated to ~30 000 chars to keep
    LLM context manageable).
    """
    owner, repo_name = _parse_owner_repo(repo_url)
    gh = _get_github()
    repo = gh.get_repo(f"{owner}/{repo_name}")
    run = repo.get_workflow_run(run_id)

    # Download logs via requests (PyGitHub doesn't handle the 302 → zip redirect)
    token = os.environ.get("GITHUB_TOKEN", "")
    resp = _requests.get(
        run.logs_url,
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
        allow_redirects=True,
        timeout=60,
    )
    if resp.status_code != 200:
        return f"(could not download logs: HTTP {resp.status_code})"

    data = resp.content

    log_text = ""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        for name in zf.namelist():
            log_text += f"\n\n=== {name} ===\n"
            log_text += zf.read(name).decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        log_text = data.decode("utf-8", errors="replace")

    # Truncate to keep within LLM context limits
    max_chars = 30_000
    if len(log_text) > max_chars:
        log_text = log_text[-max_chars:]
        log_text = "… (log truncated) …\n" + log_text

    return log_text
