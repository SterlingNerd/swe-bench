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
#   /testbed  → writable testbed directory
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

# --- Setup paths ---
OUTPUT_DIR="/workspace/outputs/${INSTANCE_ID:-unknown}"
REPOS_DIR="/workspace/repos"
NODE_BIN="${AGENT_BUNDLE}/bin"

export PATH="${NODE_BIN}:${PATH}"
export HOME="/tmp"
export PI_CODING_AGENT_DIR="${PI_CONFIG_DIR}"

echo "=============================================================================="
echo "SWE-bench Agent: ${INSTANCE_ID:-unknown}"
echo "Agent bundle: ${AGENT_BUNDLE}"
echo "Node.js: $(node --version 2>/dev/null || echo 'not found')"
echo "pi CLI:  $(pi --version 2>/dev/null || echo 'not found')"
echo "Config:  ${PI_CODING_AGENT_DIR}/"
echo "=============================================================================="

# Interactive mode: drop into shell for debugging
if [ "${1:-}" = "--interactive" ]; then
    echo "Starting interactive shell..."
    exec bash
fi

INSTANCE_ID="${1:?Usage: $0 <instance_id> <repo_url> <base_commit> <problem_statement>}"
REPO_URL="${2:?Missing repo_url}"
BASE_COMMIT="${3:?Missing base_commit}"
PROBLEM_STATEMENT="${4:?Missing problem_statement}"

# --- Setup output dir ---
mkdir -p "${OUTPUT_DIR}/eval"

# Verification: write hello world
echo "Hello from pi container at $(date)" > "${OUTPUT_DIR}/hello.txt"

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
    REPO_URL_CLEAN=$(echo "${REPO_URL}" | sed 's/\.git$//')
    git clone "${REPO_URL_CLEAN}.git" "$REPO_DIR" 2>&1 | tail -1
fi

# cd into the repo — pi needs to run inside the codebase
cd "$REPO_DIR" || { echo "ERROR: Cannot cd to $REPO_DIR"; exit 1; }
git checkout "$BASE_COMMIT" || { echo "ERROR: Cannot checkout $BASE_COMMIT"; exit 1; }

# Run the agent using the bundled pi CLI (from inside the repo)
echo "  Running agent in $REPO_DIR..."
START_TIME=$(date +%s)
AGENT_OUTPUT="${OUTPUT_DIR}/agent_output.txt"

pi -p --session-dir /tmp/pi-sessions "${PROBLEM_STATEMENT}" 2>&1 | tee "${AGENT_OUTPUT}" || {
    echo "  WARNING: pi exited with non-zero status"
}

# Save session file if it exists
if [ -d "/tmp/pi-sessions/${INSTANCE_ID}" ]; then
    cp "/tmp/pi-sessions/${INSTANCE_ID}/session.jsonl" "${OUTPUT_DIR}/session.jsonl" 2>/dev/null || true
fi

# Extract patch via git diff (from inside the repo)
# Must stage new/untracked files first — git diff alone drops them
echo "  Extracting patch..."
git add -A 2>/dev/null || true
git diff --cached > "${OUTPUT_DIR}/patch.diff" 2>/dev/null || {
    echo "  WARNING: git diff failed"
    touch "${OUTPUT_DIR}/patch.diff"
}

PATCH_SIZE=$(wc -c < "${OUTPUT_DIR}/patch.diff" 2>/dev/null || echo 0)

if [ "$PATCH_SIZE" -eq 0 ]; then
    echo "  WARNING: No patch generated (0 bytes)"
    echo '{"status": "no_patch", "patch_bytes": 0}' > "${OUTPUT_DIR}/result.json"
else
    echo "  Patch collected (${PATCH_SIZE} bytes)."
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    echo "{\"status\": \"patch_collected\", \"patch_bytes\": ${PATCH_SIZE}, \"elapsed_seconds\": ${ELAPSED}}" > "${OUTPUT_DIR}/result.json"
fi

echo "  Output: ${OUTPUT_DIR}/"
