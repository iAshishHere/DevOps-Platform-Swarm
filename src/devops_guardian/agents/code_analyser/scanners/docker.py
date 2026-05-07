"""Detect Docker usage in the repository."""

from __future__ import annotations

import re
from pathlib import Path

from devops_guardian.models.analysis import DockerInfo

FROM_PATTERN = re.compile(r"^FROM\s+(\S+)", re.MULTILINE | re.IGNORECASE)
EXPOSE_PATTERN = re.compile(r"^EXPOSE\s+(.+)", re.MULTILINE | re.IGNORECASE)

_COMPOSE_NAMES = {
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
}


def detect_docker(repo_root: str, file_paths: list[str]) -> DockerInfo:
    """Scan for Dockerfiles, docker-compose, and extract base images & exposed ports."""
    root = Path(repo_root)

    dockerfiles = [
        fp for fp in file_paths if Path(fp).name.lower().startswith("dockerfile")
    ]

    compose_files = [
        fp for fp in file_paths if Path(fp).name.lower() in _COMPOSE_NAMES
    ]

    base_images: set[str] = set()
    exposed_ports: set[str] = set()

    for df in dockerfiles:
        full_path = root / df
        if full_path.exists():
            content = full_path.read_text(errors="ignore")
            for match in FROM_PATTERN.finditer(content):
                image = match.group(1)
                if image.lower() != "scratch":
                    base_images.add(image)
            for match in EXPOSE_PATTERN.finditer(content):
                for port in match.group(1).split():
                    port = port.strip().split("/")[0]  # strip protocol (e.g. 8080/tcp)
                    if port:
                        exposed_ports.add(port)

    return DockerInfo(
        has_dockerfile=len(dockerfiles) > 0,
        has_compose=len(compose_files) > 0,
        dockerfiles=dockerfiles,
        compose_files=compose_files,
        base_images=sorted(base_images),
        exposed_ports=sorted(exposed_ports),
    )
