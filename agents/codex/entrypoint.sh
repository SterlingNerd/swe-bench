#!/bin/bash
# Run Codex non-interactively inside a pre-built SWE-bench evaluation image.
set -euo pipefail

AGENT_BUNDLE="${SWE_AGENT_BUNDLE:-/agent}"
if [ ! -d "$AGENT_BUNDLE" ]; then
    echo "ERROR: Agent bundle not found at ${AGENT_BUNDLE}"
    exit 1
fi

CODEX_RUNTIME_DIR="${SWE_CODEX_RUNTIME_DIR:-/workspace/codex-runtime}"
export HOME="${CODEX_RUNTIME_DIR}/home"
export CODEX_HOME="${HOME}/.codex"
export PATH="${AGENT_BUNDLE}/bin:${AGENT_BUNDLE}/codex-path:${PATH}"
# llama.cpp accepts any non-empty bearer token unless its server is configured
# with a real API key. Never mount a host Codex login into benchmark containers.
export SWE_CODEX_API_KEY="${SWE_CODEX_API_KEY:-local-key}"

mkdir -p "$CODEX_HOME"
chmod 700 "$CODEX_RUNTIME_DIR" "$HOME" "$CODEX_HOME"
CODEX_MODEL="${SWE_CODEX_MODEL:-qwen3.6-35b-a3b}"
CODEX_BASE_URL="${SWE_CODEX_BASE_URL:-http://host.docker.internal:11434/v1}"
CODEX_CONTEXT_WINDOW="${SWE_CODEX_CONTEXT_WINDOW:-256000}"
CODEX_AUTO_COMPACT_TOKEN_LIMIT="${SWE_CODEX_AUTO_COMPACT_TOKEN_LIMIT:-230400}"

