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
AGENT_CMD="pi -p --session-dir /tmp/pi-sessions --session-id '${INSTANCE_ID}'"

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
    exit 0
fi

# Evaluate using swebench harness
echo "  Evaluating patch (${PATCH_SIZE} bytes)..."

python3 -c "
import json
with open('${OUTPUT_DIR}/patch.diff', 'r') as f:
    patch = f.read()
patch = patch.replace('\\\\', '\\\\\\\\').replace('\"', '\\\\\"').replace('\n', '\\\\n')
pred = [{
    'instance_id': '${INSTANCE_ID}',
    'model_name_or_path': 'agent',
    'model_patch': patch
}]
with open('${OUTPUT_DIR}/eval/predictions.json', 'w') as f:
    json.dump(pred, f)
"

python3 -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Verified \
    --predictions_path "${OUTPUT_DIR}/eval/predictions.json" \
    --max_workers 1 \
    --namespace "" \
    2>&1 | tee "${OUTPUT_DIR}/eval/harness.log" || true

# Parse result and save
STATUS="unknown"
if grep -q '"resolved": true' "${OUTPUT_DIR}/eval/harness.log" 2>/dev/null; then
    STATUS="resolved"
elif grep -q '"no_test_changes"' "${OUTPUT_DIR}/eval/harness.log" 2>/dev/null; then
    STATUS="no_test_changes"
elif grep -q '"failed"' "${OUTPUT_DIR}/eval/harness.log" 2>/dev/null; then
    STATUS="failed"
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo "{\"status\": \"${STATUS}\", \"patch_bytes\": ${PATCH_SIZE}, \"elapsed_seconds\": ${ELAPSED}}" > "${OUTPUT_DIR}/result.json"
echo "  Result: ${STATUS} (${ELAPSED}s)"
echo "  Output: ${OUTPUT_DIR}/"
