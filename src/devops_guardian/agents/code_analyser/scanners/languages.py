"""Detect programming languages by file extension counting."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from devops_guardian.models.analysis import LanguageInfo

EXTENSION_MAP: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".sh": "Shell",
    ".bash": "Shell",
    ".r": "R",
    ".dart": "Dart",
    ".lua": "Lua",
    ".zig": "Zig",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".pl": "Perl",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

IGNORE_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".lock", ".sum", ".mod", ".cfg", ".ini", ".xml"}


def detect_languages(file_paths: list[str]) -> list[LanguageInfo]:
    """Return detected languages sorted by file count descending."""
    counter: Counter[str] = Counter()

    for fp in file_paths:
        ext = Path(fp).suffix.lower()
        if ext in IGNORE_EXTENSIONS:
            continue
        lang = EXTENSION_MAP.get(ext)
        if lang:
            counter[lang] += 1

    total = sum(counter.values()) or 1
    return [
        LanguageInfo(
            name=lang,
            percentage=round(count / total * 100, 1),
            file_count=count,
        )
        for lang, count in counter.most_common()
    ]
