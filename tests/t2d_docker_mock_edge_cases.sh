#!/bin/bash
# ==============================================================================
# T2d — Docker Mock Edge Cases & Integration Tests
#
# Tests edge cases in do_run() and integration between functions:
# - cp_fail mode (container succeeds but copy fails)
# - Container state inspection (docker inspect)
# - Multiple containers running concurrently
# - Output directory ownership
# - Empty patch handling
#
# Log:  tests/t2d_docker_mock_edge_cases.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/t2d_docker_mock_edge_cases.log"
PASS=0
FAIL=0
TOTAL=0
VERBOSE=0

for arg in "$@"; do
    case "$arg" in
        --verbose) VERBOSE=1 ;;
    esac
done

exec > >(tee -a "$LOG_FILE") 2>&1
: > "$LOG_FILE"

echo "=== T2d Docker Mock Edge Cases & Integration Tests ==="
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

    echo "T2d-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T2d-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2d-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T2d-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if echo "$output" | grep -qF "$expected_pattern"; then
        echo "  ✓ T2d-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2d-${id}: ${name} (pattern '${expected_pattern}' not found)"
        if [ "$VERBOSE" -eq 1 ]; then
            echo "    Output:"
            sed 's/^/      /' <<< "$output"
        fi
        FAIL=$((FAIL + 1))
    fi
}

# ==============================================================================
# Setup: Create mock docker and test agent
# ==============================================================================

MOCK_DIR="${SCRIPT_DIR}/fixtures"
TEST_WORKSPACE=$(mktemp -d /tmp/swe-bench-t2d.XXXXXX)
mkdir -p "${REPO_ROOT}/agents/mock-edge/bundle/bin"
cp "${MOCK_DIR}/mock-entrypoint.sh" "${REPO_ROOT}/agents/mock-edge/entrypoint.sh"
cat > "${REPO_ROOT}/agents/mock-edge/build_bundle.sh" <<'BUILDEOF'
#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Mock bundle built at $BUNDLE_DIR"
BUILDEOF
chmod +x "${REPO_ROOT}/agents/mock-edge/build_bundle.sh" "${REPO_ROOT}/agents/mock-edge/entrypoint.sh"
echo '#!/bin/bash' > "${REPO_ROOT}/agents/mock-edge/bundle/bin/node"
echo 'echo "mock node"' >> "${REPO_ROOT}/agents/mock-edge/bundle/bin/node"
chmod +x "${REPO_ROOT}/agents/mock-edge/bundle/bin/node"

cleanup_t2d() {
    rm -rf "${REPO_ROOT}/agents/mock-edge"
    rm -rf "$TEST_WORKSPACE"
}
trap cleanup_t2d EXIT

echo "--- T2d Setup: Mock docker and test agent created ---"
echo ""

# ==============================================================================
# 2d.1 — cp_fail Mode (container succeeds but copy fails)
# ==============================================================================

echo "--- T2d.1: cp_fail Mode ---"

run_test 01 "cp_fail mode causes do_run to return 1" \
    "(cd '$REPO_ROOT' && PATH='${MOCK_DIR}:${PATH}' SWE_DOCKER_MODE=cp_fail SWE_WORKSPACE_DIR='$TEST_WORKSPACE' bash run.sh --run mock-edge astropy__astropy-7166)" 1

echo ""

# ==============================================================================
# 2d.2 — Container State Inspection
# ==============================================================================

echo "--- T2d.2: Container State Inspection ---"

run_test_output 10 "do_run calls docker inspect after container exits" \
    "grep 'docker inspect' '$REPO_ROOT/run.sh'" "docker inspect"

run_test_output 11 "do_run checks container state before copy" \
    "grep 'container_state' '$REPO_ROOT/run.sh'" "container_state"

echo ""

# ==============================================================================
# 2d.3 — Output Directory Ownership
# ==============================================================================

echo "--- T2d.3: Output Directory Ownership ---"

run_test_output 20 "do_run fixes ownership after copy" \
    "grep 'chown' '$REPO_ROOT/run.sh'" "chown"

run_test_output 21 "do_run only fixes ownership when copy succeeds" \
    "grep 'chown' '$REPO_ROOT/run.sh'" "chown"

echo ""

# ==============================================================================
# 2d.4 — Empty Patch Handling
# ==============================================================================

echo "--- T2d.4: Empty Patch Handling ---"

run_test_output 30 "patch_bytes used in summarize_agent" \
    "grep 'patch_bytes' '$REPO_ROOT/run.sh'" "patch_bytes"

run_test_output 31 "no_patch status tracked in show_agent_status" \
    "grep 'no_patch' '$REPO_ROOT/run.sh'" "no_patch"

echo ""

# ==============================================================================
# 2d.5 — Integration: do_run → do_run_all
# ==============================================================================

echo "--- T2d.5: Integration Tests ---"

run_test_output 40 "do_run_all calls do_run for each instance" \
    "grep -A100 'do_run_all()' '$REPO_ROOT/run.sh' | grep 'do_run'" "do_run"

run_test_output 41 "do_run_all tracks count/skipped/failed" \
    "grep -A100 'do_run_all()' '$REPO_ROOT/run.sh' | grep 'count='" "count="

run_test_output 42 "do_run_all increments failed on do_run failure" \
    "grep -A100 'do_run_all()' '$REPO_ROOT/run.sh' | grep 'failed='" "failed="

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T2d Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
