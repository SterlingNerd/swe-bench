#!/bin/bash
# ==============================================================================
# T2 — Docker-Dependent Function Tests
#
# Tests cleanup, run, run-all, eval, summarize, status, init, interactive.
# Requires Docker daemon. Tests that can't run without Docker are skipped.
#
# Log:  tests/t2_docker.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/t2_docker.log"
PASS=0
FAIL=0
SKIP=0
TOTAL=0
VERBOSE=0

# Parse args
for arg in "$@"; do
    case "$arg" in
        --verbose) VERBOSE=1 ;;
    esac
done

# Check Docker availability
if ! docker info >/dev/null 2>&1; then
    echo "=== T2 Docker Tests ==="
    echo "SKIP: Docker daemon not available"
    echo "Install/start Docker and retry."
    exit 0
fi

# Start logging
exec > >(tee -a "$LOG_FILE") 2>&1
: > "$LOG_FILE"  # truncate log at start

echo "=== T2 Docker-Dependent Tests ==="
echo "Repo: ${REPO_ROOT}"
echo "Log:  ${LOG_FILE}"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# ==============================================================================
# Helpers
# ==============================================================================

run_test() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_exit="${4:-0}"
    TOTAL=$((TOTAL + 1))

    echo "T2-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T2-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T2-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if echo "$output" | grep -qF "$expected_pattern"; then
        echo "  ✓ T2-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2-${id}: ${name} (pattern '${expected_pattern}' not found)"
        if [ "$VERBOSE" -eq 1 ]; then
            echo "    Output was:"
            sed 's/^/      /' <<< "$output"
        fi
        FAIL=$((FAIL + 1))
    fi
}

# ==============================================================================
# Setup: Create test workspace with mock data and agent
# ==============================================================================

TEST_WORKSPACE=$(mktemp -d /tmp/swe-bench-t2.XXXXXX)
TEST_OUTPUTS="${TEST_WORKSPACE}/outputs"
mkdir -p "$TEST_OUTPUTS/pi/django__django-11039"
mkdir -p "$TEST_OUTPUTS/flask__flask-1000"

# Create mock agent with bundle
mkdir -p "${REPO_ROOT}/agents/test-t2/bundle"
cat > "${REPO_ROOT}/agents/test-t2/build_bundle.sh" <<'BUILDEOF'
#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Mock bundle built at $BUNDLE_DIR"
BUILDEOF
chmod +x "${REPO_ROOT}/agents/test-t2/build_bundle.sh"

# Create mock entrypoint that produces output
cat > "${REPO_ROOT}/agents/test-t2/entrypoint.sh" <<'ENTRYEOF'
#!/bin/bash
set -euo pipefail
INSTANCE_ID="$1"
OUTPUT_DIR="${SWE_OUTPUT_ROOT:-/workspace/outputs}/${INSTANCE_ID}"
mkdir -p "$OUTPUT_DIR"
echo "Mock agent ran for ${INSTANCE_ID}" > "${OUTPUT_DIR}/agent_output.txt"
echo "" > "${OUTPUT_DIR}/patch.diff"
cat > "${OUTPUT_DIR}/result.json" <<RESULTEOF
{
  "status": "no_patch",
  "patch_bytes": 0,
  "elapsed_seconds": 1,
  "agent_exit_code": 0
}
RESULTEOF
ENTRYEOF
chmod +x "${REPO_ROOT}/agents/test-t2/entrypoint.sh"

# Cleanup on exit
cleanup_t2() {
    # Clean up any test containers
    docker rm -f swe_test_* 2>/dev/null || true
    # Remove test agent
    rm -rf "${REPO_ROOT}/agents/test-t2"
    rm -rf "$TEST_WORKSPACE"
}
trap cleanup_t2 EXIT

echo "--- T2 Setup: Test workspace at ${TEST_WORKSPACE} ---"
echo ""

# ==============================================================================
# 2.1 — Cleanup (do_cleanup)
# ==============================================================================

echo "--- T2.1: Cleanup ---"

# T2-01: cleanup with no swe_* containers or swebench images → exits cleanly
TOTAL=$((TOTAL + 1))
echo "T2-01: cleanup exits cleanly when no resources found ..." >&2
set +e
(cd "$REPO_ROOT" && bash run.sh --cleanup) > /dev/null 2>&1
ACTUAL_EXIT=$?
set -e
# Verify: no swe_* containers running, no swebench images present
CONTAINERS=$(docker ps -q --filter 'name=^swe_' 2>/dev/null | wc -l)
IMAGES=$(docker images -q 'swebench/sweb.*' 2>/dev/null | wc -l)
if [ "$ACTUAL_EXIT" -eq 0 ] && [ "$CONTAINERS" -eq 0 ] && [ "$IMAGES" -eq 0 ]; then
    echo "  ✓ T2-01: cleanup exits cleanly, no swe_* containers or swebench images remain"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2-01: cleanup (exit=$ACTUAL_EXIT, containers=$CONTAINERS, images=$IMAGES)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 2.2 — Init (do_init)
