#!/bin/bash
# ==============================================================================
# SWE-bench Agent Entrypoint — generic clone → run → extract → eval
#
# Usage:
#   /entrypoint.sh <instance_id> <repo_url> <base_commit> <problem_statement>
#
# This script is shared across agent containers (pi, codex, claude, etc.).
# The agent-specific part is just the command that runs the agent.
# Everything else (clone, extract patch, eval) is in the base image.
# ==============================================================================

set -euo pipefail

INSTANCE_ID="${1:?Usage: $0 <instance_id> <repo_url> <base_commit> <problem_statement>}"
REPO_URL="${2:?Missing repo_url}"
BASE_COMMIT="${3:?Missing base_commit}"
PROBLEM_STATEMENT="${4:?Missing problem_statement}"

WORKSPACE="/home/agent/workspace"
REPOS_DIR="${WORKSPACE}/repos"
OUTPUT_DIR="${WORKSPACE}/outputs/${INSTANCE_ID}"
export SESSION_ID="${INSTANCE_ID}"
AGENT_CMD="pi -p --session-dir /tmp/pi-sessions --session-id ${SESSION_ID}"

echo "=============================================================================="
echo "SWE-bench Agent: ${INSTANCE_ID}"
echo "=============================================================================="

# Create output directory
mkdir -p "${OUTPUT_DIR}/eval"

# Save problem metadata
cat > "${OUTPUT_DIR}/meta.json" <<EOF
{"instance_id": "${INSTANCE_ID}", "repo_url": "${REPO_URL}", "base_commit": "${BASE_COMMIT}"}
EOF
echo "${PROBLEM_STATEMENT}" > "${OUTPUT_DIR}/problem_statement.txt"

# Clone repo at base commit
REPO_NAME=$(echo "${REPO_URL}" | sed 's|https://github.com/||; s|\.git$||')
REPO_DIR="${REPOS_DIR}/${REPO_NAME}"

if [ ! -d "$REPO_DIR" ]; then
    echo "  Cloning ${REPO_NAME} @ ${BASE_COMMIT:0:8}..."
    mkdir -p "${REPOS_DIR}"
    git clone "${REPO_URL}.git" "$REPO_DIR" 2>&1 | tail -1
fi

cd "$REPO_DIR" && git checkout "$BASE_COMMIT" >/dev/null 2>&1
cd - >/dev/null

# Run the agent
echo "  Running agent..."
START_TIME=$(date +%s)
AGENT_OUTPUT="${OUTPUT_DIR}/agent_output.txt"

${AGENT_CMD} "${PROBLEM_STATEMENT}" 2>&1 | tee "${AGENT_OUTPUT}" || true

# Save session file if it exists
if [ -f "/tmp/pi-sessions/${INSTANCE_ID}/session.jsonl" ]; then
    cp "/tmp/pi-sessions/${INSTANCE_ID}/session.jsonl" "${OUTPUT_DIR}/session.jsonl" 2>/dev/null || true
fi

# Extract patch via git diff
echo "  Extracting patch..."
cd "$REPO_DIR" && git diff > "${OUTPUT_DIR}/patch.diff" 2>/dev/null || true
cd - >/dev/null

PATCH_SIZE=$(wc -c < "${OUTPUT_DIR}/patch.diff" 2>/dev/null || echo 0)

if [ "$PATCH_SIZE" -eq 0 ]; then
    echo "  WARNING: No patch generated (0 bytes)"
    echo '{"status": "no_patch", "patch_bytes": 0}' > "${OUTPUT_DIR}/result.json"
else
    echo "  Patch collected (${PATCH_SIZE} bytes)."
    echo "  Run './run.sh --eval <agent>' to evaluate with swebench harness."
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    echo "{\"status\": \"patch_collected\", \"patch_bytes\": ${PATCH_SIZE}, \"elapsed_seconds\": ${ELAPSED}}" > "${OUTPUT_DIR}/result.json"
fi
echo "  Output: ${OUTPUT_DIR}/"
