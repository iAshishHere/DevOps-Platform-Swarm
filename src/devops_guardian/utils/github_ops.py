"""GitHub API operations using PyGitHub."""

from __future__ import annotations

import io
import logging
import os
import re
import time
import zipfile

import requests as _requests
from github import Github

logger = logging.getLogger(__name__)


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


def detect_github_features(repo_url: str) -> dict:
    """Probe the GitHub API to discover which security features are enabled.

    Returns a dict matching the GitHubFeatures model fields.
    """
    from devops_guardian.models.analysis import GitHubFeatures

    owner, repo_name = _parse_owner_repo(repo_url)
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
    }
    base = f"https://api.github.com/repos/{owner}/{repo_name}"
    features = GitHubFeatures()

    # 1. Basic repo metadata
    try:
        resp = _requests.get(base, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            features.is_private = data.get("private", False)
            features.default_branch = data.get("default_branch", "main")
    except Exception as e:
        logger.warning("Failed to fetch repo metadata: %s", e)

    # 2. Code scanning — try listing alerts; 403/404 = not enabled
    try:
        resp = _requests.get(
            f"{base}/code-scanning/alerts",
            headers=headers, params={"per_page": 1}, timeout=10,
        )
        features.code_scanning_enabled = resp.status_code in (200, 304)
    except Exception:
        pass

    # 3. Dependabot alerts
    try:
        resp = _requests.get(
            f"{base}/dependabot/alerts",
            headers=headers, params={"per_page": 1}, timeout=10,
        )
        features.dependabot_alerts_enabled = resp.status_code in (200, 304)
    except Exception:
        pass

    # 4. Secret scanning — check via security-and-analysis endpoint
    try:
        resp = _requests.get(
            f"{base}/secret-scanning/alerts",
            headers=headers, params={"per_page": 1}, timeout=10,
        )
        features.secret_scanning_enabled = resp.status_code in (200, 304)
    except Exception:
        pass

    logger.info(
        "GitHub features for %s/%s: code_scanning=%s, dependabot=%s, "
        "secret_scanning=%s, private=%s",
        owner, repo_name,
        features.code_scanning_enabled,
        features.dependabot_alerts_enabled,
        features.secret_scanning_enabled,
        features.is_private,
    )

    result = features.model_dump()

    # 5. GitHub Actions secrets — list names (not values) for env var mapping
    secret_names: list[str] = []
    try:
        resp = _requests.get(
            f"{base}/actions/secrets",
            headers=headers, params={"per_page": 100}, timeout=10,
        )
        if resp.status_code == 200:
            for s in resp.json().get("secrets", []):
                secret_names.append(s["name"])
            logger.info(
                "GitHub Actions secrets for %s/%s: %s",
                owner, repo_name, secret_names or "(none)",
            )
    except Exception as e:
        logger.warning("Failed to fetch GitHub Actions secrets: %s", e)

    result["actions_secrets"] = secret_names

    # 6. GitHub Actions repository variables (non-secret env vars)
    repo_variables: list[str] = []
    try:
        resp = _requests.get(
            f"{base}/actions/variables",
            headers=headers, params={"per_page": 100}, timeout=10,
        )
        if resp.status_code == 200:
            for v in resp.json().get("variables", []):
                repo_variables.append(v["name"])
            logger.info(
                "GitHub Actions variables for %s/%s: %s",
                owner, repo_name, repo_variables or "(none)",
            )
    except Exception as e:
        logger.warning("Failed to fetch GitHub Actions variables: %s", e)

    result["actions_variables"] = repo_variables

    # 7. GitHub Environments and their secrets/variables
    environment_vars: dict[str, list[str]] = {}  # env_name → [var_names]
    try:
        resp = _requests.get(
            f"{base}/environments",
            headers=headers, params={"per_page": 30}, timeout=10,
        )
        if resp.status_code == 200:
            for env in resp.json().get("environments", []):
                env_name = env["name"]
                env_var_names: list[str] = []
                # Get environment secrets
                try:
                    sec_resp = _requests.get(
                        f"{base}/environments/{env_name}/secrets",
                        headers=headers, params={"per_page": 100}, timeout=10,
                    )
                    if sec_resp.status_code == 200:
                        for s in sec_resp.json().get("secrets", []):
                            env_var_names.append(s["name"])
                except Exception:
                    pass
                # Get environment variables
                try:
                    var_resp = _requests.get(
                        f"{base}/environments/{env_name}/variables",
                        headers=headers, params={"per_page": 100}, timeout=10,
                    )
                    if var_resp.status_code == 200:
                        for v in var_resp.json().get("variables", []):
                            env_var_names.append(v["name"])
                except Exception:
                    pass
                if env_var_names:
                    environment_vars[env_name] = env_var_names
            if environment_vars:
                logger.info(
                    "GitHub Environments for %s/%s: %s",
                    owner, repo_name,
                    {k: len(v) for k, v in environment_vars.items()},
                )
    except Exception as e:
        logger.warning("Failed to fetch GitHub environments: %s", e)

    result["environment_vars"] = environment_vars

    return result


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
