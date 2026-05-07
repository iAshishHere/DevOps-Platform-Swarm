"""FastAPI REST API for DevOps Guardian."""

import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

from devops_guardian.agents.code_analyser.graph import run_analysis
from devops_guardian.agents.pipeline_generator.graph import run_pipeline_generator
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.models.pipeline import PipelineResult

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


# ── Routes ───────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/analyse", response_model=RepoAnalysis)
async def analyse_repo(request: AnalyseRequest):
    """Run the Code Analyser agent on a GitHub repository."""
    try:
        result = await asyncio.to_thread(run_analysis, str(request.repo_url))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-pipelines", response_model=PipelineResult)
async def generate_pipelines(request: AnalyseRequest):
    """Run Agent 1 + Agent 2: analyse then generate CI/CD pipelines."""
    try:
        analysis = await asyncio.to_thread(run_analysis, str(request.repo_url))
        result = await asyncio.to_thread(run_pipeline_generator, analysis)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
