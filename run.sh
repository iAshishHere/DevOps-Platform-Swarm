#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"
source .venv/bin/activate
exec python -m uvicorn devops_guardian.api:app --reload --port 8001
