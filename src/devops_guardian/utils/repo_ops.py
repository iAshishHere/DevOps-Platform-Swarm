import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urlunparse


def _inject_token(repo_url: str) -> str:
    """If GITHUB_TOKEN is set, inject it into the HTTPS URL for private repo access."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token or token == "your-github-token":
        return repo_url

    parsed = urlparse(repo_url)
    if parsed.scheme == "https" and not parsed.username:
        authenticated = parsed._replace(netloc=f"x-access-token:{token}@{parsed.hostname}")
        return urlunparse(authenticated)
    return repo_url


def _get_default_branch() -> str:
    """Return the configured default branch (DEFAULT_BRANCH env var, defaults to 'main')."""
    return os.environ.get("DEFAULT_BRANCH", "main")


def clone_repo(repo_url: str, dest: str | None = None, shallow: bool = True, branch: str | None = None) -> str:
    """Clone a GitHub repository and return the local path.

    Args:
        repo_url: HTTPS URL of the repo (supports private repos via GITHUB_TOKEN).
        dest: Target directory. If None, a temp directory is created.
        shallow: Use --depth=1 for faster clones.
        branch: Branch to clone. Defaults to DEFAULT_BRANCH env var (or 'main').

    Returns:
        Absolute path to the cloned repository.
    """
    if dest is None:
        dest = os.path.join(tempfile.mkdtemp(prefix="devops_guardian_"), "repo")

    authenticated_url = _inject_token(repo_url)
    target_branch = branch or _get_default_branch()

    cmd = ["git", "clone", "--branch", target_branch]
    if shallow:
        cmd += ["--depth", "1"]
    cmd += [authenticated_url, dest]

    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return dest


def cleanup_repo(clone_path: str) -> None:
    """Remove a previously cloned repository."""
    if os.path.isdir(clone_path):
        shutil.rmtree(clone_path)


def create_branch(clone_path: str, branch_name: str) -> None:
    """Create and checkout a new branch in the cloned repo."""
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=clone_path, check=True, capture_output=True, text=True,
    )


def commit_and_push(clone_path: str, branch_name: str, message: str) -> bool:
    """Stage all changes, commit, and push the branch to origin.

    Returns True if changes were committed and pushed, False if nothing to commit.
    """
    def run(cmd: list[str], allow_empty: bool = False) -> subprocess.CompletedProcess:
        result = subprocess.run(
            cmd, cwd=clone_path, capture_output=True, text=True,
        )
        if result.returncode != 0 and not allow_empty:
            # Sanitize token from stderr before raising
            stderr = re.sub(r"x-access-token:[^@]+@", "x-access-token:***@", result.stderr)
            raise RuntimeError(f"Git command failed: {cmd[0:3]!r}…\n{stderr}")
        return result

    run(["git", "add", "."])
    commit_result = run(["git", "commit", "-m", message], allow_empty=True)
    if commit_result.returncode != 0:
        # Nothing to commit — skip push
        if "nothing to commit" in (commit_result.stdout + commit_result.stderr):
            return False
        stderr = re.sub(r"x-access-token:[^@]+@", "x-access-token:***@", commit_result.stderr)
        raise RuntimeError(f"Git commit failed:\n{stderr}")
    run(["git", "push", "origin", branch_name])
    return True


def build_file_tree(root: str, max_depth: int = 4) -> list[str]:
    """Walk the repo and return a list of relative file paths.

    Skips hidden directories, node_modules, __pycache__, .git, and vendor.
    """
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "vendor", "dist"}
    root_path = Path(root)
    paths: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        # prune skipped directories in-place
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]

        rel_dir = Path(dirpath).relative_to(root_path)
        if len(rel_dir.parts) >= max_depth:
            dirnames.clear()
            continue

        for fname in filenames:
            paths.append(str(rel_dir / fname))

    return sorted(paths)
