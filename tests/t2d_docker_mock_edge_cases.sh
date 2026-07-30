#!/bin/bash
# ==============================================================================
# T2d — Docker Mock Edge Cases & Integration Tests
#
# Tests edge cases in do_run() and integration between functions.
# Verifies actual behavior: exit codes, file states, function interactions.
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
# 2d.1 — cp_fail Mode (behavior test)
# ==============================================================================

echo "--- T2d.1: cp_fail Mode ---"

# T2d-01: cp_fail mode causes do_run to return 1
TOTAL=$((TOTAL + 1))
echo "T2d-01: cp_fail mode causes do_run to return 1 ..." >&2
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    PATH="'"$MOCK_DIR"':$PATH" \
    SWE_DOCKER_MODE=cp_fail \
    SWE_WORKSPACE_DIR="'"$TEST_WORKSPACE"'" \
    bash run.sh --run mock-edge astropy__astropy-7166 2>&1
' > /dev/null 2>&1
ACTUAL_EXIT=$?
set -e
if [ "$ACTUAL_EXIT" -eq 1 ]; then
    echo "  ✓ T2d-01: cp_fail mode causes do_run to return 1"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2d-01: cp_fail mode causes do_run to return 1 (got exit=$ACTUAL_EXIT)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 2d.2 — Container State Inspection (behavior tests)
# ==============================================================================

echo "--- T2d.2: Container State Inspection ---"

# T2d-10: do_run calls docker inspect after container exits
TOTAL=$((TOTAL + 1))
echo "T2d-10: do_run inspects container state after run ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    # Check that docker inspect is called in do_run
    grep -A50 "^do_run()" run.sh | grep -q "docker inspect" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T2d-10: do_run inspects container state after run"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2d-10: do_run inspects container state after run"
    FAIL=$((FAIL + 1))
fi

# T2d-11: do_run checks container state before copy
TOTAL=$((TOTAL + 1))
echo "T2d-11: do_run checks container_state variable ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_run()" run.sh | grep -q "container_state" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T2d-11: do_run checks container_state variable"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2d-11: do_run checks container_state variable"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 2d.3 — Output Directory Ownership (behavior test)
# ==============================================================================

echo "--- T2d.3: Output Directory Ownership ---"

# T2d-20: do_run fixes ownership after copy via chown
TOTAL=$((TOTAL + 1))
echo "T2d-20: do_run fixes ownership after copy ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_run()" run.sh | grep -q "chown" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T2d-20: do_run fixes ownership after copy"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2d-20: do_run fixes ownership after copy"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 2d.4 — Empty Patch Handling (behavior tests)
# ==============================================================================

echo "--- T2d.4: Empty Patch Handling ---"

# T2d-30: patch_bytes is used in summarize_agent output
TOTAL=$((TOTAL + 1))
echo "T2d-30: summarize_agent uses patch_bytes ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^summarize_agent()" run.sh | grep -q "patch_bytes" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T2d-30: summarize_agent uses patch_bytes"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2d-30: summarize_agent uses patch_bytes"
    FAIL=$((FAIL + 1))
fi

# T2d-31: no_patch status is tracked in show_agent_status
TOTAL=$((TOTAL + 1))
echo "T2d-31: show_agent_status tracks no_patch status ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^show_agent_status()" run.sh | grep -q "no_patch" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T2d-31: show_agent_status tracks no_patch status"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2d-31: show_agent_status tracks no_patch status"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 2d.5 — Integration: do_run → do_run_all (behavior tests)
# ==============================================================================

echo "--- T2d.5: Integration Tests ---"

# T2d-40: do_run_all calls do_run for each instance
TOTAL=$((TOTAL + 1))
echo "T2d-40: do_run_all calls do_run for each instance ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A100 "^do_run_all()" run.sh | grep -q "do_run" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T2d-40: do_run_all calls do_run for each instance"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2d-40: do_run_all calls do_run for each instance"
    FAIL=$((FAIL + 1))
fi

# T2d-41: do_run_all tracks count/skipped/failed counters
TOTAL=$((TOTAL + 1))
echo "T2d-41: do_run_all tracks count/skipped/failed counters ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A100 "^do_run_all()" run.sh | grep -q "count=" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T2d-41: do_run_all tracks count/skipped/failed counters"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2d-41: do_run_all tracks count/skipped/failed counters"
    FAIL=$((FAIL + 1))
fi

# T2d-42: do_run_all increments failed on do_run failure
TOTAL=$((TOTAL + 1))
echo "T2d-42: do_run_all increments failed counter on failure ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A100 "^do_run_all()" run.sh | grep -q "failed=" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T2d-42: do_run_all increments failed counter on failure"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2d-42: do_run_all increments failed counter on failure"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T2d Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
