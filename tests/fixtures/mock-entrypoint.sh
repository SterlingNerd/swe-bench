#!/bin/bash
# ==============================================================================
# Mock Agent Entrypoint — minimal agent for testing run.sh
#
# Simulates an agent that:
# 1. Verifies mount points are correct and writable
# 2. Writes result.json, patch.diff, and other expected outputs
# 3. Optionally simulates timeout or error based on env vars
# ==============================================================================

set -euo pipefail

INSTANCE_ID="${1:?Usage: mock-entrypoint.sh <instance_id> <repo_url> <base_commit> <problem_statement>}"
REPO_URL="${2:-}"
BASE_COMMIT="${3:-}"
PROBLEM_STATEMENT="${4:-}"

OUTPUT_DIR="${SWE_OUTPUT_ROOT:-/workspace/outputs}/${INSTANCE_ID}"
mkdir -p "$OUTPUT_DIR"

echo "=== Mock Agent Entrypoint ==="
echo "Instance: ${INSTANCE_ID}"
echo "Output dir: ${OUTPUT_DIR}"
echo ""

# Verify mounts are writable
echo "--- Verifying mounts ---"
if [ -d "/agent" ]; then
    echo "  /agent exists (bundle mounted)"
    ls -la /agent/ 2>/dev/null || true
else
    echo "  WARNING: /agent not found"
fi

if [ -d "${OUTPUT_DIR}" ]; then
    echo "  ${OUTPUT_DIR} is writable"
    touch "${OUTPUT_DIR}/.writable_test" && rm "${OUTPUT_DIR}//.writable_test"
    echo "  Write test passed"
else
    echo "  ERROR: ${OUTPUT_DIR} not found"
    exit 1
fi

echo ""

# Simulate timeout if requested
if [ "${MOCK_TIMEOUT:-0}" -eq 1 ]; then
    echo "Simulating timeout..."
    sleep 2
    # Write partial result
    cat > "${OUTPUT_DIR}/result.json" <<EOF
{
  "status": "timed_out",
  "patch_bytes": 0,
  "elapsed_seconds": 3600,
  "agent_exit_code": 124
}
EOF
    : > "${OUTPUT_DIR}/patch.diff"
    exit 124
fi

# Simulate error if requested
if [ "${MOCK_ERROR:-0}" -eq 1 ]; then
    echo "Simulating agent error..."
    cat > "${OUTPUT_DIR}/result.json" <<EOF
{
  "status": "agent_error",
  "patch_bytes": 0,
  "elapsed_seconds": 10,
  "agent_exit_code": 1
}
EOF
    : > "${OUTPUT_DIR}/patch.diff"
    exit 1
fi

# Normal successful run
echo "--- Generating outputs ---"

# Write result.json
cat > "${OUTPUT_DIR}/result.json" <<EOF
{
  "status": "patch_collected",
  "patch_bytes": 42,
  "elapsed_seconds": 5,
  "agent_exit_code": 0
}
EOF

# Write patch.diff
cat > "${OUTPUT_DIR}/patch.diff" <<'EOF'
diff --git a/test.py b/test.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/test.py
@@ -0,0 +1 @@
+print('hello world')
EOF

# Write agent output
cat > "${OUTPUT_DIR}/agent_output.txt" <<EOF
Mock agent ran for ${INSTANCE_ID}
Problem: ${PROBLEM_STATEMENT:0:50}...
Generated patch with 42 bytes.
EOF

# Write meta.json
cat > "${OUTPUT_DIR}/meta.json" <<EOF
{
  "instance_id": "${INSTANCE_ID}",
  "agent": "${SWE_AGENT_NAME:-mock}",
  "model": "test-model",
  "provider": "local"
}
EOF

# Create pi-sessions directory if pi agent
if [ "${SWE_AGENT_NAME:-}" = "pi" ]; then
    mkdir -p "${OUTPUT_DIR}/pi-sessions"
    cat > "${OUTPUT_DIR}/pi-sessions/session.json" <<EOF
{
  "instance_id": "${INSTANCE_ID}",
  "turns": 1,
  "status": "completed"
}
EOF
fi

echo ""
echo "--- Output files ---"
ls -la "${OUTPUT_DIR}/"
echo ""
echo "Mock agent completed successfully."
exit 0
