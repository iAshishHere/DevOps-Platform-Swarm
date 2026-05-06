"""LLM-powered architecture inference (monorepo, microservices, etc.)."""

from __future__ import annotations

from typing import Literal

from devops_guardian.agents.code_analyser.prompts import ARCHITECTURE_PROMPT
from devops_guardian.utils.llm import get_llm


def detect_architecture(
    file_tree_summary: str,
    languages: list[str],
    frameworks: list[str],
) -> Literal["monorepo", "microservices", "single-service", "monolith", "unknown"]:
    """Use an LLM to infer the repo's architecture style."""
    llm = get_llm()

    prompt = ARCHITECTURE_PROMPT.format(
        file_tree=file_tree_summary,
        languages=", ".join(languages) if languages else "none detected",
        frameworks=", ".join(frameworks) if frameworks else "none detected",
    )

    response = llm.invoke(prompt)
    result = response.content.strip().lower()

    valid = {"monorepo", "microservices", "single-service", "monolith"}
    if result in valid:
        return result  # type: ignore[return-value]
    # Try to extract from a longer response
    for v in valid:
        if v in result:
            return v  # type: ignore[return-value]
    return "unknown"
