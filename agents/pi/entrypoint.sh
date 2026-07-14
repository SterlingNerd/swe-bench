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

AGENT_BUNDLE="/agent"
if [ ! -d "${AGENT_BUNDLE}" ]; then
    echo "ERROR: Agent bundle not found at ${AGENT_BUNDLE}"
    exit 1
fi

# --- Setup writable config dir ---
# pi looks for config via PI_CODING_AGENT_DIR pointing to .pi/agent/
# Bundle has .pi/agent/ with settings.json, models.json, auth.json
PI_CONFIG_DIR="/tmp/.pi/agent"
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
echo "${PROBLEM_STATEMENT}" > "${OUTPUT_DIR}/problem_statement.txt"

# Use swebench's /testbed (repo already at base commit)
REPO_DIR="/testbed"
cd "$REPO_DIR" || { echo "ERROR: Cannot cd to $REPO_DIR"; exit 1; }

# Run the agent using the bundled pi CLI (from inside the repo)
echo "  Running agent in $REPO_DIR..."
START_TIME=$(date +%s)
AGENT_OUTPUT="${OUTPUT_DIR}/agent_output.txt"
SESSION_DIR="${OUTPUT_DIR}/pi-sessions"
mkdir -p "${SESSION_DIR}"

set +e
pi -p --session-dir "${SESSION_DIR}" "${PROBLEM_STATEMENT}" 2>&1 | tee "${AGENT_OUTPUT}"
AGENT_EXIT_CODE=${PIPESTATUS[0]}
set -e
if [ "$AGENT_EXIT_CODE" -ne 0 ]; then
    echo "  WARNING: pi exited with status ${AGENT_EXIT_CODE}"
fi

# Extract patch via git diff (from inside the repo)
# Must stage new/untracked files first — git diff alone drops them
echo "  Extracting patch..."
git add -A 2>/dev/null || true
git diff --binary "$BASE_COMMIT" > "${OUTPUT_DIR}/patch.diff" 2>/dev/null || {
    echo "  WARNING: git diff failed"
    touch "${OUTPUT_DIR}/patch.diff"
}

PATCH_SIZE=$(wc -c < "${OUTPUT_DIR}/patch.diff" 2>/dev/null || echo 0)
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

if [ "$PATCH_SIZE" -gt 0 ]; then
    STATUS="patch_collected"
    echo "  Patch collected (${PATCH_SIZE} bytes)."
elif [ "$AGENT_EXIT_CODE" -ne 0 ]; then
    STATUS="agent_error"
    echo "  ERROR: Agent failed without generating a patch."
else
    STATUS="no_patch"
    echo "  WARNING: No patch generated (0 bytes)"
fi

RESULT_STATUS="$STATUS" PATCH_SIZE="$PATCH_SIZE" ELAPSED="$ELAPSED" \
    AGENT_EXIT_CODE="$AGENT_EXIT_CODE" RESULT_FILE="${OUTPUT_DIR}/result.json" \
    python3 - <<'PY'
import json
import os

result = {
    "status": os.environ["RESULT_STATUS"],
    "patch_bytes": int(os.environ["PATCH_SIZE"]),
    "elapsed_seconds": int(os.environ["ELAPSED"]),
    "agent_exit_code": int(os.environ["AGENT_EXIT_CODE"]),
}
with open(os.environ["RESULT_FILE"], "w") as handle:
    json.dump(result, handle, indent=2)
PY

echo "  Output: ${OUTPUT_DIR}/"
