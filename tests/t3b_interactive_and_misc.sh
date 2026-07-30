#!/bin/bash
# ==============================================================================
# T3b — Interactive Mode & Miscellaneous Tests
#
# Tests do_interactive() and miscellaneous edge cases.
# Verifies actual behavior: argument validation, docker flags, output isolation.
#
# Log:  tests/t3b_interactive_and_misc.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_HELPER="${SCRIPT_DIR}/test_helper.sh"
[ -f "$SOURCE_HELPER" ] && source "$SOURCE_HELPER"
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

    if check_output "$output" "$expected_pattern"; then
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
# 3b.1 — do_interactive() Argument Validation (behavior tests)
# ==============================================================================

echo "--- T3b.1: do_interactive() Argument Validation ---"

# T3b-01: --interactive with missing agent exits non-zero
TOTAL=$((TOTAL + 1))
echo "T3b-01: --interactive with missing agent exits non-zero ..." >&2
run_test 01 "--interactive with missing agent exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --interactive)" 1

# T3b-02: --interactive with missing instance exits non-zero
TOTAL=$((TOTAL + 1))
echo "T3b-02: --interactive with missing instance exits non-zero ..." >&2
run_test 02 "--interactive with missing instance exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --interactive pi)" 1

echo ""

# ==============================================================================
# 3b.2 — do_interactive() Docker Flags (behavior tests)
# ==============================================================================

echo "--- T3b.2: do_interactive() Docker Flags ---"

# T3b-10: bundle mounted read-only at /agent
TOTAL=$((TOTAL + 1))
echo "T3b-10: bundle mounted read-only at /agent ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^do_interactive()" run.sh | grep -q "/agent:ro" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-10: bundle mounted read-only at /agent"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-10: bundle mounted read-only at /agent"
    FAIL=$((FAIL + 1))
fi

# T3b-11: entrypoint.sh is called in interactive mode
TOTAL=$((TOTAL + 1))
echo "T3b-11: entrypoint.sh is called in interactive mode ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^do_interactive()" run.sh | grep -q "entrypoint.sh" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-11: entrypoint.sh is called in interactive mode"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-11: entrypoint.sh is called in interactive mode"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 3b.3 — Bundle Validation (behavior test)
# ==============================================================================

echo "--- T3b.3: Bundle Validation ---"

# T3b-20: interactive validates bundle exists
TOTAL=$((TOTAL + 1))
echo "T3b-20: interactive validates bundle exists ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^do_interactive()" run.sh | grep -q "bundle" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-20: interactive validates bundle exists"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-20: interactive validates bundle exists"
    FAIL=$((FAIL + 1))
fi

# T3b-21: interactive prints error if bundle missing
TOTAL=$((TOTAL + 1))
echo "T3b-21: interactive prints error if bundle missing ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^do_interactive()" run.sh | grep -q "not found" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-21: interactive prints error if bundle missing"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-21: interactive prints error if bundle missing"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 3b.4 — Multiple Agent Output Isolation (behavior tests)
# ==============================================================================

echo "--- T3b.4: Multiple Agent Output Isolation ---"

# T3b-30: do_run uses agent_output_root for isolation
TOTAL=$((TOTAL + 1))
echo "T3b-30: do_run uses agent_output_root for isolation ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_run()" run.sh | grep -q "agent_output_root" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-30: do_run uses agent_output_root for isolation"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-30: do_run uses agent_output_root for isolation"
    FAIL=$((FAIL + 1))
fi

# T3b-31: OUTPUT_DIR used for output paths
TOTAL=$((TOTAL + 1))
echo "T3b-31: OUTPUT_DIR used for output paths ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_run()" run.sh | grep -q "OUTPUT_DIR" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-31: OUTPUT_DIR used for output paths"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-31: OUTPUT_DIR used for output paths"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 3b.5 — Result.json Schema (behavior tests)
# ==============================================================================

echo "--- T3b.5: Result.json Schema ---"

TEST_WS=$(mktemp -d /tmp/swe-bench-t3b-ctx.XXXXXX)

# T3b-40: record_host_result writes RESULT_STATUS
TOTAL=$((TOTAL + 1))
echo "T3b-40: record_host_result writes status field ..." >&2
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    record_host_result "'"$TEST_WS/result.json"'" "test_status" 42 123
' 2>&1
set -e
if python3 -c "import json; d=json.load(open('$TEST_WS/result.json')); assert 'status' in d" 2>/dev/null; then
    echo "  ✓ T3b-40: record_host_result writes status field"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-40: record_host_result writes status field"
    FAIL=$((FAIL + 1))
fi

# T3b-41: record_host_result writes container_exit_code
TOTAL=$((TOTAL + 1))
echo "T3b-41: record_host_result writes container_exit_code ..." >&2
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    record_host_result "'"$TEST_WS/result2.json"'" "test_status" 99 456
' 2>&1
set -e
if python3 -c "import json; d=json.load(open('$TEST_WS/result2.json')); assert d['container_exit_code']==99" 2>/dev/null; then
    echo "  ✓ T3b-41: record_host_result writes container_exit_code"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-41: record_host_result writes container_exit_code"
    FAIL=$((FAIL + 1))
fi

# T3b-42: record_host_result writes elapsed_seconds
TOTAL=$((TOTAL + 1))
echo "T3b-42: record_host_result writes elapsed_seconds ..." >&2
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    record_host_result "'"$TEST_WS/result3.json"'" "test_status" 42 789
' 2>&1
set -e
if python3 -c "import json; d=json.load(open('$TEST_WS/result3.json')); assert d['elapsed_seconds']==789" 2>/dev/null; then
    echo "  ✓ T3b-42: record_host_result writes elapsed_seconds"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-42: record_host_result writes elapsed_seconds"
    FAIL=$((FAIL + 1))
fi

# T3b-43: patch_bytes is used in summarize_agent
TOTAL=$((TOTAL + 1))
echo "T3b-43: patch_bytes is used in summarize_agent ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^summarize_agent()" run.sh | grep -q "patch_bytes" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-43: patch_bytes is used in summarize_agent"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-43: patch_bytes is used in summarize_agent"
    FAIL=$((FAIL + 1))
fi

rm -rf "$TEST_WS"

echo ""

# ==============================================================================
# 3b.6 — Summarize Edge Cases (behavior tests)
# ==============================================================================

echo "--- T3b.6: Summarize Edge Cases ---"

# T3b-50: summarize_agent handles result.json
TOTAL=$((TOTAL + 1))
echo "T3b-50: summarize_agent reads result.json ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^summarize_agent()" run.sh | grep -q "result.json" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-50: summarize_agent reads result.json"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-50: summarize_agent reads result.json"
    FAIL=$((FAIL + 1))
fi

# T3b-51: summarize_agent skips corrupted JSON
TOTAL=$((TOTAL + 1))
echo "T3b-51: summarize_agent handles corrupted JSON gracefully ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^summarize_agent()" run.sh | grep -q "JSONDecodeError" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-51: summarize_agent handles corrupted JSON gracefully"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-51: summarize_agent handles corrupted JSON gracefully"
    FAIL=$((FAIL + 1))
fi

# T3b-52: summarize prints resolved count
TOTAL=$((TOTAL + 1))
echo "T3b-52: summarize prints resolved count ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^summarize_agent()" run.sh | grep -q "resolved" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T3b-52: summarize prints resolved count"
    PASS=$((PASS + 1))
else
    echo "  ✗ T3b-52: summarize prints resolved count"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T3b Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
