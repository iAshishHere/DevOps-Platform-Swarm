"""Detect frameworks from dependency/config files."""

from __future__ import annotations

import json
from pathlib import Path

# Maps: dependency name → framework label
PIP_FRAMEWORKS = {
    "django": "Django",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "starlette": "Starlette",
    "tornado": "Tornado",
    "celery": "Celery",
    "streamlit": "Streamlit",
    "gradio": "Gradio",
}

NPM_FRAMEWORKS = {
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "angular": "Angular",
    "svelte": "Svelte",
    "express": "Express",
    "nestjs": "NestJS",
    "fastify": "Fastify",
    "@nestjs/core": "NestJS",
    "hono": "Hono",
}

JAVA_FRAMEWORKS = {
    "spring-boot": "Spring Boot",
    "spring-boot-starter": "Spring Boot",
    "quarkus": "Quarkus",
    "micronaut": "Micronaut",
}


def _detect_from_requirements(repo_root: str) -> list[str]:
    """Parse requirements.txt, setup.cfg, pyproject.toml for Python frameworks."""
    frameworks: set[str] = set()
    req_files = ["requirements.txt", "requirements/base.txt", "requirements/prod.txt"]

    for rf in req_files:
        path = Path(repo_root) / rf
        if path.exists():
            content = path.read_text(errors="ignore").lower()
            for dep, fw in PIP_FRAMEWORKS.items():
                if dep in content:
                    frameworks.add(fw)

    pyproject = Path(repo_root) / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(errors="ignore").lower()
        for dep, fw in PIP_FRAMEWORKS.items():
            if dep in content:
                frameworks.add(fw)

    return list(frameworks)


def _detect_from_package_json(repo_root: str) -> list[str]:
    """Parse package.json for JS/TS frameworks."""
    frameworks: set[str] = set()
    pkg = Path(repo_root) / "package.json"
    if not pkg.exists():
        return []

    try:
        data = json.loads(pkg.read_text(errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return []

    all_deps = {}
    all_deps.update(data.get("dependencies", {}))
    all_deps.update(data.get("devDependencies", {}))

    for dep, fw in NPM_FRAMEWORKS.items():
        if dep in all_deps:
            frameworks.add(fw)

    return list(frameworks)


def _detect_from_java(repo_root: str) -> list[str]:
    """Check pom.xml / build.gradle for Java frameworks."""
    frameworks: set[str] = set()
    root = Path(repo_root)

    for fname in ["pom.xml", "build.gradle", "build.gradle.kts"]:
        fpath = root / fname
        if fpath.exists():
            content = fpath.read_text(errors="ignore").lower()
            for dep, fw in JAVA_FRAMEWORKS.items():
                if dep in content:
                    frameworks.add(fw)

    return list(frameworks)


def detect_frameworks(repo_root: str) -> list[str]:
    """Return all detected frameworks."""
    results: set[str] = set()
    results.update(_detect_from_requirements(repo_root))
    results.update(_detect_from_package_json(repo_root))
    results.update(_detect_from_java(repo_root))
    return sorted(results)
