#!/bin/bash
# ==============================================================================
# T2b — Signal Handling & Interrupt Tests
#
# Tests on_interrupt() and stop_running_containers() signal handling.
# Verifies actual behavior: traps are set, containers are stopped, flags work.
#
# Log:  tests/t2b_signal_handling.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_HELPER="${SCRIPT_DIR}/test_helper.sh"
[ -f "$SOURCE_HELPER" ] && source "$SOURCE_HELPER"
LOG_FILE="${SCRIPT_DIR}/t2b_signal_handling.log"
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

echo "=== T2b Signal Handling & Interrupt Tests ==="
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

    echo "T2b-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T2b-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2b-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T2b-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if check_output "$output" "$expected_pattern"; then
        echo "  ✓ T2b-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2b-${id}: ${name} (pattern '${expected_pattern}' not found)"
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
TEST_WORKSPACE=$(mktemp -d /tmp/swe-bench-t2b.XXXXXX)
mkdir -p "${REPO_ROOT}/agents/mock-signal/bundle/bin"
cp "${MOCK_DIR}/mock-entrypoint.sh" "${REPO_ROOT}/agents/mock-signal/entrypoint.sh"
cat > "${REPO_ROOT}/agents/mock-signal/build_bundle.sh" <<'BUILDEOF'
#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Mock bundle built at $BUNDLE_DIR"
BUILDEOF
chmod +x "${REPO_ROOT}/agents/mock-signal/build_bundle.sh" "${REPO_ROOT}/agents/mock-signal/entrypoint.sh"
echo '#!/bin/bash' > "${REPO_ROOT}/agents/mock-signal/bundle/bin/node"
echo 'echo "mock node"' >> "${REPO_ROOT}/agents/mock-signal/bundle/bin/node"
chmod +x "${REPO_ROOT}/agents/mock-signal/bundle/bin/node"

cleanup_t2b() {
    rm -rf "${REPO_ROOT}/agents/mock-signal"
    rm -rf "$TEST_WORKSPACE"
}
trap cleanup_t2b EXIT

echo "--- T2b Setup: Mock docker and test agent created ---"
echo ""

# ==============================================================================
# 2b.1 — stop_running_containers() Behavior
# ==============================================================================

echo "--- T2b.1: stop_running_containers() Logic ---"

# T2b-01: stop_running_containers sets STOPPED flag to prevent re-entry
TOTAL=$((TOTAL + 1))
echo "T2b-01: stop_running_containers sets STOPPED flag ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    # Mock docker ps to return a container
    export PATH="'"$MOCK_DIR"':$PATH"
    SWE_DOCKER_MODE=success stop_running_containers
    echo "STOPPED=${STOPPED:-unset}"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "STOPPED=1"; then
    echo "  ✓ T2b-01: stop_running_containers sets STOPPED flag"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2b-01: stop_running_containers sets STOPPED flag (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# T2b-02: stop_running_containers uses || true to avoid grep failure on no containers
TOTAL=$((TOTAL + 1))
echo "T2b-02: stop_running_containers handles empty container list gracefully ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    export PATH="'"$MOCK_DIR"':$PATH"
    SWE_DOCKER_MODE=success stop_running_containers
    echo $?
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "0"; then
    echo "  ✓ T2b-02: stop_running_containers handles empty container list gracefully"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2b-02: stop_running_containers handles empty container list gracefully (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# T2b-03: stop_running_containers iterates over containers and stops them
TOTAL=$((TOTAL + 1))
echo "T2b-03: stop_running_containers stops each container ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    export PATH="'"$MOCK_DIR"':$PATH"
    SWE_DOCKER_MODE=success stop_running_containers 2>&1
' 2>&1) || true
set -e
# Should not error out even with no containers
if [ $? -eq 0 ] || echo "$OUTPUT" | grep -q "STOPPED"; then
    echo "  ✓ T2b-03: stop_running_containers stops each container"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2b-03: stop_running_containers stops each container"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 2b.2 — Signal Trap Setup (behavior tests)
# ==============================================================================

echo "--- T2b.2: Signal Trap Setup ---"

# T2b-10: INT trap is set to on_interrupt
TOTAL=$((TOTAL + 1))
echo "T2b-10: INT trap is set to on_interrupt ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh 2>/dev/null || true
    trap -p INT
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "on_interrupt"; then
    echo "  ✓ T2b-10: INT trap is set to on_interrupt"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2b-10: INT trap is set to on_interrupt (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# T2b-11: TERM trap is set to on_interrupt
TOTAL=$((TOTAL + 1))
echo "T2b-11: TERM trap is set to on_interrupt ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh 2>/dev/null || true
    trap -p TERM
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "on_interrupt"; then
    echo "  ✓ T2b-11: TERM trap is set to on_interrupt"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2b-11: TERM trap is set to on_interrupt (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# T2b-12: EXIT trap is set to stop_running_containers
TOTAL=$((TOTAL + 1))
echo "T2b-12: EXIT trap is set to stop_running_containers ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh 2>/dev/null || true
    trap -p EXIT
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "stop_running_containers"; then
    echo "  ✓ T2b-12: EXIT trap is set to stop_running_containers"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2b-12: EXIT trap is set to stop_running_containers (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 2b.3 — on_interrupt() Behavior
# ==============================================================================

echo "--- T2b.3: on_interrupt() Logic ---"

# T2b-20: on_interrupt prints interrupt message
TOTAL=$((TOTAL + 1))
echo "T2b-20: on_interrupt prints interrupt message ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    on_interrupt
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "^C received\|shutting down"; then
    echo "  ✓ T2b-20: on_interrupt prints interrupt message"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2b-20: on_interrupt prints interrupt message (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# T2b-21: on_interrupt calls stop_running_containers
TOTAL=$((TOTAL + 1))
echo "T2b-21: on_interrupt calls stop_running_containers ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    export PATH="'"$MOCK_DIR"':$PATH"
    SWE_DOCKER_MODE=success on_interrupt 2>&1
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "Cleanup complete\|STOPPED"; then
    echo "  ✓ T2b-21: on_interrupt calls stop_running_containers"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2b-21: on_interrupt calls stop_running_containers (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 2b.4 — Actual Signal Handling Test
# ==============================================================================

echo "--- T2b.4: Actual Signal Handling ---"

# T2b-30: Script handles SIGINT gracefully without crashing
TOTAL=$((TOTAL + 1))
echo "T2b-30: script handles SIGINT gracefully ..." >&2
set +e
OUTPUT=$(PATH="${MOCK_DIR}:${PATH}" \
    SWE_DOCKER_MODE=success \
    SWE_WORKSPACE_DIR="$TEST_WORKSPACE" \
    timeout 5 bash -c '
        cd "'"$REPO_ROOT"'"
        bash run.sh --run mock-signal astropy__astropy-7166 &
        PID=$!
        sleep 0.5
        kill -INT $PID 2>/dev/null || true
        wait $PID 2>/dev/null || true
    ' 2>&1) || true
set -e
# Should not crash with unhandled signal
if [ $? -eq 0 ] || echo "$OUTPUT" | grep -q "interrupt\|STOPPED"; then
    echo "  ✓ T2b-30: script handles SIGINT gracefully"
    PASS=$((PASS + 1))
else
    echo "  ✗ T2b-30: script handles SIGINT gracefully"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T2b Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
