"""LangGraph state graph for the Code Analyser agent."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from devops_guardian.agents.code_analyser.scanners.architecture import detect_architecture
from devops_guardian.agents.code_analyser.scanners.cicd import detect_cicd
from devops_guardian.agents.code_analyser.scanners.cloud import detect_cloud_providers
from devops_guardian.agents.code_analyser.scanners.docker import detect_docker
from devops_guardian.agents.code_analyser.scanners.frameworks import detect_frameworks
from devops_guardian.agents.code_analyser.scanners.languages import detect_languages
from devops_guardian.agents.code_analyser.scanners.packages import detect_package_managers
from devops_guardian.agents.code_analyser.scanners.tests import detect_tests
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.utils.repo_ops import build_file_tree, cleanup_repo, clone_repo


class AnalyserState(TypedDict):
    repo_url: str
    clone_path: str
    file_paths: list[str]
    result: dict[str, Any]


# ── Graph nodes ──────────────────────────────────────────────────────────────


def clone_node(state: AnalyserState) -> dict:
    clone_path = clone_repo(state["repo_url"])
    file_paths = build_file_tree(clone_path)
    return {"clone_path": clone_path, "file_paths": file_paths}


def scan_node(state: AnalyserState) -> dict:
    repo_root = state["clone_path"]
    file_paths = state["file_paths"]

    languages = detect_languages(file_paths)
    frameworks = detect_frameworks(repo_root)
    docker = detect_docker(repo_root, file_paths)
    test_info = detect_tests(repo_root, file_paths)
    cicd = detect_cicd(file_paths)
    cloud = detect_cloud_providers(repo_root, file_paths)
    package_managers = detect_package_managers(file_paths)

    # Build a short tree for the LLM (first 80 lines)
    tree_summary = "\n".join(file_paths[:80])
    architecture = detect_architecture(
        tree_summary,
        [l.name for l in languages],
        frameworks,
    )

    analysis = RepoAnalysis(
        repo_url=state["repo_url"],
        languages=languages,
        frameworks=frameworks,
        architecture=architecture,
        docker=docker,
        tests=test_info,
        package_managers=package_managers,
        cloud_providers=cloud,
        cicd=cicd,
    )

    return {"result": analysis.model_dump()}


def cleanup_node(state: AnalyserState) -> dict:
    cleanup_repo(state["clone_path"])
    return {}


# ── Build graph ──────────────────────────────────────────────────────────────


def build_graph() -> StateGraph:
    graph = StateGraph(AnalyserState)
    graph.add_node("clone", clone_node)
    graph.add_node("scan", scan_node)
    graph.add_node("cleanup", cleanup_node)

    graph.set_entry_point("clone")
    graph.add_edge("clone", "scan")
    graph.add_edge("scan", "cleanup")
    graph.add_edge("cleanup", END)

    return graph


def run_analysis(repo_url: str) -> RepoAnalysis:
    """Run the full analysis pipeline and return structured output."""
    graph = build_graph()
    app = graph.compile()

    final_state = app.invoke({"repo_url": repo_url, "clone_path": "", "file_paths": [], "result": {}})
    return RepoAnalysis(**final_state["result"])
