import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def clone_repo(repo_url: str, dest: str | None = None, shallow: bool = True) -> str:
    """Clone a GitHub repository and return the local path.

    Args:
        repo_url: HTTPS URL of the repo.
        dest: Target directory. If None, a temp directory is created.
        shallow: Use --depth=1 for faster clones.

    Returns:
        Absolute path to the cloned repository.
    """
    if dest is None:
        dest = os.path.join(tempfile.mkdtemp(prefix="devops_guardian_"), "repo")

    cmd = ["git", "clone"]
    if shallow:
        cmd += ["--depth", "1"]
    cmd += [repo_url, dest]

    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return dest


def cleanup_repo(clone_path: str) -> None:
    """Remove a previously cloned repository."""
    if os.path.isdir(clone_path):
        shutil.rmtree(clone_path)


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
