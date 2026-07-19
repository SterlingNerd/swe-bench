#!/bin/bash
# ==============================================================================
# SWE-bench Agent Entrypoint — runs the pi coding agent.
#
# Works with a self-contained agent bundle mounted at /agent by the swebench
# harness. Copies config to a writable location, then runs pi.
#
# Usage:
#   /entrypoint.sh --interactive          Drop into interactive shell
#   /entrypoint.sh <instance_id> <repo_url> <base_commit> <problem_statement>
#
# Container mounts (provided by swebench harness):
#   /agent    → agent bundle (read-only) — Node.js + pi CLI + config
#
# Environment variables:
#   SWE_OUTPUT_ROOT  Output root directory (default: /workspace/outputs)
#   SWE_AGENT_NAME   Agent name for metadata (default: pi)
# ==============================================================================

set -euo pipefail

AGENT_BUNDLE="${SWE_AGENT_BUNDLE:-/agent}"
if [ ! -d "${AGENT_BUNDLE}" ]; then
    echo "ERROR: Agent bundle not found at ${AGENT_BUNDLE}"
    exit 1
fi

# --- Setup writable config dir ---
# pi looks for config via PI_CODING_AGENT_DIR pointing to .pi/agent/
# Bundle has .pi/agent/ with settings.json, models.json, auth.json
PI_CONFIG_DIR="${SWE_PI_CONFIG_DIR:-/tmp/.pi/agent}"
mkdir -p "${PI_CONFIG_DIR}"
if [ -d "${AGENT_BUNDLE}/.pi/agent" ]; then
    cp -r "${AGENT_BUNDLE}/.pi/agent/"* "${PI_CONFIG_DIR}/" 2>/dev/null || true
fi

# --- Read instance_id from first arg (before using it) ---
# Interactive mode: drop into shell for debugging (check before consuming $1)
if [ "${1:-}" = "--interactive" ]; then
    echo "Starting interactive shell..."
    exec bash
fi

INSTANCE_ID="${1:?Usage: $0 <instance_id> <repo_url> <base_commit> <problem_statement>}"

# --- Setup paths ---
OUTPUT_ROOT="${SWE_OUTPUT_ROOT:-/workspace/outputs}"
OUTPUT_DIR="${OUTPUT_ROOT}/${INSTANCE_ID}"
REPOS_DIR="/tmp/repos"
NODE_BIN="${AGENT_BUNDLE}/bin"

export PATH="${NODE_BIN}:${PATH}"
export HOME="/tmp"
export PI_CODING_AGENT_DIR="${PI_CONFIG_DIR}"

echo "=============================================================================="
echo "SWE-bench Agent: ${INSTANCE_ID}"
echo "Agent bundle: ${AGENT_BUNDLE}"
echo "Node.js: $(node --version 2>/dev/null || echo 'not found')"
echo "pi CLI:  $(pi --version 2>/dev/null || echo 'not found')"
echo "Config:  ${PI_CODING_AGENT_DIR}/"
echo "=============================================================================="
REPO_URL="${2:?Missing repo_url}"
BASE_COMMIT="${3:?Missing base_commit}"
PROBLEM_STATEMENT="${4:?Missing problem_statement}"

# --- Setup output dir ---
mkdir -p "${OUTPUT_DIR}/eval"

# Save problem metadata (use python3 for proper JSON escaping)
python3 -c "
import json, sys
meta = {
    'instance_id': sys.argv[1],
    'repo_url': sys.argv[2],
    'base_commit': sys.argv[3],
    'agent': sys.argv[5]
}
json.dump(meta, open(sys.argv[4], 'w'))
" "${INSTANCE_ID}" "${REPO_URL}" "${BASE_COMMIT}" "${OUTPUT_DIR}/meta.json" "${SWE_AGENT_NAME:-pi}"
printf '%s\n' "$PROBLEM_STATEMENT" > "${OUTPUT_DIR}/problem_statement.txt"

# Use swebench's /testbed (repo already at base commit)
REPO_DIR="${SWE_TESTBED_DIR:-/testbed}"
cd "$REPO_DIR" || { echo "ERROR: Cannot cd to $REPO_DIR"; exit 1; }

# Run the agent using the bundled pi CLI (from inside the repo)
echo "  Running agent in $REPO_DIR..."
START_TIME=$(date +%s)
AGENT_OUTPUT="${OUTPUT_DIR}/agent_output.txt"
SESSION_DIR="${OUTPUT_DIR}/pi-sessions"
mkdir -p "${SESSION_DIR}"

TERMINATION_SIGNAL=""
AGENT_PID=""
KILL_TIMER_PID=""
forward_agent_signal() {
    local signal_name="$1"
    TERMINATION_SIGNAL="$signal_name"
    if [ -n "$AGENT_PID" ] && kill -0 "$AGENT_PID" 2>/dev/null; then
        echo "  ${signal_name} received; requesting Pi checkpoint shutdown..." >&2
        kill -TERM -- "-${AGENT_PID}" 2>/dev/null || kill -TERM "$AGENT_PID" 2>/dev/null || true
        (
            sleep 20
            kill -KILL -- "-${AGENT_PID}" 2>/dev/null || true
        ) &
        KILL_TIMER_PID=$!
    fi
}
trap 'forward_agent_signal TERM' TERM
trap 'forward_agent_signal INT' INT

set +e
set -m
pi -p --session-dir "${SESSION_DIR}" "${PROBLEM_STATEMENT}" \
    > >(tee "${AGENT_OUTPUT}") 2>&1 &
AGENT_PID=$!
set +m
wait "$AGENT_PID"
AGENT_EXIT_CODE=$?
while kill -0 "$AGENT_PID" 2>/dev/null; do
    wait "$AGENT_PID"
    AGENT_EXIT_CODE=$?
