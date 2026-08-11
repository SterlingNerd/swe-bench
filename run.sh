#!/usr/bin/env bash
# SWE-bench Orchestrator — wrapper that activates the venv and runs the CLI.
# Usage: ./run.sh [swebench-orchestrator args...]
#
# Examples:
#   ./run.sh --index
#   ./run.sh --build
#   ./run.sh --run pi django__django-11039
#   ./run.sh --run-all pi --timeout 3600 --resume
#   ./run.sh --eval pi
#   ./run.sh --summarize

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_SCRIPT="$SCRIPT_DIR/.venv/bin/swebench-orchestrator"

if [ ! -f "$VENV_SCRIPT" ]; then
    echo "ERROR: Virtual environment not found at $SCRIPT_DIR/.venv/" >&2
    echo "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'" >&2
    exit 1
fi

exec "$VENV_SCRIPT" "$@"