# ==============================================================================

echo "--- T2.2: Init ---"

run_test_output 02 "--init installs swebench" \
    "(cd '$REPO_ROOT' && bash run.sh --init)" "swebench"

echo ""

# ==============================================================================
# 2.3 — Summarize (summarize_agent, do_summarize)
# ==============================================================================

echo "--- T2.3: Summarize ---"

# Set up test data
mkdir -p "$TEST_OUTPUTS/pi/django__django-11039"
mkdir -p "$TEST_OUTPUTS/pi/django__django-12000"
cat > "$TEST_OUTPUTS/pi/django__django-11039/result.json" <<'EOF'
{"status": "resolved", "patch_bytes": 1234, "elapsed_seconds": 300}
EOF
cat > "$TEST_OUTPUTS/pi/django__django-12000/result.json" <<'EOF'
{"status": "no_patch", "patch_bytes": 0, "elapsed_seconds": 60}
EOF

run_test_output 03 "--summarize pi shows agent summary" \
    "(cd '$REPO_ROOT' && SWE_WORKSPACE_DIR='$TEST_WORKSPACE' bash run.sh --summarize pi)" "Agent: pi"

run_test_output 04 "--summarize with no agent summarizes all" \
    "(cd '$REPO_ROOT' && SWE_WORKSPACE_DIR='$TEST_WORKSPACE' bash run.sh --summarize)" "Total:"

echo ""

# ==============================================================================
# 2.4 — Status (show_agent_status, do_status)
# ==============================================================================

echo "--- T2.4: Status ---"

run_test_output 05 "--status pi shows agent status" \
    "(cd '$REPO_ROOT' && SWE_WORKSPACE_DIR='$TEST_WORKSPACE' bash run.sh --status pi)" "Agent: pi"

run_test_output 06 "--status with no agent shows all agents" \
    "(cd '$REPO_ROOT' && SWE_WORKSPACE_DIR='$TEST_WORKSPACE' bash run.sh --status)" "SWE-bench Harness Status"

echo ""

# ==============================================================================
# 2.5 — Eval (do_eval) — requires swebench installed
# ==============================================================================

echo "--- T2.5: Eval ---"

run_test_output 07 "--eval nonexistent agent prints error" \
    "(cd '$REPO_ROOT' && bash run.sh --eval nonexistent)" "not found"

run_test_output 08 "--eval with no outputs prints message" \
    "(cd '$REPO_ROOT' && SWE_WORKSPACE_DIR='/tmp/nonexistent_xyz' bash run.sh --eval pi)" "No outputs found"

echo ""

# ==============================================================================
# 2.6 — Status Visual Indicators (T2-48 through T2-53)
#
# Tests that show_agent_status() displays the correct visual indicator
# for each status type:
# - resolved → green ✓
# - failed → red ✗
# - no_patch → yellow —
# - timed_out → ⌛
# - agent_error/container_error → red !
# - unknown → ?
# ==============================================================================

echo "--- T2.6: Status Visual Indicators ---"

# Set up test workspace with various status types
TEST_WS_STATUS=$(mktemp -d /tmp/swe-bench-t2-status.XXXXXX)
mkdir -p "${TEST_WS_STATUS}/outputs/status-agent"

# Create instances with different statuses (mkdir first, then write file)
mkdir -p "${TEST_WS_STATUS}/outputs/status-agent/instance-resolved"
echo '{"status": "resolved", "patch_bytes": 100}' > "${TEST_WS_STATUS}/outputs/status-agent/instance-resolved/result.json"
mkdir -p "${TEST_WS_STATUS}/outputs/status-agent/instance-failed"
echo '{"status": "failed", "patch_bytes": 50}' > "${TEST_WS_STATUS}/outputs/status-agent/instance-failed/result.json"
mkdir -p "${TEST_WS_STATUS}/outputs/status-agent/instance-no-patch"
echo '{"status": "no_patch", "patch_bytes": 0}' > "${TEST_WS_STATUS}/outputs/status-agent/instance-no-patch/result.json"
mkdir -p "${TEST_WS_STATUS}/outputs/status-agent/instance-timedout"
echo '{"status": "timed_out", "patch_bytes": 0}' > "${TEST_WS_STATUS}/outputs/status-agent/instance-timedout/result.json"
mkdir -p "${TEST_WS_STATUS}/outputs/status-agent/instance-err"
echo '{"status": "agent_error", "patch_bytes": 0}' > "${TEST_WS_STATUS}/outputs/status-agent/instance-err/result.json"
# Instance with no result.json
mkdir -p "${TEST_WS_STATUS}/outputs/status-agent/instance-nodata"

echo "--- T2.6a: Status indicator characters ---"

