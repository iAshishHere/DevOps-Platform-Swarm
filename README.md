# DevOps-Platform-Swarm

Multi AI agents solution to build entire DevOps lifecycle.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Environment Variables](#environment-variables)
- [GitHub PAT Token Permissions](#github-pat-token-permissions)
- [Getting Started](#getting-started)

---

## Overview

DevOps Guardian is a multi-agent platform that:

1. **Agent 1 — Code Analyser**: Clones a repository, scans its codebase (languages, frameworks, packages, CI/CD, Docker, cloud, tests, architecture), and detects which GitHub security features are enabled.
2. **Agent 2 — Pipeline Generator**: Uses the analysis to generate GitHub Actions workflow files, pushes them to a branch, monitors workflow runs, auto-fixes failures, and creates a pull request.

---

## Architecture

```
User → FastAPI / Django UI
         │
         ├── Agent 1 (Code Analyser)
         │     ├── Clone repo                    ← GITHUB_TOKEN (Contents: Read)
         │     ├── Scan files locally             ← no token needed
         │     └── Detect GitHub features         ← GITHUB_TOKEN (Security scopes)
         │
         └── Agent 2 (Pipeline Generator)
               ├── Generate pipeline YAML         ← Azure OpenAI (LLM)
               ├── Push to branch                 ← GITHUB_TOKEN (Contents + Workflows: Write)
               ├── Wait for workflow runs          ← GITHUB_TOKEN (Actions: Read)
               ├── Download run logs               ← GITHUB_TOKEN (Actions: Read)
               ├── Fix & re-push (loop)            ← GITHUB_TOKEN (Contents + Workflows: Write)
               └── Create pull request             ← GITHUB_TOKEN (Pull Requests: Write)
```

---

## Prerequisites

- Python 3.11+
- An Azure OpenAI deployment (GPT-4o or equivalent)
- A GitHub Personal Access Token (fine-grained recommended)

---

## Environment Variables

Create a `.env` file at the project root:

```env
# ── Required ─────────────────────────────────────────────
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_DEPLOYMENT=<deployment-name>

# ── Optional ─────────────────────────────────────────────
AZURE_OPENAI_API_VERSION=2024-12-01-preview   # default
DEFAULT_BRANCH=main                           # target branch for PRs
MAX_FIX_ATTEMPTS=3                            # max fix-loop iterations
PIPELINE_POLL_INTERVAL=30                     # seconds between run polls
PIPELINE_WAIT_TIMEOUT=900                     # max seconds to wait for runs
```

---

## GitHub PAT Token Permissions

A **fine-grained Personal Access Token** is recommended over classic tokens for least-privilege access.

> **Scope the token** to only the repositories you intend to analyse and generate pipelines for.

### Required Permissions

| Permission | Access Level | Used By | Reason |
|---|---|---|---|
| **Contents** | **Read & Write** | Agent 1 & 2 | **Read**: Clone the repository and read source files for code analysis. **Write**: Push generated pipeline YAML files to the feature branch. |
| **Workflows** | **Read & Write** | Agent 2 | GitHub requires explicit workflow-file permission to push changes to `.github/workflows/`. Without this, pushes that modify workflow files are rejected with a 403 error. |
| **Pull Requests** | **Read & Write** | Agent 2 | Create a pull request once all generated pipelines pass (or reach max fix attempts). |
| **Actions** | **Read** | Agent 2 | Poll workflow run status (`GET /repos/{owner}/{repo}/actions/runs`) and download run logs to diagnose failures in the fix loop. |

### Recommended Permissions (for full feature detection)

These permissions allow Agent 1 to accurately detect which GitHub security features are enabled on the repository. The generated pipelines then adapt accordingly (e.g., including SARIF upload only when Code Scanning is enabled).

| Permission | Access Level | Used By | Reason |
|---|---|---|---|
| **Security Events** | **Read** | Agent 1 | Probe `GET /repos/{owner}/{repo}/code-scanning/alerts` to detect if GitHub Code Scanning (CodeQL / SARIF uploads) is enabled. Without this, the agent assumes Code Scanning is disabled and skips SARIF upload steps. |
| **Dependabot Alerts** | **Read** | Agent 1 | Probe `GET /repos/{owner}/{repo}/dependabot/alerts` to detect if Dependabot vulnerability alerts are enabled. Informs pipeline generation decisions. |
| **Secret Scanning Alerts** | **Read** | Agent 1 | Probe `GET /repos/{owner}/{repo}/secret-scanning/alerts` to detect if GitHub Secret Scanning is enabled. Informs whether to include secret-scanning-aware steps. |
| **Secrets** | **Read** | Agent 1 | List GitHub Actions secret names (`GET /repos/{owner}/{repo}/actions/secrets`) to determine which environment variables are already configured as secrets. Pipelines then use `${{ secrets.VAR_NAME }}` for those and inject dummy CI values for the rest. Without this, all required env vars get dummy values. |

### What Happens Without the Recommended Permissions?

The platform **still works** without the security-related scopes. The `detect_github_features()` function handles 403/404 responses gracefully — it simply defaults the corresponding feature flag to `false`. This means:

- Code analysis completes normally (all file-based scanning works without any token).
- Pipelines are generated, but security-dependent steps (like SARIF upload) are excluded since the agent can't confirm the feature is enabled.
- No errors are thrown; the behaviour is conservative.

### Classic PAT (not recommended)

If you must use a classic PAT, these scopes are required:

| Scope | Reason |
|---|---|
| `repo` | Full access to repository contents, PRs, and commit status. |
| `workflow` | Permission to create and update workflow files under `.github/workflows/`. |

> **Note**: Classic PATs grant broader access than needed. Fine-grained PATs are preferred for security.

---

## Getting Started

```bash
# 1. Install dependencies
pip install -e .

# 2. Set up environment variables
cp .env.example .env
# Edit .env with your values

# 3. Run via CLI
devops-guardian analyse https://github.com/<owner>/<repo>

# 4. Or run the API server
uvicorn devops_guardian.api:app --host 0.0.0.0 --port 8000
```
