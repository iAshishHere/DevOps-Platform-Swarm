"""FastAPI REST API for DevOps Guardian."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from sse_starlette.sse import EventSourceResponse

from devops_guardian.agents.code_analyser.graph import run_analysis
from devops_guardian.agents.pipeline_generator.graph import run_pipeline_generator
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.models.pipeline import (
    AvailableOptions,
    PipelineConfig,
    PipelineResult,
    available_options_from_analysis,
)
from devops_guardian.utils.job_manager import Job, JobStatus, create_job, get_job

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

app = FastAPI(
    title="DevOps Guardian API",
    version="0.1.0",
    description="Multi-agent DevOps analysis platform",
)

# CORS — allow your frontend to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response schemas ───────────────────────────────────────────────


class AnalyseRequest(BaseModel):
    repo_url: HttpUrl
    branch: str = ""  # optional — defaults to DEFAULT_BRANCH env var


class GeneratePipelinesRequest(BaseModel):
    analysis: RepoAnalysis
    config: PipelineConfig = PipelineConfig()


# ── Background runner ────────────────────────────────────────────────────────


_OUTPUT_ROOT = Path("outputs")


def _get_run_dir() -> Path:
    """Return the next run directory: outputs/<date>/run<N>."""
    date_str = datetime.now().strftime("%d%b%Y").lstrip("0")  # e.g. 7May2026
    date_dir = _OUTPUT_ROOT / date_str
    date_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        (d for d in date_dir.iterdir() if d.is_dir() and d.name.startswith("run")),
        key=lambda d: int(d.name[3:]) if d.name[3:].isdigit() else 0,
    )
    next_num = int(existing[-1].name[3:]) + 1 if existing else 1
    run_dir = date_dir / f"run{next_num}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _run_pipeline_job(job: Job, analysis: RepoAnalysis, config: PipelineConfig) -> None:
    """Execute pipeline generation in a background thread."""
    job.status = JobStatus.RUNNING
    try:
        run_dir = _get_run_dir()

        # Save Agent 1 analysis so the output folder is self-contained
        a1_path = run_dir / "agent1-code-analyser.json"
        a1_path.write_text(json.dumps(analysis.model_dump(), indent=2))

        result = run_pipeline_generator(
            analysis, str(run_dir), config, progress_callback=job.emit,
        )
        job.finish(result.model_dump())
    except Exception as e:
        job.fail(str(e))


# ── Routes ───────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/analyse", response_model=RepoAnalysis)
async def analyse_repo(request: AnalyseRequest):
    """Run the Code Analyser agent on a GitHub repository."""
    try:
        result = await asyncio.to_thread(
            run_analysis, str(request.repo_url), "", request.branch,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/available-options", response_model=AvailableOptions)
async def get_available_options(analysis: RepoAnalysis):
    """Derive the set of pipeline options the UI can show to the user.

    Call this after ``/api/analyse`` to find out which pipeline types
    and test categories are available based on Agent 1's discovery.
    """
    return available_options_from_analysis(analysis)


@app.post("/api/generate-pipelines")
async def generate_pipelines(request: GeneratePipelinesRequest):
    """Start pipeline generation as a background job.

    Returns a ``job_id`` immediately.  Use ``GET /api/jobs/{job_id}``
    to poll status or ``GET /api/jobs/{job_id}/stream`` for live SSE.
    """
    job = create_job()
    asyncio.get_event_loop().run_in_executor(
        None, _run_pipeline_job, job, request.analysis, request.config,
    )
    return {"job_id": job.job_id}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Poll the status and events of a pipeline generation job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "events": [
            {"step": e.step, "message": e.message, "timestamp": e.timestamp, "detail": e.detail}
            for e in job.events
        ],
        "result": job.result,
        "error": job.error,
    }


@app.get("/api/jobs/{job_id}/stream")
async def stream_job_events(job_id: str):
    """Stream live progress events via Server-Sent Events (SSE)."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        # First, replay any events that already happened
        sent = 0
        for evt in job.events:
            yield {
                "event": evt.step,
                "data": json.dumps({
                    "step": evt.step,
                    "message": evt.message,
                    "timestamp": evt.timestamp,
                    "detail": evt.detail,
                }),
            }
            sent += 1

        # Then stream new events as they arrive
        while True:
            evt = await job._queue.get()
            if evt is None:
                # Job finished — send final status
                yield {
                    "event": "complete",
                    "data": json.dumps({
                        "status": job.status.value,
                        "result": job.result,
                        "error": job.error,
                    }),
                }
                break
            yield {
                "event": evt.step,
                "data": json.dumps({
                    "step": evt.step,
                    "message": evt.message,
                    "timestamp": evt.timestamp,
                    "detail": evt.detail,
                }),
            }

    return EventSourceResponse(event_generator())
