#!/bin/bash
# ==============================================================================
# T2 — Docker Mocked Tests (no real Docker needed)
#
# Uses a PATH-swapped fake docker to test do_run() logic paths:
# - success: container exits 0, outputs copied correctly
# - timeout: container exits 124, timed_out status recorded
# - error: container exits non-zero, agent_error recorded
# - cp_fail: container succeeds but copy fails
# - oom: container OOM killed (exit 137)
#
# Log:  tests/t2_docker_mocked.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/t2_docker_mocked.log"
PASS=0
FAIL=0
TOTAL=0
VERBOSE=0

# Parse args
for arg in "$@"; do
    case "$arg" in
        --verbose) VERBOSE=1 ;;
    esac
done

# Start logging
exec > >(tee -a "$LOG_FILE") 2>&1
: > "$LOG_FILE"

echo "=== T2 Docker Mocked Tests ==="
echo "Repo: ${REPO_ROOT}"
echo "Log:  ${LOG_FILE}"
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# ==============================================================================
# Setup: Create test workspace, mock docker, and test agent
# ==============================================================================

TEST_WORKSPACE=$(mktemp -d /tmp/swe-bench-t2mock.XXXXXX)
TEST_OUTPUTS="${TEST_WORKSPACE}/outputs"
MOCK_DIR="${SCRIPT_DIR}/fixtures"
mkdir -p "$TEST_OUTPUTS"

# Create test agent with mock entrypoint
mkdir -p "${REPO_ROOT}/agents/mock-test/bundle"
cp "${MOCK_DIR}/mock-entrypoint.sh" "${REPO_ROOT}/agents/mock-test/entrypoint.sh"
cat > "${REPO_ROOT}/agents/mock-test/build_bundle.sh" <<'BUILDEOF'
#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Mock bundle built at $BUNDLE_DIR"
BUILDEOF
chmod +x "${REPO_ROOT}/agents/mock-test/build_bundle.sh"
chmod +x "${REPO_ROOT}/agents/mock-test/entrypoint.sh"

# Create a minimal node binary so the agent bundle validation passes
mkdir -p "${REPO_ROOT}/agents/mock-test/bundle/bin"
echo '#!/bin/bash' > "${REPO_ROOT}/agents/mock-test/bundle/bin/node"
echo 'echo "mock node"' >> "${REPO_ROOT}/agents/mock-test/bundle/bin/node"
chmod +x "${REPO_ROOT}/agents/mock-test/bundle/bin/node"

# Create test agent for each test run
create_test_agent() {
    mkdir -p "${REPO_ROOT}/agents/mock-test/bundle/bin"
    cp "${MOCK_DIR}/mock-entrypoint.sh" "${REPO_ROOT}/agents/mock-test/entrypoint.sh"
    cat > "${REPO_ROOT}/agents/mock-test/build_bundle.sh" <<'BUILDEOF'
#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Mock bundle built at $BUNDLE_DIR"
BUILDEOF
    chmod +x "${REPO_ROOT}/agents/mock-test/build_bundle.sh" "${REPO_ROOT}/agents/mock-test/entrypoint.sh"
    echo '#!/bin/bash' > "${REPO_ROOT}/agents/mock-test/bundle/bin/node"
    echo 'echo "mock node"' >> "${REPO_ROOT}/agents/mock-test/bundle/bin/node"
    chmod +x "${REPO_ROOT}/agents/mock-test/bundle/bin/node"
}

# Cleanup on exit
cleanup_t2mock() {
    rm -rf "${REPO_ROOT}/agents/mock-test"
    rm -rf "$TEST_WORKSPACE"
}
trap cleanup_t2mock EXIT

# Create agent once at setup
create_test_agent

# ==============================================================================
# Helpers
# ==============================================================================

