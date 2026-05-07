"""Parse dependency manifests to extract installed package names and versions."""

from __future__ import annotations

import json
import re
from pathlib import Path

from devops_guardian.models.analysis import InstalledPackage, InstalledPackages

# ── Python ───────────────────────────────────────────────────────────────────

_PIP_LINE = re.compile(
    r"^\s*([A-Za-z0-9_][A-Za-z0-9._-]*)\s*([><=!~]{1,3}\s*[\d.*]+(?:\s*,\s*[><=!~]{1,3}\s*[\d.*]+)*)?\s*",
)


def _parse_requirements_txt(path: Path) -> list[InstalledPackage]:
    """Parse a requirements.txt-style file."""
    pkgs: list[InstalledPackage] = []
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _PIP_LINE.match(line)
        if m:
            name = m.group(1)
            version = (m.group(2) or "").strip()
            pkgs.append(InstalledPackage(name=name, version=version))
    return pkgs


def _parse_pyproject_deps(path: Path) -> list[InstalledPackage]:
    """Rough parse of [project.dependencies] from pyproject.toml."""
    pkgs: list[InstalledPackage] = []
    content = path.read_text(errors="ignore")
    in_deps = False
    in_dev = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[project.dependencies]") or stripped.startswith("dependencies"):
            in_deps = True
            in_dev = False
            continue
        if "dev-dependencies" in stripped or "dev" in stripped and "dependencies" in stripped:
            in_deps = True
            in_dev = True
            continue
        if stripped.startswith("[") and in_deps:
            in_deps = False
            in_dev = False
            continue
        if in_deps:
            # Try to parse "package>=version" from quoted strings
            for m in re.finditer(r'"([A-Za-z0-9_][A-Za-z0-9._-]*)\s*([><=!~].*?)?"', stripped):
                pkgs.append(InstalledPackage(name=m.group(1), version=(m.group(2) or "").strip(), dev=in_dev))
    return pkgs


# ── Node.js ──────────────────────────────────────────────────────────────────


def _parse_package_json(path: Path) -> list[InstalledPackage]:
    """Parse package.json dependencies and devDependencies."""
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return []

    pkgs: list[InstalledPackage] = []
    for dep, ver in data.get("dependencies", {}).items():
        pkgs.append(InstalledPackage(name=dep, version=ver, dev=False))
    for dep, ver in data.get("devDependencies", {}).items():
        pkgs.append(InstalledPackage(name=dep, version=ver, dev=True))
    return pkgs


# ── Java (pom.xml — rough) ──────────────────────────────────────────────────

_MVN_DEP = re.compile(
    r"<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>(?:\s*<version>([^<]*)</version>)?",
    re.DOTALL,
)


def _parse_pom_xml(path: Path) -> list[InstalledPackage]:
    content = path.read_text(errors="ignore")
    pkgs: list[InstalledPackage] = []
    for m in _MVN_DEP.finditer(content):
        name = f"{m.group(1)}:{m.group(2)}"
        version = (m.group(3) or "").strip()
        dev = "<scope>test</scope>" in content[m.start():m.start() + 300]
        pkgs.append(InstalledPackage(name=name, version=version, dev=dev))
    return pkgs


# ── Go ───────────────────────────────────────────────────────────────────────


def _parse_go_mod(path: Path) -> list[InstalledPackage]:
    pkgs: list[InstalledPackage] = []
    in_require = False
    for line in path.read_text(errors="ignore").splitlines():
        stripped = line.strip()
        if stripped.startswith("require ("):
            in_require = True
            continue
        if stripped == ")":
            in_require = False
            continue
        if in_require:
            parts = stripped.split()
            if len(parts) >= 2:
                pkgs.append(InstalledPackage(name=parts[0], version=parts[1]))
        elif stripped.startswith("require "):
            parts = stripped.split()
            if len(parts) >= 3:
                pkgs.append(InstalledPackage(name=parts[1], version=parts[2]))
    return pkgs


# ── Gemfile (Ruby) ───────────────────────────────────────────────────────────

_GEM_LINE = re.compile(r"""gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?""")


def _parse_gemfile(path: Path) -> list[InstalledPackage]:
    pkgs: list[InstalledPackage] = []
    in_dev = False
    for line in path.read_text(errors="ignore").splitlines():
        stripped = line.strip()
        if "group :development" in stripped or "group :test" in stripped:
            in_dev = True
        if stripped == "end":
            in_dev = False
        m = _GEM_LINE.search(stripped)
        if m:
            pkgs.append(InstalledPackage(name=m.group(1), version=(m.group(2) or ""), dev=in_dev))
    return pkgs


