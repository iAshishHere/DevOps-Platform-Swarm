"""Detect required environment variables from source code, config files, and Docker.

Scans:
1. Source code — language-specific regex patterns for env var references.
2. .env / .env.example files.
3. Dockerfiles and docker-compose files.

After collecting all vars, checks availability in:
- GitHub Actions secrets
- GitHub repository environment variables
- Current server/process environment
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Patterns per language ────────────────────────────────────────────────────
# Each pattern captures the env var name in group 1.

# --- Python ---------------------------------------------------------------

# ALL references (required + optional)
_PYTHON_PATTERNS = [
    # os.environ['KEY'] or os.environ["KEY"]  — required (KeyError)
    re.compile(r"""os\.environ\[['"]([A-Z_][A-Z0-9_]*)['"]"""),
    # os.getenv('KEY') — no default → optional (returns None)
    re.compile(r"""os\.getenv\(\s*['"]([A-Z_][A-Z0-9_]*)['"]\s*\)"""),
    # os.getenv('KEY', ...) — has default → optional
    re.compile(r"""os\.getenv\(\s*['"]([A-Z_][A-Z0-9_]*)['"]\s*,"""),
    # os.environ.get('KEY') — no default → optional
    re.compile(r"""os\.environ\.get\(\s*['"]([A-Z_][A-Z0-9_]*)['"]\s*\)"""),
    # os.environ.get('KEY', ...) — has default → optional
    re.compile(r"""os\.environ\.get\(\s*['"]([A-Z_][A-Z0-9_]*)['"]\s*,"""),
    # django-environ: env('KEY') or env.str('KEY') / env.bool('KEY') / env.int('KEY') etc.
    re.compile(r"""env(?:\.(?:str|bool|int|float|list|dict|url|db_url|path))?\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
    # python-decouple: config('KEY')
    re.compile(r"""config\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
]

# REQUIRED only (crash without it)
_PYTHON_REQUIRED_PATTERNS = [
    # os.environ['KEY'] — always required (KeyError on missing)
    re.compile(r"""os\.environ\[['"]([A-Z_][A-Z0-9_]*)['"]"""),
    # env('KEY') without default= → required by django-environ
    re.compile(r"""env(?:\.(?:str|bool|int|float|list|dict|url|db_url|path))?\(\s*['"]([A-Z_][A-Z0-9_]*)['"]\s*\)"""),
    # config('KEY') without default= → required by python-decouple
    re.compile(r"""config\(\s*['"]([A-Z_][A-Z0-9_]*)['"]\s*\)"""),
]

# --- JavaScript / TypeScript -----------------------------------------------

_JS_TS_PATTERNS = [
    # process.env.KEY (captures KEY)
    re.compile(r"""process\.env\.([A-Z_][A-Z0-9_]*)"""),
    # process.env['KEY'] or process.env["KEY"]
    re.compile(r"""process\.env\[['"]([A-Z_][A-Z0-9_]*)['"]"""),
    # import.meta.env.VITE_KEY (Vite)
    re.compile(r"""import\.meta\.env\.([A-Z_][A-Z0-9_]*)"""),
    # Deno.env.get("KEY")
    re.compile(r"""Deno\.env\.get\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
]

# --- Java / Kotlin ---------------------------------------------------------

_JAVA_PATTERNS = [
    # System.getenv("KEY")
    re.compile(r"""System\.getenv\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)"""),
    # @Value("${KEY}") (Spring)
    re.compile(r"""@Value\(\s*["']\$\{([A-Z_][A-Z0-9_]*)"""),
]

# --- Go -------------------------------------------------------------------

_GO_PATTERNS = [
    # os.Getenv("KEY")
    re.compile(r"""os\.Getenv\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)"""),
    # os.LookupEnv("KEY")
    re.compile(r"""os\.LookupEnv\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)"""),
]

# --- Ruby -----------------------------------------------------------------

_RUBY_PATTERNS = [
    # ENV['KEY'] or ENV["KEY"] or ENV.fetch("KEY")
    re.compile(r"""ENV\[['"]([A-Z_][A-Z0-9_]*)['"]"""),
    re.compile(r"""ENV\.fetch\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
]

# --- .NET / C# ------------------------------------------------------------

_DOTNET_PATTERNS = [
    # Environment.GetEnvironmentVariable("KEY")
    re.compile(r"""GetEnvironmentVariable\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)"""),
]

# --- PHP ------------------------------------------------------------------

_PHP_PATTERNS = [
    # getenv('KEY') or $_ENV['KEY'] or $_SERVER['KEY']
    re.compile(r"""getenv\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
    re.compile(r"""\$_ENV\[['"]([A-Z_][A-Z0-9_]*)['"]"""),
    re.compile(r"""\$_SERVER\[['"]([A-Z_][A-Z0-9_]*)['"]"""),
]

# --- Rust -----------------------------------------------------------------

_RUST_PATTERNS = [
    # std::env::var("KEY") or env::var("KEY")
    re.compile(r"""env::var\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)"""),
    # env!("KEY")
    re.compile(r"""env!\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)"""),
]

# Language extension → (all patterns, required-only patterns)
_LANG_PATTERNS: dict[str, tuple[list[re.Pattern], list[re.Pattern]]] = {
    ".py":   (_PYTHON_PATTERNS,  _PYTHON_REQUIRED_PATTERNS),
    ".js":   (_JS_TS_PATTERNS,   _JS_TS_PATTERNS),
    ".ts":   (_JS_TS_PATTERNS,   _JS_TS_PATTERNS),
    ".tsx":  (_JS_TS_PATTERNS,   _JS_TS_PATTERNS),
    ".jsx":  (_JS_TS_PATTERNS,   _JS_TS_PATTERNS),
    ".mjs":  (_JS_TS_PATTERNS,   _JS_TS_PATTERNS),
    ".cjs":  (_JS_TS_PATTERNS,   _JS_TS_PATTERNS),
    ".java": (_JAVA_PATTERNS,    _JAVA_PATTERNS),
    ".kt":   (_JAVA_PATTERNS,    _JAVA_PATTERNS),
    ".go":   (_GO_PATTERNS,      _GO_PATTERNS),
    ".rb":   (_RUBY_PATTERNS,    _RUBY_PATTERNS),
    ".cs":   (_DOTNET_PATTERNS,  _DOTNET_PATTERNS),
    ".php":  (_PHP_PATTERNS,     _PHP_PATTERNS),
    ".rs":   (_RUST_PATTERNS,    _RUST_PATTERNS),
}

# ── .env file patterns ──────────────────────────────────────────────────────
# Matches: KEY=value, KEY="value", KEY='value', KEY= (empty)
_DOTENV_PATTERN = re.compile(r"""^([A-Z_][A-Z0-9_]*)\s*=""", re.MULTILINE)

# Filenames considered as .env specification files
_DOTENV_FILES = frozenset({
    ".env", ".env.example", ".env.sample", ".env.template",
    ".env.local", ".env.development", ".env.production", ".env.test",
    ".env.defaults",
})

# ── Docker patterns ─────────────────────────────────────────────────────────
# Dockerfile: ENV KEY=value or ARG KEY or ARG KEY=default
_DOCKERFILE_ENV = re.compile(r"""^ENV\s+([A-Z_][A-Z0-9_]*)""", re.MULTILINE)
_DOCKERFILE_ARG = re.compile(r"""^ARG\s+([A-Z_][A-Z0-9_]*)""", re.MULTILINE)

# docker-compose: environment: - KEY=value or KEY (no value = required from host)
_COMPOSE_ENV = re.compile(r"""^\s*-\s*([A-Z_][A-Z0-9_]*)\s*(?:=|$)""", re.MULTILINE)

# Env vars that are set by the runtime/CI and should NOT be reported.
_SYSTEM_ENV_VARS = frozenset({
    "PATH", "HOME", "USER", "SHELL", "LANG", "TERM", "PWD", "HOSTNAME",
    "GITHUB_TOKEN", "GITHUB_SHA", "GITHUB_REF", "GITHUB_WORKSPACE",
    "GITHUB_ACTIONS", "GITHUB_ACTOR", "GITHUB_REPOSITORY", "GITHUB_EVENT_NAME",
    "GITHUB_RUN_ID", "GITHUB_RUN_NUMBER", "GITHUB_ENV", "GITHUB_OUTPUT",
    "GITHUB_STEP_SUMMARY", "CI", "RUNNER_OS", "RUNNER_TEMP", "RUNNER_TOOL_CACHE",
    "NODE_ENV", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
    "VIRTUAL_ENV", "CONDA_DEFAULT_ENV",
})


def _scan_dotenv_files(
    repo_root: str, file_paths: list[str],
    all_vars: dict[str, set[str]], required: dict[str, set[str]],
) -> None:
    """Scan .env / .env.example files for declared env var names."""
    for rel_path in file_paths:
        name = Path(rel_path).name.lower()
        if name not in _DOTENV_FILES:
            continue
        full_path = Path(repo_root) / rel_path
        try:
            content = full_path.read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _DOTENV_PATTERN.finditer(content):
            var_name = m.group(1)
            if var_name not in _SYSTEM_ENV_VARS:
                all_vars.setdefault(var_name, set()).add(rel_path)
                # .env.example vars are effectively required — the file
                # exists specifically to document what the app needs.
                if "example" in name or "sample" in name or "template" in name:
                    required.setdefault(var_name, set()).add(rel_path)


def _scan_docker_files(
    repo_root: str, file_paths: list[str],
    all_vars: dict[str, set[str]],
) -> None:
    """Scan Dockerfiles and docker-compose files for env var references."""
    for rel_path in file_paths:
        name = Path(rel_path).name.lower()
        full_path = Path(repo_root) / rel_path

        is_dockerfile = name.startswith("dockerfile") or name == "containerfile"
        is_compose = "compose" in name and (name.endswith(".yml") or name.endswith(".yaml"))

        if not is_dockerfile and not is_compose:
            continue

        try:
            content = full_path.read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        patterns = []
        if is_dockerfile:
            patterns = [_DOCKERFILE_ENV, _DOCKERFILE_ARG]
        elif is_compose:
            patterns = [_COMPOSE_ENV]

        for pat in patterns:
            for m in pat.finditer(content):
                var_name = m.group(1)
                if var_name not in _SYSTEM_ENV_VARS:
                    all_vars.setdefault(var_name, set()).add(rel_path)


def detect_env_vars(
    repo_root: str,
    file_paths: list[str],
    languages: list[str] | None = None,
    frameworks: list[str] | None = None,
) -> dict:
    """Detect env vars the codebase expects from outside.

    Steps:
    1. Regex scan of source code (language-specific patterns).
    2. Scan .env / .env.example files.
    3. Scan Dockerfiles and docker-compose files.

    Returns:
        {
            "required": [{"name": "SECRET_KEY", "files": [...]}],
            "optional": [{"name": "DEBUG", "files": [...]}],
            "total_env_var_count": 5,
        }
    """
    required: dict[str, set[str]] = {}   # name → set of files
    all_vars: dict[str, set[str]] = {}   # name → set of files

    # ── 1. Regex scan of source code files ──────────────────────────────
    for rel_path in file_paths:
        ext = Path(rel_path).suffix.lower()
        if ext not in _LANG_PATTERNS:
            continue

        all_pats, req_pats = _LANG_PATTERNS[ext]
        full_path = Path(repo_root) / rel_path

        try:
            content = full_path.read_text(errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        # Skip test files for required detection — but still scan them
        # for all_vars so we know what vars tests need
        rel_lower = rel_path.lower()
        is_test = any(part in rel_lower for part in (
            "test_", "_test.", "tests/", "test/", "spec/",
        ))

        for pat in all_pats:
            for m in pat.finditer(content):
                var_name = m.group(1)
                if var_name not in _SYSTEM_ENV_VARS:
                    all_vars.setdefault(var_name, set()).add(rel_path)

        if not is_test:
            for pat in req_pats:
                for m in pat.finditer(content):
                    var_name = m.group(1)
                    if var_name not in _SYSTEM_ENV_VARS:
                        required.setdefault(var_name, set()).add(rel_path)

    # ── 2. Scan .env / .env.example files ───────────────────────────────
    _scan_dotenv_files(repo_root, file_paths, all_vars, required)

    # ── 3. Scan Dockerfiles and docker-compose files ────────────────────
    _scan_docker_files(repo_root, file_paths, all_vars)

    # ── 4. Build output ─────────────────────────────────────────────────
    result_required = []
    for name, files in sorted(required.items()):
        result_required.append({"name": name, "files": sorted(files)})

    result_optional = []
    for name, files in sorted(all_vars.items()):
        if name in required:
            continue
        result_optional.append({"name": name, "files": sorted(files)})

    total = len(result_required) + len(result_optional)

    logger.info(
        "Env vars scan complete: %d total (%d required, %d optional)",
        total, len(result_required), len(result_optional),
    )

    return {
        "required": result_required,
        "optional": result_optional,
        "total_env_var_count": total,
    }