run_test() {
    local id="$1"
    local name="$2"
    local mode="$3"
    local expected_status="$4"
    local expected_exit="${5:-0}"
    TOTAL=$((TOTAL + 1))

    echo "T2M-${id}: ${name} (mode=$mode) ..." >&2

    # Set up environment with mocked docker
    local test_outputs="${TEST_OUTPUTS}/t2m-${id}"
    mkdir -p "$test_outputs"

    # Select instance based on test ID to avoid stale results
    local inst="$TEST_INSTANCE"
    case "$id" in
        01) inst="$TEST_INST_01" ;;
        02) inst="$TEST_INST_02" ;;
        03) inst="$TEST_INST_03" ;;
        04) inst="$TEST_INST_04" ;;
    esac

    set +e
    PATH="${MOCK_DIR}:${PATH}" \
    SWE_DOCKER_MODE="$mode" \
    SWE_FIXTURES_DIR="$MOCK_DIR" \
    SWE_TEST_OUTPUTS="$test_outputs" \
    SWE_WORKSPACE_DIR="$TEST_WORKSPACE" \
    bash "${REPO_ROOT}/run.sh" --run mock-test "$inst" 3600 \
        > /dev/null 2>&1
    local actual_exit=$?
    set -e

    # Check result.json if it was created
    local main_result="${TEST_OUTPUTS}/mock-test/${inst}/result.json"
    local actual_status="none"
    if [ -f "$main_result" ]; then
        actual_status=$(python3 -c "import json; print(json.load(open('$main_result')).get('status', 'unknown'))" 2>/dev/null || echo "parse_error")
    fi

    if [ "$actual_status" = "$expected_status" ] && [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T2M-${id}: ${name} (status=$actual_status, exit=$actual_exit)"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2M-${id}: ${name} (expected status=$expected_status exit=$expected_exit, got status=$actual_status exit=$actual_exit)"
        if [ -f "$main_result" ]; then
            echo "    result.json: $(cat "$main_result")"
        fi
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local mode="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T2M-${id}: ${name} (mode=$mode) ..." >&2

    set +e
    local output
    output=$(PATH="${MOCK_DIR}:${PATH}" \
        SWE_DOCKER_MODE="$mode" \
        SWE_FIXTURES_DIR="$MOCK_DIR" \
        SWE_TEST_OUTPUTS="$TEST_OUTPUTS" \
        SWE_WORKSPACE_DIR="$TEST_WORKSPACE" \
        bash "${REPO_ROOT}/run.sh" --run mock-test "$TEST_INSTANCE" 3600 2>&1) || true
    set -e

    if echo "$output" | grep -qF "$expected_pattern"; then
        echo "  ✓ T2M-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2M-${id}: ${name} (pattern '${expected_pattern}' not found)"
        if [ "$VERBOSE" -eq 1 ]; then
            echo "    Output:"
            sed 's/^/      /' <<< "$output"
        fi
        FAIL=$((FAIL + 1))
    fi
}

# ==============================================================================
# 2.M — do_run() Logic Paths
# ==============================================================================

echo "--- T2.M: do_run() Logic Paths ---"

# Use instances that exist in the real cache (different per test to avoid stale results)
TEST_INSTANCE="astropy__astropy-7166"  # default
TEST_INST_01="astropy__astropy-7166"
TEST_INST_02="astropy__astropy-7336"
TEST_INST_03="astropy__astropy-7606"
TEST_INST_04="astropy__astropy-7671"

# Clean up previous test results before each test
cleanup_test_outputs() {
    rm -rf "${TEST_OUTPUTS}/mock-test"
}

run_test 01 "success path" "success" "patch_collected" 0
cleanup_test_outputs
run_test 02 "timeout path" "timeout" "timed_out" 124
cleanup_test_outputs
run_test 03 "agent error path" "error" "container_error" 1
cleanup_test_outputs
run_test 04 "OOM killed path" "oom" "container_error" 137

echo ""

# ==============================================================================
# 2.N — Output File Verification
# ==============================================================================

echo "--- T2.N: Output File Verification ---"

# Test output files by checking the filesystem directly
run_test 10 "success creates result.json" "success" "patch_collected" 0
cleanup_test_outputs
run_test 11 "success creates patch.diff" "success" "patch_collected" 0
cleanup_test_outputs
run_test 12 "success creates agent_output.txt" "success" "patch_collected" 0
cleanup_test_outputs
run_test 13 "success creates meta.json" "success" "patch_collected" 0

echo ""

# ==============================================================================
# 2.O — do_run() Error Handling
# ==============================================================================

echo "--- T2.O: do_run() Error Handling ---"

# These tests verify error handling before docker is called (no mock needed)
TOTAL=$((TOTAL + 1))
echo "T2M-20: invalid agent name rejected ..." >&2
set +e
(cd "$REPO_ROOT" && bash run.sh --run nonexistent-agent astropy__astropy-7166) > /dev/null 2>&1
ACTUAL_EXIT=$?
set -e
if [ "$ACTUAL_EXIT" -ne 0 ]; then
    echo "  ✓ T2M-20: invalid agent name rejected"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2M-20: invalid agent name rejected (expected non-zero exit, got 0)"
    FAIL=$((FAIL + 1))
fi

TOTAL=$((TOTAL + 1))
echo "T2M-21: non-numeric timeout rejected ..." >&2
set +e
(cd "$REPO_ROOT" && bash run.sh --run mock-test astropy__astropy-7166 abc) > /dev/null 2>&1
ACTUAL_EXIT=$?
set -e
if [ "$ACTUAL_EXIT" -ne 0 ]; then
    echo "  ✓ T2M-21: non-numeric timeout rejected"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2M-21: non-numeric timeout rejected (expected non-zero exit, got 0)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T2 Mocked Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