if ! [[ "$CODEX_CONTEXT_WINDOW" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: SWE_CODEX_CONTEXT_WINDOW must be a positive integer" >&2
    exit 2
fi
if ! [[ "$CODEX_AUTO_COMPACT_TOKEN_LIMIT" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: SWE_CODEX_AUTO_COMPACT_TOKEN_LIMIT must be a positive integer" >&2
    exit 2
fi
if [ "$CODEX_AUTO_COMPACT_TOKEN_LIMIT" -gt "$CODEX_CONTEXT_WINDOW" ]; then
    echo "ERROR: auto-compact limit cannot exceed the context window" >&2
    exit 2
fi

CODEX_TEMPLATE="${AGENT_BUNDLE}/config.toml" \
    CODEX_CONFIG="${CODEX_HOME}/config.toml" \
    CODEX_MODEL_VALUE="$CODEX_MODEL" CODEX_BASE_URL_VALUE="$CODEX_BASE_URL" \
    CODEX_CONTEXT_WINDOW_VALUE="$CODEX_CONTEXT_WINDOW" \
    CODEX_AUTO_COMPACT_TOKEN_LIMIT_VALUE="$CODEX_AUTO_COMPACT_TOKEN_LIMIT" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

template = Path(os.environ["CODEX_TEMPLATE"]).read_text()
replacements = {
    '"__SWE_CODEX_MODEL__"': json.dumps(os.environ["CODEX_MODEL_VALUE"]),
    '"__SWE_CODEX_BASE_URL__"': json.dumps(os.environ["CODEX_BASE_URL_VALUE"]),
    "__SWE_CODEX_CONTEXT_WINDOW__": os.environ["CODEX_CONTEXT_WINDOW_VALUE"],
    "__SWE_CODEX_AUTO_COMPACT_TOKEN_LIMIT__": os.environ[
        "CODEX_AUTO_COMPACT_TOKEN_LIMIT_VALUE"
    ],
}
for placeholder, value in replacements.items():
    if placeholder not in template:
        raise SystemExit(f"missing Codex config placeholder: {placeholder}")
    template = template.replace(placeholder, value)
if "__SWE_CODEX_" in template:
    raise SystemExit("unrendered Codex config placeholder")
Path(os.environ["CODEX_CONFIG"]).write_text(template)
PY

if [ "${1:-}" = "--interactive" ]; then
    echo "Starting interactive shell with Codex $(codex --version)..."
    exec bash
fi

INSTANCE_ID="${1:?Usage: $0 <instance_id> <repo_url> <base_commit> <problem_statement>}"
REPO_URL="${2:?Missing repo_url}"
BASE_COMMIT="${3:?Missing base_commit}"
PROBLEM_STATEMENT="${4:?Missing problem_statement}"
OUTPUT_ROOT="${SWE_OUTPUT_ROOT:-/workspace/outputs}"
OUTPUT_DIR="${OUTPUT_ROOT}/${INSTANCE_ID}"
REPO_DIR="${SWE_TESTBED_DIR:-/testbed}"

mkdir -p "${OUTPUT_DIR}/eval"
META_FILE="${OUTPUT_DIR}/meta.json"
INSTANCE_ID_VALUE="$INSTANCE_ID" REPO_URL_VALUE="$REPO_URL" \
    BASE_COMMIT_VALUE="$BASE_COMMIT" META_FILE="$META_FILE" \
    AGENT_NAME_VALUE="${SWE_AGENT_NAME:-codex}" \
    CODEX_VERSION_VALUE="$(codex --version 2>/dev/null || echo unknown)" \
    CODEX_MODEL_VALUE="$CODEX_MODEL" CODEX_BASE_URL_VALUE="$CODEX_BASE_URL" \
    python3 - <<'PY'
import json
import os

meta = {
    "instance_id": os.environ["INSTANCE_ID_VALUE"],
    "repo_url": os.environ["REPO_URL_VALUE"],
    "base_commit": os.environ["BASE_COMMIT_VALUE"],
    "agent": os.environ["AGENT_NAME_VALUE"],
    "agent_version": os.environ["CODEX_VERSION_VALUE"],
    "model": os.environ["CODEX_MODEL_VALUE"],
    "provider": "local_swebench",
    "base_url": os.environ["CODEX_BASE_URL_VALUE"],
}
with open(os.environ["META_FILE"], "w") as handle:
    json.dump(meta, handle, indent=2)
PY
printf '%s\n' "$PROBLEM_STATEMENT" > "${OUTPUT_DIR}/problem_statement.txt"

cd "$REPO_DIR" || { echo "ERROR: Cannot cd to ${REPO_DIR}"; exit 1; }

PROMPT=$(printf '%s\n\n%s' \
    "Solve the SWE-bench issue below in /testbed. Inspect the repository, implement the smallest correct production fix, and run focused tests when practical. Do not commit. Do not weaken or rewrite tests merely to bypass the intended behavior. Leave all intended changes in the working tree; the harness will extract the patch." \
    "$PROBLEM_STATEMENT")

echo "=============================================================================="
echo "SWE-bench Agent: ${INSTANCE_ID}"
echo "Codex: $(codex --version 2>/dev/null || echo 'not found')"
echo "Model: ${CODEX_MODEL}"
echo "Model endpoint: ${CODEX_BASE_URL}"
echo "=============================================================================="

START_TIME=$(date +%s)
TRAJECTORY_FILE="${OUTPUT_DIR}/trajectory.jsonl"
AGENT_OUTPUT="${OUTPUT_DIR}/agent_output.txt"
AGENT_STDERR="${OUTPUT_DIR}/agent_stderr.txt"

set +e
codex exec \
    --strict-config \
    --cd "$REPO_DIR" \
    --dangerously-bypass-approvals-and-sandbox \
    --ephemeral \
    --json \
    --output-last-message "$AGENT_OUTPUT" \
    "$PROMPT" \
    2> >(tee "$AGENT_STDERR" >&2) | tee "$TRAJECTORY_FILE"
AGENT_EXIT_CODE=${PIPESTATUS[0]}
set -e

if [ "$AGENT_EXIT_CODE" -ne 0 ]; then
    echo "  WARNING: Codex exited with status ${AGENT_EXIT_CODE}"
fi
[ -f "$AGENT_OUTPUT" ] || touch "$AGENT_OUTPUT"

echo "  Extracting patch..."
PATCH_FILE="${OUTPUT_DIR}/patch.diff"
PATCH_TMP=$(mktemp "${OUTPUT_DIR}/.patch.diff.XXXXXX")
PATCH_CAPTURE_ERROR=0
if ! git add -A 2>/dev/null; then
    echo "  WARNING: git add failed"
    PATCH_CAPTURE_ERROR=1
fi
if [ "$PATCH_CAPTURE_ERROR" -eq 0 ] && \
        git diff --binary "$BASE_COMMIT" > "$PATCH_TMP" 2>/dev/null; then
    mv "$PATCH_TMP" "$PATCH_FILE"
else
    if [ "$PATCH_CAPTURE_ERROR" -eq 0 ]; then
        echo "  WARNING: git diff failed"
    fi
    rm -f "$PATCH_TMP"
    : > "$PATCH_FILE"
    PATCH_CAPTURE_ERROR=1
fi

PATCH_SIZE=$(wc -c < "$PATCH_FILE" 2>/dev/null || echo 0)
ELAPSED=$(( $(date +%s) - START_TIME ))
if [ "$PATCH_CAPTURE_ERROR" -ne 0 ]; then
    STATUS="invalid_result"
elif [ "$AGENT_EXIT_CODE" -ne 0 ]; then
    STATUS="agent_error"
elif [ "$PATCH_SIZE" -gt 0 ]; then
    STATUS="patch_collected"
else
    STATUS="no_patch"
fi

RESULT_STATUS="$STATUS" PATCH_SIZE="$PATCH_SIZE" ELAPSED="$ELAPSED" \
    AGENT_EXIT_CODE="$AGENT_EXIT_CODE" RESULT_FILE="${OUTPUT_DIR}/result.json" \
    PATCH_CAPTURE_ERROR="$PATCH_CAPTURE_ERROR" \
    TRAJECTORY_FILE="$TRAJECTORY_FILE" python3 - <<'PY'
import json
import os

result = {
    "status": os.environ["RESULT_STATUS"],
    "patch_bytes": int(os.environ["PATCH_SIZE"]),
    "elapsed_seconds": int(os.environ["ELAPSED"]),
    "agent_exit_code": int(os.environ["AGENT_EXIT_CODE"]),
    "patch_capture_error": bool(int(os.environ["PATCH_CAPTURE_ERROR"])),
}

# Preserve the last usage event when the provider supplies one.
try:
    with open(os.environ["TRAJECTORY_FILE"]) as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed" and event.get("usage"):
                result["usage"] = event["usage"]
except FileNotFoundError:
    pass

with open(os.environ["RESULT_FILE"], "w") as handle:
    json.dump(result, handle, indent=2)
PY

echo "  Result: ${STATUS}; patch ${PATCH_SIZE} bytes; ${ELAPSED}s"
echo "  Output: ${OUTPUT_DIR}/"
