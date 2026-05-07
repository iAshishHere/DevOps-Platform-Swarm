"""Structured file-based logging for each agent run.

Writes detailed artefacts into the run directory so every decision,
retry, and change is fully auditable.

Directory layout produced
─────────────────────────
outputs/<date>/run<N>/
├── agent1/
│   ├── execution.log          ← full text log stream
│   ├── file_tree.txt          ← discovered repo file tree
│   └── scanners/
│       ├── languages.json
│       ├── frameworks.json
│       ├── docker.json
│       ├── tests.json
│       ├── packages.json
│       ├── installed_packages.json
│       ├── deployment.json
│       ├── cicd.json
│       ├── cloud.json
│       └── architecture.json
├── agent2/
│   ├── execution.log          ← full text log stream
│   ├── platform.txt           ← resolved CI/CD platform
│   ├── phase1/
│   │   ├── generated/         ← initial YAML per pipeline
│   │   │   └── ci.yml
│   │   ├── run_results.json   ← GitHub Actions run outcomes
│   │   ├── attempt1/
│   │   │   ├── error_logs/    ← raw Actions logs per workflow
│   │   │   ├── before/        ← YAML before fix
│   │   │   ├── after/         ← YAML after fix
│   │   │   └── summary.json   ← what changed, identical?, skipped?
│   │   └── attempt2/ …
│   ├── phase2/ …
│   ├── phase3/ …
│   └── pr.json                ← PR URL, final pipeline list
├── agent1-code-analyser.json
└── agent2-pipeline-generator.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunLogger:
    """Writes structured artefacts and a streaming text log for one agent."""

    def __init__(self, run_dir: str | Path, agent_name: str) -> None:
        self.base = Path(run_dir) / agent_name
        self.base.mkdir(parents=True, exist_ok=True)

        # Set up a Python file-handler that captures everything
        self._log_path = self.base / "execution.log"
        self._file_handler = logging.FileHandler(self._log_path, mode="a", encoding="utf-8")
        self._file_handler.setLevel(logging.DEBUG)
        self._file_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
        )

    # ── lifecycle ────────────────────────────────────────────────────────

    def attach(self) -> None:
        """Attach the file handler so it captures agent logs (INFO+) and library warnings."""
        # Capture our own agent messages at DEBUG level
        app_logger = logging.getLogger("devops_guardian")
        app_logger.addHandler(self._file_handler)
        if app_logger.level == logging.NOTSET or app_logger.level > logging.DEBUG:
            app_logger.setLevel(logging.DEBUG)
        # Also capture warnings/errors from libraries via root logger
        logging.getLogger().addHandler(self._file_handler)

    def detach(self) -> None:
        """Remove the file handler from all loggers it was attached to."""
        logging.getLogger("devops_guardian").removeHandler(self._file_handler)
        logging.getLogger().removeHandler(self._file_handler)
        self._file_handler.flush()

    # ── generic writers ──────────────────────────────────────────────────

    def write_json(self, rel_path: str, data: Any) -> Path:
        """Write *data* as pretty-printed JSON to *rel_path* under the agent dir."""
        dest = self.base / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, indent=2, default=str))
        return dest

    def write_text(self, rel_path: str, text: str) -> Path:
        """Write raw text to *rel_path* under the agent dir."""
        dest = self.base / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        return dest

    def log(self, message: str, level: int = logging.INFO) -> None:
        """Write a single message directly into execution.log."""
        record = logging.LogRecord(
            name="RunLogger", level=level, pathname="", lineno=0,
            msg=message, args=(), exc_info=None,
        )
        self._file_handler.emit(record)

    # ── Agent 1 shortcuts ────────────────────────────────────────────────

    def save_file_tree(self, file_paths: list[str]) -> None:
        self.write_text("file_tree.txt", "\n".join(file_paths))

    def save_scanner(self, scanner_name: str, data: Any) -> None:
        self.write_json(f"scanners/{scanner_name}.json", data)

    # ── Agent 2 shortcuts ────────────────────────────────────────────────

    def save_platform(self, platform: str) -> None:
        self.write_text("platform.txt", platform)

    def save_generated_pipeline(self, phase: int, pipeline: dict) -> None:
        filename = Path(pipeline["filename"]).stem + ".yml"
        self.write_text(f"phase{phase}/generated/{filename}", pipeline["content"])

    def save_run_results(self, phase: int, runs: list[dict]) -> None:
        self.write_json(f"phase{phase}/run_results.json", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_runs": len(runs),
            "passed": [r["name"] for r in runs if r.get("conclusion") == "success"],
            "failed": [r["name"] for r in runs if r.get("conclusion") != "success"],
            "details": runs,
        })

    def save_fix_attempt(
        self,
        phase: int,
        attempt: int,
        workflow_name: str,
        filename: str,
        error_log: str,
        yaml_before: str,
        yaml_after: str,
        identical: bool,
    ) -> None:
        stem = Path(filename).stem
        attempt_dir = f"phase{phase}/attempt{attempt}"
        self.write_text(f"{attempt_dir}/error_logs/{stem}.log", error_log)
        self.write_text(f"{attempt_dir}/before/{stem}.yml", yaml_before)
        self.write_text(f"{attempt_dir}/after/{stem}.yml", yaml_after)

        # Build a per-file summary
        summary_path = self.base / attempt_dir / "summary.json"
        existing: list = []
        if summary_path.exists():
            existing = json.loads(summary_path.read_text())
        existing.append({
            "workflow": workflow_name,
            "filename": filename,
            "identical_yaml": identical,
            "changed": not identical,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        summary_path.write_text(json.dumps(existing, indent=2))

    def save_fix_skip(
        self,
        phase: int,
        attempt: int,
        workflow_name: str,
        reason: str,
    ) -> None:
        attempt_dir = f"phase{phase}/attempt{attempt}"
        summary_path = self.base / attempt_dir / "summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        existing: list = []
        if summary_path.exists():
            existing = json.loads(summary_path.read_text())
        existing.append({
            "workflow": workflow_name,
            "skipped": True,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        summary_path.write_text(json.dumps(existing, indent=2))

    def save_phase_outcome(self, phase: int, passed: bool, attempts_used: int) -> None:
        self.write_json(f"phase{phase}/outcome.json", {
            "phase": phase,
            "passed": passed,
            "fix_attempts_used": attempts_used,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def save_pr(self, pr_url: str, pipelines: list[dict]) -> None:
        self.write_json("pr.json", {
            "pull_request_url": pr_url,
            "pipelines": [
                {"filename": p["filename"], "description": p["description"]}
                for p in pipelines
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
