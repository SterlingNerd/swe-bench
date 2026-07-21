#!/bin/bash
# ==============================================================================
# T3b — Interactive Mode & Miscellaneous Tests
#
# Tests do_interactive() and miscellaneous edge cases.
#
# Log:  tests/t3b_interactive_and_misc.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/t3b_interactive_and_misc.log"
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

echo "=== T3b Interactive Mode & Miscellaneous Tests ==="
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

    echo "T3b-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T3b-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T3b-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T3b-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if echo "$output" | grep -qF "$expected_pattern"; then
        echo "  ✓ T3b-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T3b-${id}: ${name} (pattern '${expected_pattern}' not found)"
        if [ "$VERBOSE" -eq 1 ]; then
            echo "    Output:"
            sed 's/^/      /' <<< "$output"
        fi
        FAIL=$((FAIL + 1))
    fi
}

# ==============================================================================
# 3b.1 — do_interactive() Argument Validation
# ==============================================================================

echo "--- T3b.1: do_interactive() Argument Validation ---"

run_test 01 "--interactive with missing agent exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --interactive)" 1

run_test 02 "--interactive with missing instance exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --interactive pi)" 1

echo ""

# ==============================================================================
# 3b.2 — do_interactive() Docker Flags
# ==============================================================================

echo "--- T3b.2: do_interactive() Docker Flags ---"

run_test_output 10 "bundle mounted read-only at /agent" \
    "grep '/agent:ro' '$REPO_ROOT/run.sh'" "/agent:ro"

run_test_output 11 "entrypoint.sh called in script" \
    "grep 'entrypoint.sh' '$REPO_ROOT/run.sh'" "entrypoint.sh"

run_test_output 12 "entrypoint.sh called in script" \
    "grep 'entrypoint.sh' '$REPO_ROOT/run.sh'" "entrypoint.sh"

echo ""

# ==============================================================================
# 3b.3 — Bundle Validation in Interactive Mode
# ==============================================================================

echo "--- T3b.3: Bundle Validation ---"

run_test_output 20 "interactive validates bundle exists" \
    "grep 'bundle' '$REPO_ROOT/run.sh'" "bundle"

run_test_output 21 "interactive prints error if bundle missing" \
    "grep 'not found' '$REPO_ROOT/run.sh'" "not found"

echo ""

# ==============================================================================
# 3b.4 — Multiple Agent Output Isolation
# ==============================================================================

echo "--- T3b.4: Multiple Agent Output Isolation ---"

run_test_output 30 "do_run uses agent_output_root for isolation" \
    "grep 'agent_output_root' '$REPO_ROOT/run.sh'" "agent_output_root"

run_test_output 31 "OUTPUT_DIR used for output paths" \
    "grep 'OUTPUT_DIR' '$REPO_ROOT/run.sh'" "OUTPUT_DIR"

echo ""

# ==============================================================================
# 3b.5 — Result.json Schema Validation
# ==============================================================================

echo "--- T3b.5: Result.json Schema ---"

run_test_output 40 "record_host_result writes RESULT_STATUS" \
    "grep 'RESULT_STATUS' '$REPO_ROOT/run.sh'" "RESULT_STATUS"

run_test_output 41 "record_host_result writes container_exit_code" \
    "grep 'CONTAINER_EXIT_CODE' '$REPO_ROOT/run.sh'" "CONTAINER_EXIT_CODE"

run_test_output 42 "record_host_result writes ELAPSED_SECONDS" \
    "grep 'ELAPSED_SECONDS' '$REPO_ROOT/run.sh'" "ELAPSED_SECONDS"

run_test_output 43 "patch_bytes used in script" \
    "grep 'patch_bytes' '$REPO_ROOT/run.sh'" "patch_bytes"

echo ""

# ==============================================================================
# 3b.6 — Summarize Edge Cases
# ==============================================================================

echo "--- T3b.6: Summarize Edge Cases ---"

run_test_output 50 "summarize_agent handles result.json" \
    "grep 'result.json' '$REPO_ROOT/run.sh'" "result.json"

run_test_output 51 "summarize_agent skips corrupted JSON" \
    "grep 'JSONDecodeError' '$REPO_ROOT/run.sh'" "JSONDecodeError"

run_test_output 52 "summarize prints resolved count" \
    "grep 'resolved' '$REPO_ROOT/run.sh'" "resolved"

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T3b Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
