"""Master orchestrator – chains agents together.

Currently only runs Agent 1 (code-analyser).
Future agents will be added as nodes here.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from devops_guardian.agents.code_analyser.graph import run_analysis


class GuardianState(TypedDict):
    repo_url: str
    analysis: dict[str, Any]
    # Future: pipeline_config, security_report, etc.


def analyse_node(state: GuardianState) -> dict:
    result = run_analysis(state["repo_url"])
    return {"analysis": result.model_dump()}


# Future agent nodes will follow this same pattern:
# def pipeline_node(state: GuardianState) -> dict:
#     result = generate_pipeline(state["analysis"])
#     return {"pipeline_config": result.model_dump()}


def build_orchestrator() -> StateGraph:
    graph = StateGraph(GuardianState)
    graph.add_node("analyse", analyse_node)
    # graph.add_node("generate_pipeline", pipeline_node)
    # graph.add_node("scan_security", security_node)

    graph.set_entry_point("analyse")
    graph.add_edge("analyse", END)
    # Future edges:
    # graph.add_edge("analyse", "generate_pipeline")
    # graph.add_edge("analyse", "scan_security")

    return graph


def run_guardian(repo_url: str) -> dict[str, Any]:
    """Run the full DevOps Guardian pipeline."""
    graph = build_orchestrator()
    app = graph.compile()
    return app.invoke({"repo_url": repo_url, "analysis": {}})
