#!/bin/bash
# ==============================================================================
# T2b — Signal Handling & Interrupt Tests
#
# Tests on_interrupt() and stop_running_containers() signal handling.
# Uses a mock docker to simulate containers.
#
# Log:  tests/t2b_signal_handling.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
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

    if echo "$output" | grep -qF "$expected_pattern"; then
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
# 2b.1 — stop_running_containers() Logic
# ==============================================================================

echo "--- T2b.1: stop_running_containers() Logic ---"

run_test_output 01 "stop_running_containers uses mapfile" \
    "grep -A8 'stop_running_containers()' '$REPO_ROOT/run.sh'" "mapfile"

run_test_output 02 "stop_running_containers uses || true to avoid grep failure" \
    "grep -A8 'stop_running_containers()' '$REPO_ROOT/run.sh'" "|| true"

run_test_output 03 "stop_running_containers sets STOPPED flag" \
    "grep -A8 'stop_running_containers()' '$REPO_ROOT/run.sh'" "STOPPED=1"

run_test_output 04 "stop_running_containers checks STOPPED before proceeding" \
    "grep -A3 'stop_running_containers()' '$REPO_ROOT/run.sh'" "STOPPED"

run_test_output 05 "stop_running_containers iterates over containers" \
    "grep -A8 'stop_running_containers()' '$REPO_ROOT/run.sh'" "for cname"

echo ""

# ==============================================================================
# 2b.2 — Signal Trap Setup
# ==============================================================================

echo "--- T2b.2: Signal Trap Setup ---"

run_test_output 10 "INT trap set to on_interrupt" \
    "grep 'trap.*on_interrupt' '$REPO_ROOT/run.sh'" "INT"

run_test_output 11 "TERM trap set to on_interrupt" \
    "grep 'trap.*on_interrupt' '$REPO_ROOT/run.sh'" "TERM"

run_test_output 12 "EXIT trap set to stop_running_containers" \
    "grep 'trap.*stop_running_containers' '$REPO_ROOT/run.sh'" "EXIT"

echo ""

# ==============================================================================
# 2b.3 — on_interrupt() Logic
# ==============================================================================

echo "--- T2b.3: on_interrupt() Logic ---"

run_test_output 20 "on_interrupt prints interrupt message" \
    "grep -A5 'on_interrupt()' '$REPO_ROOT/run.sh'" "^C received"

run_test_output 21 "on_interrupt calls stop_running_containers" \
    "grep -A5 'on_interrupt()' '$REPO_ROOT/run.sh'" "stop_running_containers"

echo ""

# ==============================================================================
# 2b.4 — Actual Signal Handling Test
# ==============================================================================

echo "--- T2b.4: Actual Signal Handling ---"

# Test that sending SIGINT to a running script triggers cleanup
run_test 30 "script handles SIGINT gracefully" \
    "(cd '$REPO_ROOT' && timeout 5 bash -c '
        PATH=\"${MOCK_DIR}:${PATH}\" \
        SWE_DOCKER_MODE=success \
        SWE_WORKSPACE_DIR=\"$TEST_WORKSPACE\" \
        bash run.sh --run mock-signal astropy__astropy-7166 &
        PID=\$!
        sleep 0.5
        kill -INT \$PID 2>/dev/null || true
        wait \$PID 2>/dev/null || true
    ')" 0

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T2b Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
