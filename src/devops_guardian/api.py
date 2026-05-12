"""FastAPI REST API for DevOps Guardian."""

import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from devops_guardian.agents.code_analyser.graph import run_analysis
from devops_guardian.agents.pipeline_generator.graph import run_pipeline_generator
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.models.pipeline import (
    AvailableOptions,
    PipelineConfig,
    PipelineResult,
    available_options_from_analysis,
)

load_dotenv()

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


@app.post("/api/generate-pipelines", response_model=PipelineResult)
async def generate_pipelines(request: GeneratePipelinesRequest):
    """Run Agent 2: generate CI/CD pipelines from a prior analysis.

    Accepts the full ``RepoAnalysis`` from Agent 1 plus an optional
    ``PipelineConfig`` with the user's selections.
    """
    try:
        result = await asyncio.to_thread(
            run_pipeline_generator, request.analysis, "", request.config,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
