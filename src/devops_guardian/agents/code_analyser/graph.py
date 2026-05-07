"""LangGraph state graph for the Code Analyser agent."""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from devops_guardian.agents.code_analyser.scanners.architecture import detect_architecture
from devops_guardian.agents.code_analyser.scanners.cicd import detect_cicd
from devops_guardian.agents.code_analyser.scanners.cloud import detect_cloud_providers
from devops_guardian.agents.code_analyser.scanners.deployment import detect_deployment
from devops_guardian.agents.code_analyser.scanners.docker import detect_docker
from devops_guardian.agents.code_analyser.scanners.frameworks import detect_frameworks
from devops_guardian.agents.code_analyser.scanners.installed_packages import detect_installed_packages
from devops_guardian.agents.code_analyser.scanners.languages import detect_languages
from devops_guardian.agents.code_analyser.scanners.packages import detect_dependency_files, detect_package_managers
from devops_guardian.agents.code_analyser.scanners.tests import detect_tests
from devops_guardian.models.analysis import RepoAnalysis
from devops_guardian.utils.repo_ops import build_file_tree, cleanup_repo, clone_repo
from devops_guardian.utils.run_logger import RunLogger

logger = logging.getLogger(__name__)


class AnalyserState(TypedDict):
    repo_url: str
    clone_path: str
    file_paths: list[str]
    run_dir: str  # path to the run output directory (empty = no logging)
    result: dict[str, Any]


# ── Graph nodes ──────────────────────────────────────────────────────────────


def clone_node(state: AnalyserState) -> dict:
    clone_path = clone_repo(state["repo_url"])
    file_paths = build_file_tree(clone_path)
    return {"clone_path": clone_path, "file_paths": file_paths}


def scan_node(state: AnalyserState) -> dict:
    repo_root = state["clone_path"]
    file_paths = state["file_paths"]

    # Set up structured logger if run_dir is provided
    rl: RunLogger | None = None
    if state.get("run_dir"):
        rl = RunLogger(state["run_dir"], "agent1")
        rl.attach()
        rl.save_file_tree(file_paths)
        logger.info("Agent 1 — scanning %d files …", len(file_paths))

    languages = detect_languages(file_paths)
    if rl:
        rl.save_scanner("languages", [l.model_dump() for l in languages])

    frameworks = detect_frameworks(repo_root, file_paths)
    if rl:
        rl.save_scanner("frameworks", frameworks)

    docker = detect_docker(repo_root, file_paths)
    if rl:
        rl.save_scanner("docker", docker.model_dump())

    test_info = detect_tests(repo_root, file_paths)
    if rl:
        rl.save_scanner("tests", test_info.model_dump())

    cicd = detect_cicd(file_paths)
    if rl:
        rl.save_scanner("cicd", cicd)

    cloud = detect_cloud_providers(repo_root, file_paths)
    if rl:
        rl.save_scanner("cloud", cloud)

    package_managers = detect_package_managers(file_paths)
    if rl:
        rl.save_scanner("packages", [pm.model_dump() for pm in package_managers])

    dependency_files = detect_dependency_files(file_paths)
    if rl:
        rl.save_scanner("dependency_files", dependency_files.model_dump())

    installed_packages = detect_installed_packages(repo_root, file_paths)
    if rl:
        rl.save_scanner("installed_packages", installed_packages.model_dump())

    deployment = detect_deployment(repo_root, file_paths, docker.has_dockerfile)
    if rl:
        rl.save_scanner("deployment", deployment.model_dump())

    # Build a short tree for the LLM (first 80 lines)
    tree_summary = "\n".join(file_paths[:80])
    architecture = detect_architecture(
        tree_summary,
        [l.name for l in languages],
        frameworks,
    )
    if rl:
        rl.save_scanner("architecture", architecture)

    analysis = RepoAnalysis(
        repo_url=state["repo_url"],
        languages=languages,
        frameworks=frameworks,
        architecture=architecture,
        docker=docker,
        tests=test_info,
        installed_packages=installed_packages,
        package_managers=package_managers,
        dependency_files=dependency_files,
        deployment=deployment,
        cloud_providers=cloud,
        cicd=cicd,
    )

    if rl:
        logger.info("Agent 1 — scan complete.")
        rl.detach()

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


def run_analysis(repo_url: str, run_dir: str = "") -> RepoAnalysis:
    """Run the full analysis pipeline and return structured output."""
    graph = build_graph()
    app = graph.compile()

    final_state = app.invoke({
        "repo_url": repo_url,
        "clone_path": "",
        "file_paths": [],
        "run_dir": run_dir,
        "result": {},
    })
    return RepoAnalysis(**final_state["result"])
