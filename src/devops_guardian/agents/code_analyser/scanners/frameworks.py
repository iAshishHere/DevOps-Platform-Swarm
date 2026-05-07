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
    "scrapy": "Scrapy",
    "bottle": "Bottle",
    "sanic": "Sanic",
    "aiohttp": "aiohttp",
    "litestar": "Litestar",
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
    "koa": "Koa",
    "gatsby": "Gatsby",
    "remix": "Remix",
    "@remix-run/node": "Remix",
}

JAVA_FRAMEWORKS = {
    "spring-boot": "Spring Boot",
    "spring-boot-starter": "Spring Boot",
    "quarkus": "Quarkus",
    "micronaut": "Micronaut",
}

# Well-known config / entry-point files that indicate a framework
FRAMEWORK_MARKER_FILES = {
    "manage.py": "Django",
    "settings.py": "Django",
    "wsgi.py": "Django",
    "asgi.py": "Django",
    "next.config.js": "Next.js",
    "next.config.mjs": "Next.js",
    "next.config.ts": "Next.js",
    "nuxt.config.ts": "Nuxt",
    "nuxt.config.js": "Nuxt",
    "angular.json": "Angular",
    "svelte.config.js": "Svelte",
    "gatsby-config.js": "Gatsby",
    "remix.config.js": "Remix",
    "vite.config.ts": "Vite",
    "vite.config.js": "Vite",
    "webpack.config.js": "Webpack",
}


def _find_files(file_paths: list[str], filename: str) -> list[str]:
    """Return all relative paths matching a filename anywhere in the tree."""
    return [fp for fp in file_paths if Path(fp).name == filename]


def _detect_from_requirements(repo_root: str, file_paths: list[str]) -> list[str]:
    """Parse all requirements*.txt, setup.cfg, pyproject.toml for Python frameworks."""
    frameworks: set[str] = set()
    root = Path(repo_root)

    # Find all requirements*.txt files anywhere in the tree
    req_files = [fp for fp in file_paths if Path(fp).name.startswith("requirements") and fp.endswith(".txt")]

    for rf in req_files:
        path = root / rf
        if path.exists():
            content = path.read_text(errors="ignore").lower()
            for dep, fw in PIP_FRAMEWORKS.items():
                if dep in content:
                    frameworks.add(fw)

    # Also check pyproject.toml, setup.cfg, setup.py (anywhere)
    for cfg_name in ["pyproject.toml", "setup.cfg", "setup.py", "Pipfile"]:
        for fp in _find_files(file_paths, cfg_name):
            path = root / fp
            if path.exists():
                content = path.read_text(errors="ignore").lower()
                for dep, fw in PIP_FRAMEWORKS.items():
                    if dep in content:
                        frameworks.add(fw)

    return list(frameworks)


def _detect_from_package_json(repo_root: str, file_paths: list[str]) -> list[str]:
    """Parse all package.json files for JS/TS frameworks."""
    frameworks: set[str] = set()
    root = Path(repo_root)

    for fp in _find_files(file_paths, "package.json"):
        pkg = root / fp
        try:
            data = json.loads(pkg.read_text(errors="ignore"))
        except (json.JSONDecodeError, OSError):
            continue

        all_deps = {}
        all_deps.update(data.get("dependencies", {}))
        all_deps.update(data.get("devDependencies", {}))

        for dep, fw in NPM_FRAMEWORKS.items():
            if dep in all_deps:
                frameworks.add(fw)

    return list(frameworks)


def _detect_from_java(repo_root: str, file_paths: list[str]) -> list[str]:
    """Check pom.xml / build.gradle for Java frameworks."""
    frameworks: set[str] = set()
    root = Path(repo_root)

    for fname in ["pom.xml", "build.gradle", "build.gradle.kts"]:
        for fp in _find_files(file_paths, fname):
            fpath = root / fp
            if fpath.exists():
                content = fpath.read_text(errors="ignore").lower()
                for dep, fw in JAVA_FRAMEWORKS.items():
                    if dep in content:
                        frameworks.add(fw)

    return list(frameworks)


def _detect_from_marker_files(file_paths: list[str]) -> list[str]:
    """Detect frameworks from well-known config / entry-point files."""
    frameworks: set[str] = set()
    file_names = {Path(fp).name for fp in file_paths}

    for marker, fw in FRAMEWORK_MARKER_FILES.items():
        if marker in file_names:
            frameworks.add(fw)

    return list(frameworks)


def detect_frameworks(repo_root: str, file_paths: list[str]) -> list[str]:
    """Return all detected frameworks."""
    results: set[str] = set()
    results.update(_detect_from_requirements(repo_root, file_paths))
    results.update(_detect_from_package_json(repo_root, file_paths))
    results.update(_detect_from_java(repo_root, file_paths))
    results.update(_detect_from_marker_files(file_paths))
    return sorted(results)
