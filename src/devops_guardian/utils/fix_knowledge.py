"""Persistent fix knowledge across pipeline-generator runs.

Stores successful fix diffs per repository so subsequent runs start
with institutional memory of what changes were needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fix_knowledge"


def _repo_key(repo_url: str) -> str:
    """Convert a repo URL to a safe filename key."""
    match = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", repo_url)
    if not match:
        return "unknown"
    return f"{match.group(1)}_{match.group(2)}"


def load_fix_knowledge(repo_url: str) -> list[dict]:
    """Load previously saved fix knowledge for a repository."""
    path = _KNOWLEDGE_DIR / f"{_repo_key(repo_url)}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_fix_knowledge(
    repo_url: str,
    fix_history: dict[str, list[dict]],
) -> None:
    """Persist applied fixes from the current run for future reference.

    Only saves entries that produced an actual YAML change (have ``diff_summary``).
    Merges with existing knowledge, deduplicating by diff content.
    Keeps at most 50 entries per repository.
    """
    new_entries: list[dict] = []
    for filename, attempts in fix_history.items():
        for attempt in attempts:
            diff = attempt.get("diff_summary")
            if diff and attempt.get("yaml_after"):
                new_entries.append({
                    "filename": filename,
                    "error_summary": attempt["error_log"][:500],
                    "diff_summary": diff,
                })

    if not new_entries:
        return

    _KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_fix_knowledge(repo_url)
    existing_diffs = {e.get("diff_summary") for e in existing}
    for entry in new_entries:
        if entry["diff_summary"] not in existing_diffs:
            existing.append(entry)
            existing_diffs.add(entry["diff_summary"])

    # Cap at 50 entries, keep most recent
    existing = existing[-50:]
    path = _KNOWLEDGE_DIR / f"{_repo_key(repo_url)}.json"
    path.write_text(json.dumps(existing, indent=2))