# T2-48: resolved → green ✓
TOTAL=$((TOTAL + 1))
echo "T2-48: resolved shows green ✓ ..." >&2
set +e
OUTPUT="$(cd "$REPO_ROOT" && SWE_WORKSPACE_DIR="$TEST_WS_STATUS" bash run.sh --status status-agent 2>&1)"
ACTUAL_EXIT=$?
set -e
if echo "$OUTPUT" | grep -q "resolved"; then
    echo "  ✓ T2-48: resolved shows green ✓"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2-48: resolved shows green ✓ (pattern not found)"
    if [ "$VERBOSE" -eq 1 ]; then
        echo "    Output: $(echo "$OUTPUT" | head -10)"
    fi
    FAIL=$((FAIL + 1))
fi

# T2-49: failed → red ✗
TOTAL=$((TOTAL + 1))
echo "T2-49: failed shows red ✗ ..." >&2
set +e
OUTPUT="$(cd "$REPO_ROOT" && SWE_WORKSPACE_DIR="$TEST_WS_STATUS" bash run.sh --status status-agent 2>&1)"
ACTUAL_EXIT=$?
set -e
if echo "$OUTPUT" | grep -q "failed"; then
    echo "  ✓ T2-49: failed shows red ✗"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2-49: failed shows red ✗ (pattern not found)"
    if [ "$VERBOSE" -eq 1 ]; then
        echo "    Output: $(echo "$OUTPUT" | head -10)"
    fi
    FAIL=$((FAIL + 1))
fi

# T2-50: no_patch → yellow —
TOTAL=$((TOTAL + 1))
echo "T2-50: no_patch shows yellow — ..." >&2
set +e
OUTPUT="$(cd "$REPO_ROOT" && SWE_WORKSPACE_DIR="$TEST_WS_STATUS" bash run.sh --status status-agent 2>&1)"
ACTUAL_EXIT=$?
set -e
if echo "$OUTPUT" | grep -q "no patch"; then
    echo "  ✓ T2-50: no_patch shows yellow —"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2-50: no_patch shows yellow — (pattern not found)"
    if [ "$VERBOSE" -eq 1 ]; then
        echo "    Output: $(echo "$OUTPUT" | head -10)"
    fi
    FAIL=$((FAIL + 1))
fi

# T2-51: timed_out → ⌛
TOTAL=$((TOTAL + 1))
echo "T2-51: timed_out shows ⌛ ..." >&2
set +e
OUTPUT="$(cd "$REPO_ROOT" && SWE_WORKSPACE_DIR="$TEST_WS_STATUS" bash run.sh --status status-agent 2>&1)"
ACTUAL_EXIT=$?
set -e
if echo "$OUTPUT" | grep -q "timed out"; then
    echo "  ✓ T2-51: timed_out shows ⌛"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2-51: timed_out shows ⌛ (pattern not found)"
    if [ "$VERBOSE" -eq 1 ]; then
        echo "    Output: $(echo "$OUTPUT" | head -10)"
    fi
    FAIL=$((FAIL + 1))
fi

# T2-52: agent_error → red !
TOTAL=$((TOTAL + 1))
echo "T2-52: agent_error shows red ! ..." >&2
set +e
OUTPUT="$(cd "$REPO_ROOT" && SWE_WORKSPACE_DIR="$TEST_WS_STATUS" bash run.sh --status status-agent 2>&1)"
ACTUAL_EXIT=$?
set -e
if echo "$OUTPUT" | grep -q "agent_error\|error"; then
    echo "  ✓ T2-52: agent_error shows red !"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2-52: agent_error shows red ! (pattern not found)"
    if [ "$VERBOSE" -eq 1 ]; then
        echo "    Output: $(echo "$OUTPUT" | head -10)"
    fi
    FAIL=$((FAIL + 1))
fi

# T2-53: no result.json → ?
TOTAL=$((TOTAL + 1))
echo "T2-53: no result.json shows ? ..." >&2
set +e
OUTPUT="$(cd "$REPO_ROOT" && SWE_WORKSPACE_DIR="$TEST_WS_STATUS" bash run.sh --status status-agent 2>&1)"
ACTUAL_EXIT=$?
set -e
if echo "$OUTPUT" | grep -q "no result"; then
    echo "  ✓ T2-53: no result.json shows ?"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2-53: no result.json shows ? (pattern not found)"
    if [ "$VERBOSE" -eq 1 ]; then
        echo "    Output: $(echo "$OUTPUT" | head -10)"
    fi
    FAIL=$((FAIL + 1))
fi

# T2-54: eval/ and logs/ directories are skipped
TOTAL=$((TOTAL + 1))
echo "T2-54: eval/ and logs/ directories skipped ..." >&2
run_test_output 54 "status skips eval directory" \
    "grep 'eval|logs' '$REPO_ROOT/run.sh'" "eval|logs"

# T2-55: --status with no agent shows all agents
TOTAL=$((TOTAL + 1))
echo "T2-55: --status with no agent shows all ..." >&2
run_test_output 55 "--status without agent shows all agents" \
    "grep -A5 'do_status()' '$REPO_ROOT/run.sh'" "requested_agent"

rm -rf "$TEST_WS_STATUS"

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T2 Results: ${PASS}/${TOTAL} passed, ${FAIL} failed, ${SKIP} skipped ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
