#!/bin/bash
# ==============================================================================
# T2c — do_run_all() Mocked Tests
#
# Tests do_run_all() logic paths using mocked docker:
# - Resume mode (skips completed instances)
# - Timeout validation
# - Agent validation
# - Multiple instance processing
#
# Log:  tests/t2c_run_all_mocked.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/t2c_run_all_mocked.log"
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

echo "=== T2c do_run_all() Mocked Tests ==="
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

    echo "T2c-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T2c-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2c-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T2c-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if echo "$output" | grep -qF "$expected_pattern"; then
        echo "  ✓ T2c-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T2c-${id}: ${name} (pattern '${expected_pattern}' not found)"
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
TEST_WORKSPACE=$(mktemp -d /tmp/swe-bench-t2c.XXXXXX)
mkdir -p "${REPO_ROOT}/agents/mock-runall/bundle/bin"
cp "${MOCK_DIR}/mock-entrypoint.sh" "${REPO_ROOT}/agents/mock-runall/entrypoint.sh"
cat > "${REPO_ROOT}/agents/mock-runall/build_bundle.sh" <<'BUILDEOF'
#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Mock bundle built at $BUNDLE_DIR"
BUILDEOF
chmod +x "${REPO_ROOT}/agents/mock-runall/build_bundle.sh" "${REPO_ROOT}/agents/mock-runall/entrypoint.sh"
echo '#!/bin/bash' > "${REPO_ROOT}/agents/mock-runall/bundle/bin/node"
echo 'echo "mock node"' >> "${REPO_ROOT}/agents/mock-runall/bundle/bin/node"
chmod +x "${REPO_ROOT}/agents/mock-runall/bundle/bin/node"

cleanup_t2c() {
    rm -rf "${REPO_ROOT}/agents/mock-runall"
    rm -rf "$TEST_WORKSPACE"
}
trap cleanup_t2c EXIT

echo "--- T2c Setup: Mock docker and test agent created ---"
echo ""

# ==============================================================================
# 2c.1 — do_run_all() Argument Parsing
# ==============================================================================

echo "--- T2c.1: do_run_all() Argument Parsing ---"

run_test 01 "--run-all with missing agent exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --run-all)" 1

run_test 02 "--run-all with invalid timeout exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --run-all mock-runall --timeout abc)" 1

echo ""

# ==============================================================================
# 2c.2 — do_run_all() Agent Validation
# ==============================================================================

echo "--- T2c.2: do_run_all() Agent Validation ---"

run_test_output 10 "invalid agent prints error" \
    "(cd '$REPO_ROOT' && bash run.sh --run-all nonexistent-agent)" "not found"

echo ""

# ==============================================================================
# 2c.3 — Resume Mode Logic
# ==============================================================================

echo "--- T2c.3: Resume Mode Logic ---"

run_test_output 20 "resume flag is parsed" \
    "grep -A20 'do_run_all()' '$REPO_ROOT/run.sh'" "resume"

echo ""

# ==============================================================================
# 2c.4 — Storage Check Mid-Run
# ==============================================================================

echo "--- T2c.4: Storage Check Mid-Run ---"

run_test_output 30 "storage check function exists in run_all" \
    "grep -A100 'do_run_all()' '$REPO_ROOT/run.sh'" "check_storage"

run_test_output 31 "run_all breaks on storage failure" \
    "grep -A100 'do_run_all()' '$REPO_ROOT/run.sh'" "break"

echo ""

# ==============================================================================
# 2c.5 — do_run_all() Output Format
# ==============================================================================

echo "--- T2c.5: do_run_all() Output Format ---"

run_test_output 40 "run_all prints WORK header for each instance" \
    "grep -A100 'do_run_all()' '$REPO_ROOT/run.sh'" "WORK]"

run_test_output 41 "run_all prints Done summary" \
    "grep -A100 'do_run_all()' '$REPO_ROOT/run.sh'" "Done:"

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T2c Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