# ── Cargo.toml (Rust) ───────────────────────────────────────────────────────

_CARGO_DEP = re.compile(r'^(\S+)\s*=\s*"([^"]+)"', re.MULTILINE)


def _parse_cargo_toml(path: Path) -> list[InstalledPackage]:
    pkgs: list[InstalledPackage] = []
    content = path.read_text(errors="ignore")
    in_deps = False
    in_dev = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "[dependencies]":
            in_deps = True
            in_dev = False
            continue
        if stripped == "[dev-dependencies]":
            in_deps = True
            in_dev = True
            continue
        if stripped.startswith("["):
            in_deps = False
            continue
        if in_deps:
            m = _CARGO_DEP.match(stripped)
            if m:
                pkgs.append(InstalledPackage(name=m.group(1), version=m.group(2), dev=in_dev))
    return pkgs


# ── composer.json (PHP) ─────────────────────────────────────────────────────


def _parse_composer_json(path: Path) -> list[InstalledPackage]:
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return []

    pkgs: list[InstalledPackage] = []
    for dep, ver in data.get("require", {}).items():
        if dep == "php":
            continue
        pkgs.append(InstalledPackage(name=dep, version=ver, dev=False))
    for dep, ver in data.get("require-dev", {}).items():
        pkgs.append(InstalledPackage(name=dep, version=ver, dev=True))
    return pkgs


# ── .csproj (.NET) ──────────────────────────────────────────────────────────

_NUGET_REF = re.compile(
    r'<PackageReference\s+Include="([^"]+)"(?:\s+Version="([^"]*)")?',
    re.IGNORECASE,
)


def _parse_csproj(path: Path) -> list[InstalledPackage]:
    content = path.read_text(errors="ignore")
    return [
        InstalledPackage(name=m.group(1), version=(m.group(2) or ""))
        for m in _NUGET_REF.finditer(content)
    ]


# ── Public API ───────────────────────────────────────────────────────────────


def detect_installed_packages(repo_root: str, file_paths: list[str]) -> InstalledPackages:
    """Parse all dependency manifests and return the full package inventory."""
    root = Path(repo_root)
    python: list[InstalledPackage] = []
    npm: list[InstalledPackage] = []
    java: list[InstalledPackage] = []
    go: list[InstalledPackage] = []
    ruby: list[InstalledPackage] = []
    rust: list[InstalledPackage] = []
    dotnet: list[InstalledPackage] = []
    php: list[InstalledPackage] = []

    for fp in file_paths:
        name = Path(fp).name
        full = root / fp

        if not full.exists():
            continue

        # Python
        if name.startswith("requirements") and name.endswith(".txt"):
            python.extend(_parse_requirements_txt(full))
        elif name == "pyproject.toml":
            python.extend(_parse_pyproject_deps(full))

        # Node
        elif name == "package.json":
            npm.extend(_parse_package_json(full))

        # Java
        elif name == "pom.xml":
            java.extend(_parse_pom_xml(full))

        # Go
        elif name == "go.mod":
            go.extend(_parse_go_mod(full))

        # Ruby
        elif name == "Gemfile":
            ruby.extend(_parse_gemfile(full))

        # Rust
        elif name == "Cargo.toml":
            rust.extend(_parse_cargo_toml(full))

        # PHP
        elif name == "composer.json":
            php.extend(_parse_composer_json(full))

        # .NET
        elif name.endswith(".csproj"):
            dotnet.extend(_parse_csproj(full))

    # Deduplicate by name within each ecosystem (keep first occurrence)
    def _dedup(pkgs: list[InstalledPackage]) -> list[InstalledPackage]:
        seen: set[str] = set()
        out: list[InstalledPackage] = []
        for p in pkgs:
            if p.name not in seen:
                seen.add(p.name)
                out.append(p)
        return sorted(out, key=lambda p: p.name.lower())

    return InstalledPackages(
        python=_dedup(python),
        npm=_dedup(npm),
        java=_dedup(java),
        go=_dedup(go),
        ruby=_dedup(ruby),
        rust=_dedup(rust),
        dotnet=_dedup(dotnet),
        php=_dedup(php),
    )
