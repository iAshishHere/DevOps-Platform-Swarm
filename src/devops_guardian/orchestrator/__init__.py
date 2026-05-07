"""Master orchestrator – chains agents together.

Currently only runs Agent 1 (code-analyser).
Future agents will be added as nodes here.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from devops_guardian.agents.code_analyser.graph import run_analysis
from devops_guardian.agents.pipeline_generator.graph import run_pipeline_generator
from devops_guardian.models.analysis import RepoAnalysis


class GuardianState(TypedDict):
    repo_url: str
    analysis: dict[str, Any]
    pipeline_config: dict[str, Any]
    run_dir: str  # path to the run output directory
    # Future: security_report, etc.


def analyse_node(state: GuardianState) -> dict:
    result = run_analysis(state["repo_url"], run_dir=state.get("run_dir", ""))
    return {"analysis": result.model_dump()}


def pipeline_node(state: GuardianState) -> dict:
    analysis = RepoAnalysis(**state["analysis"])
    result = run_pipeline_generator(analysis, run_dir=state.get("run_dir", ""))
    return {"pipeline_config": result.model_dump()}


# Future agent nodes will follow this same pattern:
# def security_node(state: GuardianState) -> dict:
#     result = scan_security(state["analysis"])
#     return {"security_report": result.model_dump()}


def build_orchestrator() -> StateGraph:
    graph = StateGraph(GuardianState)
    graph.add_node("analyse", analyse_node)
    graph.add_node("generate_pipeline", pipeline_node)
    # graph.add_node("scan_security", security_node)

    graph.set_entry_point("analyse")
    graph.add_edge("analyse", "generate_pipeline")
    graph.add_edge("generate_pipeline", END)
    # Future edges:
    # graph.add_edge("analyse", "scan_security")

    return graph


def run_guardian(repo_url: str, run_dir: str = "") -> dict[str, Any]:
    """Run the full DevOps Guardian pipeline."""
    graph = build_orchestrator()
    app = graph.compile()
    return app.invoke({
        "repo_url": repo_url,
        "analysis": {},
        "pipeline_config": {},
        "run_dir": run_dir,
    })
