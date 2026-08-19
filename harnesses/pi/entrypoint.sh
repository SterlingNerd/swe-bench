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

# --- Fix permissions for /testbed (owned by root in image) ---
HOST_UID="${HOST_UID:-$(id -u)}"
HOST_GID="${HOST_GID:-$(id -g)}"
echo "DEBUG: HOST_UID='${HOST_UID}', HOST_GID='${HOST_GID}', id -u=$(id -u)"
if [ "$(id -u)" = "0" ]; then
    # Running as root: use existing nonroot user (UID 1000) or create one matching host
    if [ "${HOST_UID}" = "1000" ] && [ "${HOST_GID}" = "1000" ]; then
        RUN_USER="nonroot"
    else
        RUN_USER="hostuser"
        if ! id -u "${RUN_USER}" >/dev/null 2>&1; then
            groupadd -g "${HOST_GID}" "${RUN_USER}" 2>/dev/null || true
            useradd -u "${HOST_UID}" -g "${HOST_GID}" -m -s /bin/bash "${RUN_USER}" 2>/dev/null || true
        fi
    fi
    # Clean up previous run's output directory to avoid permission issues
    # NOTE: Runner creates OUTPUT_DIR with correct ownership, so we don't rm -rf it
    # Only chown what we need inside the container
    chown -R "${HOST_UID}:${HOST_GID}" /testbed 2>/dev/null || true
    # Fix config dir permissions for agent
    chown -R "${HOST_UID}:${HOST_GID}" "${PI_CONFIG_DIR}" 2>/dev/null || true
    # Create output directory structure (runner doesn't mount outputs volume)
    mkdir -p "${OUTPUT_DIR}/eval" "${OUTPUT_DIR}/pi-sessions"
    chown -R "${HOST_UID}:${HOST_GID}" "${OUTPUT_DIR}" 2>/dev/null || true
fi

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

# Save problem metadata (use python3 for proper JSON escaping)
cat > /tmp/meta.py << 'PYEOF'
import json, sys
meta = {
    'instance_id': sys.argv[1],
    'repo_url': sys.argv[2],
    'base_commit': sys.argv[3],
    'agent': sys.argv[5]
}
json.dump(meta, open(sys.argv[4], 'w'))
PYEOF
python3 /tmp/meta.py "${INSTANCE_ID}" "${REPO_URL}" "${BASE_COMMIT}" "${OUTPUT_DIR}/meta.json" "${SWE_AGENT_NAME:-pi}"
echo "${PROBLEM_STATEMENT}" > "${OUTPUT_DIR}/problem_statement.txt"

# Use swebench's /testbed (repo already at base commit)
REPO_DIR="/testbed"
cd "$REPO_DIR" || { echo "ERROR: Cannot cd to $REPO_DIR"; exit 1; }

# --- Run agent ---
SESSION_DIR="${OUTPUT_DIR}/pi-sessions"
mkdir -p "${SESSION_DIR}"

# Run agent as host user (with cd to repo dir since runuser changes cwd)
if [ "$(id -u)" = "0" ]; then
    # Run agent as root directly (with --cap-drop ALL and --security-opt no-new-privileges, 
    # root in container is relatively safe due to user namespace remapping)
    # Write problem statement to a file to avoid shell escaping issues
    PROBLEM_FILE="/tmp/problem_statement.txt"
    printf "%s" "${PROBLEM_STATEMENT}" > "${PROBLEM_FILE}"
    chmod a+r "${PROBLEM_FILE}"
    # Use a subshell to properly handle stdin redirection for the pi command
    RUN_AS='(cd /testbed && exec pi -p --session-dir "${SESSION_DIR}" < /tmp/problem_statement.txt 2>&1)'
fi

echo "  Running agent in $REPO_DIR..."
START_TIME=$(date +%s)
AGENT_OUTPUT="${OUTPUT_DIR}/agent_output.txt"

set +e
eval "${RUN_AS}" | tee "${AGENT_OUTPUT}"
AGENT_EXIT_CODE=${PIPESTATUS[0]}
set -e
if [ "$AGENT_EXIT_CODE" -ne 0 ]; then
    echo "  WARNING: pi exited with status ${AGENT_EXIT_CODE}"
fi

# Extract patch via git diff (from inside the repo)
echo "  Extracting patch..."
cd /testbed || { echo "ERROR: cd failed"; pwd; exit 1; }
GIT_OPTS="--git-dir=/testbed/.git --work-tree=/testbed"

# Stage all changes first
git $GIT_OPTS add -A 2>/dev/null || true

# If there are staged changes but no commit, create one to ensure clean diff
if ! git $GIT_OPTS diff --quiet; then
    echo "  Committing agent changes..."
    git $GIT_OPTS -c user.name="swe-agent" -c user.email="swe-agent@swebench" \
        commit -m "Agent changes for ${INSTANCE_ID}" 2>/dev/null || true
fi

# Diff from base commit to current HEAD (includes agent commit if made)
if ! git $GIT_OPTS diff --binary "$BASE_COMMIT" > "${OUTPUT_DIR}/patch.diff" 2>/dev/null; then
    echo "  WARNING: git diff failed"
    touch "${OUTPUT_DIR}/patch.diff"
fi

echo "DEBUG: Before PATCH_SIZE calculation"
PATCH_SIZE=$(wc -c < "${OUTPUT_DIR}/patch.diff" 2>/dev/null || echo 0)
echo "DEBUG: PATCH_SIZE=$PATCH_SIZE"
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
    echo "  Agent completed without modifying files (0-byte patch)."
fi

# Write status files for runner to read (docker cp will copy them out)
echo "$STATUS" > "${OUTPUT_DIR}/.status"
echo "$PATCH_SIZE" > "${OUTPUT_DIR}/.patch_size"
echo "$ELAPSED" > "${OUTPUT_DIR}/.elapsed"
echo "$AGENT_EXIT_CODE" > "${OUTPUT_DIR}/.agent_exit_code"

echo "  Output: ${OUTPUT_DIR}/"
