"""LLM prompts used by the code-analyser agent."""

ARCHITECTURE_PROMPT = """You are analyzing a GitHub repository to determine its architecture style.

Based on the directory structure and metadata below, classify this repository into exactly ONE of:
- monorepo (multiple independent projects/packages sharing one repo)
- microservices (multiple independently deployable services)
- single-service (one backend service or API)
- monolith (one large tightly-coupled application)

Directory tree (depth 3):
{file_tree}

Detected languages: {languages}
Detected frameworks: {frameworks}

Respond with ONLY one word: monorepo, microservices, single-service, or monolith.
"""