done
[ -z "$KILL_TIMER_PID" ] || kill "$KILL_TIMER_PID" 2>/dev/null || true
AGENT_PID=""
set -e
if [ "$AGENT_EXIT_CODE" -ne 0 ]; then
    echo "  WARNING: pi exited with status ${AGENT_EXIT_CODE}"
fi

# Extract a binary-safe patch with a temporary index so checkpointing never
# mutates the repository's live index.
echo "  Extracting patch..."
PATCH_FILE="${OUTPUT_DIR}/patch.diff"
PATCH_TMP=$(mktemp "${OUTPUT_DIR}/.patch.diff.XXXXXX")
INDEX_TMP=$(mktemp "${OUTPUT_DIR}/.checkpoint-index.XXXXXX")
rm -f "$INDEX_TMP"
PATCH_CAPTURE_ERROR=0
CHECKPOINT_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if ! GIT_INDEX_FILE="$INDEX_TMP" git read-tree "$BASE_COMMIT" 2>/dev/null; then
    echo "  WARNING: checkpoint index initialization failed"
    PATCH_CAPTURE_ERROR=1
elif ! GIT_INDEX_FILE="$INDEX_TMP" git add -A 2>/dev/null; then
    echo "  WARNING: git add failed"
    PATCH_CAPTURE_ERROR=1
fi
if [ "$PATCH_CAPTURE_ERROR" -eq 0 ] && \
        GIT_INDEX_FILE="$INDEX_TMP" git diff --cached --binary "$BASE_COMMIT" \
            > "$PATCH_TMP" 2>/dev/null; then
    mv "$PATCH_TMP" "$PATCH_FILE"
else
    [ "$PATCH_CAPTURE_ERROR" -ne 0 ] || echo "  WARNING: git diff failed"
    rm -f "$PATCH_TMP"
    : > "$PATCH_FILE"
    PATCH_CAPTURE_ERROR=1
fi
rm -f "$INDEX_TMP"
CHECKPOINT_FINISHED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

PATCH_SIZE=$(wc -c < "$PATCH_FILE" 2>/dev/null || echo 0)
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

TERMINATION_STATUS=""
TERMINATION_REASON="agent_exit"
TERMINATION_REQUESTED_AT=""
if [ -n "$TERMINATION_SIGNAL" ]; then
    read -r TERMINATION_STATUS TERMINATION_REASON TERMINATION_REQUESTED_AT < <(
        REQUEST_FILE="${OUTPUT_DIR}/termination-request.json" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["REQUEST_FILE"])
request = {}
try:
    request = json.load(path.open())
except (OSError, json.JSONDecodeError):
    pass
print(
    request.get("requested_status", "operator_cancelled"),
    request.get("reason", "signal"),
    request.get("requested_at", "unknown"),
)
PY
    )
fi
if [ "$PATCH_CAPTURE_ERROR" -ne 0 ]; then
    STATUS="invalid_result"
elif [ -n "$TERMINATION_STATUS" ]; then
    STATUS="$TERMINATION_STATUS"
elif [ "$AGENT_EXIT_CODE" -ne 0 ]; then
    STATUS="agent_error"
elif [ "$PATCH_SIZE" -gt 0 ]; then
    STATUS="patch_collected"
else
    STATUS="no_patch"
fi

RESULT_STATUS="$STATUS" PATCH_SIZE="$PATCH_SIZE" ELAPSED="$ELAPSED" \
    AGENT_EXIT_CODE="$AGENT_EXIT_CODE" RESULT_FILE="${OUTPUT_DIR}/result.json" \
    PATCH_CAPTURE_ERROR="$PATCH_CAPTURE_ERROR" TERMINATION_SIGNAL="$TERMINATION_SIGNAL" \
    TERMINATION_REASON="$TERMINATION_REASON" \
    TERMINATION_REQUESTED_AT="$TERMINATION_REQUESTED_AT" \
    CHECKPOINT_STARTED_AT="$CHECKPOINT_STARTED_AT" \
    CHECKPOINT_FINISHED_AT="$CHECKPOINT_FINISHED_AT" python3 - <<'PY'
import json
import os
import tempfile
from pathlib import Path

result = {
    "schema_version": 1,
    "status": os.environ["RESULT_STATUS"],
    "patch_bytes": int(os.environ["PATCH_SIZE"]),
    "elapsed_seconds": int(os.environ["ELAPSED"]),
    "agent_exit_code": int(os.environ["AGENT_EXIT_CODE"]),
    "patch_capture_error": bool(int(os.environ["PATCH_CAPTURE_ERROR"])),
    "checkpointed": not bool(int(os.environ["PATCH_CAPTURE_ERROR"])),
    "partial_patch": bool(os.environ["TERMINATION_SIGNAL"]),
    "finalization_reason": os.environ["TERMINATION_REASON"],
    "termination_signal": os.environ["TERMINATION_SIGNAL"] or None,
    "termination_requested_at": os.environ["TERMINATION_REQUESTED_AT"] or None,
    "checkpoint_started_at": os.environ["CHECKPOINT_STARTED_AT"],
    "checkpoint_finished_at": os.environ["CHECKPOINT_FINISHED_AT"],
}
target = Path(os.environ["RESULT_FILE"])
fd, name = tempfile.mkstemp(prefix=".result.", dir=target.parent)
try:
    with os.fdopen(fd, "w") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(name, target)
finally:
    Path(name).unlink(missing_ok=True)
PY

echo "  Result: ${STATUS}; patch ${PATCH_SIZE} bytes; ${ELAPSED}s"
echo "  Output: ${OUTPUT_DIR}/"
