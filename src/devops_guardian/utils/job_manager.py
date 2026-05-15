"""Job manager with disk-backed persistence so jobs survive server restarts."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_JOBS_DIR = Path(__file__).resolve().parent.parent.parent.parent / ".jobs"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ProgressEvent:
    step: str           # e.g. "clone", "generate", "fix_attempt"
    message: str        # human-readable, e.g. "Generating CI pipeline…"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Job:
    job_id: str
    status: JobStatus = JobStatus.QUEUED
    events: list[ProgressEvent] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    _queue: asyncio.Queue[ProgressEvent | None] = field(
        default_factory=asyncio.Queue, repr=False,
    )

    def emit(self, step: str, message: str, **detail: Any) -> None:
        """Push a progress event (called from worker thread)."""
        evt = ProgressEvent(step=step, message=message, detail=detail)
        self.events.append(evt)
        self._persist()
        # Schedule put on the event loop so it's thread-safe
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, evt)
        except RuntimeError:
            pass  # loop closed — ignore

    def finish(self, result: dict[str, Any]) -> None:
        self.status = JobStatus.COMPLETED
        self.result = result
        self.emit("done", "Pipeline generation complete.")
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        except RuntimeError:
            pass

    def fail(self, error: str) -> None:
        self.status = JobStatus.FAILED
        self.error = error
        self.emit("error", f"Job failed: {error}")
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        except RuntimeError:
            pass

    def _persist(self) -> None:
        """Write job state to disk (best-effort, never raises)."""
        try:
            _JOBS_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "job_id": self.job_id,
                "status": self.status.value,
                "events": [
                    {"step": e.step, "message": e.message,
                     "timestamp": e.timestamp, "detail": e.detail}
                    for e in self.events
                ],
                "result": self.result,
                "error": self.error,
            }
            path = _JOBS_DIR / f"{self.job_id}.json"
            path.write_text(json.dumps(data, default=str))
        except Exception:
            logger.debug("Failed to persist job %s", self.job_id, exc_info=True)

    _loop: asyncio.AbstractEventLoop = field(default=None, repr=False)  # type: ignore[assignment]


# ── Global store ─────────────────────────────────────────────────────────────

_jobs: dict[str, Job] = {}


def create_job() -> Job:
    """Create a new job and register it."""
    job_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_event_loop()
    job = Job(job_id=job_id, _loop=loop)
    _jobs[job_id] = job
    job._persist()
    return job


def _load_from_disk(job_id: str) -> Job | None:
    """Try to restore a job from its JSON file on disk."""
    path = _JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        events = [
            ProgressEvent(
                step=e["step"], message=e["message"],
                timestamp=e.get("timestamp", ""),
                detail=e.get("detail", {}),
            )
            for e in data.get("events", [])
        ]
        loop = asyncio.get_event_loop()
        job = Job(
            job_id=data["job_id"],
            status=JobStatus(data["status"]),
            events=events,
            result=data.get("result"),
            error=data.get("error"),
            _loop=loop,
        )
        _jobs[job_id] = job
        return job
    except Exception:
        logger.debug("Failed to load job %s from disk", job_id, exc_info=True)
        return None


def get_job(job_id: str) -> Job | None:
    job = _jobs.get(job_id)
    if job is not None:
        return job
    # Cache miss — try disk
    return _load_from_disk(job_id)
