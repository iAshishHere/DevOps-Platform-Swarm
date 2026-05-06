"""Detect Docker usage in the repository."""

from __future__ import annotations

import re
from pathlib import Path

from devops_guardian.models.analysis import DockerInfo

FROM_PATTERN = re.compile(r"^FROM\s+(\S+)", re.MULTILINE | re.IGNORECASE)


def detect_docker(repo_root: str, file_paths: list[str]) -> DockerInfo:
    """Scan for Dockerfiles, docker-compose, and extract base images."""
    root = Path(repo_root)

    dockerfiles = [
        fp for fp in file_paths if Path(fp).name.lower().startswith("dockerfile")
    ]

    has_compose = any(
        "docker-compose" in Path(fp).name.lower() or "compose.yaml" == Path(fp).name.lower() or "compose.yml" == Path(fp).name.lower()
        for fp in file_paths
    )

    base_images: set[str] = set()
    for df in dockerfiles:
        full_path = root / df
        if full_path.exists():
            content = full_path.read_text(errors="ignore")
            for match in FROM_PATTERN.finditer(content):
                image = match.group(1)
                if image.lower() != "scratch":
                    base_images.add(image)

    return DockerInfo(
        has_dockerfile=len(dockerfiles) > 0,
        has_compose=has_compose,
        dockerfiles=dockerfiles,
        base_images=sorted(base_images),
    )
