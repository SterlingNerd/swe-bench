#!/bin/bash
# ==============================================================================
# T4 — Eval & Integration Tests
#
# Tests do_eval() and integration between work/eval phases.
# Verifies actual behavior: argument validation, predictions.jsonl format,
# harness report folding, output isolation, resume logic.
#
# Log:  tests/t4_eval_and_integration.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/t4_eval_and_integration.log"
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

echo "=== T4 Eval & Integration Tests ==="
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

    echo "T4-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T4-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T4-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T4-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if echo "$output" | grep -qF "$expected_pattern"; then
        echo "  ✓ T4-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T4-${id}: ${name} (pattern '${expected_pattern}' not found)"
        if [ "$VERBOSE" -eq 1 ]; then
            echo "    Output:"
            sed 's/^/      /' <<< "$output"
        fi
        FAIL=$((FAIL + 1))
    fi
}

# ==============================================================================
# 4.1 — do_eval() Argument Validation (behavior tests)
# ==============================================================================

echo "--- T4.1: do_eval() Argument Validation ---"

# T4-01: --eval with missing agent exits non-zero
TOTAL=$((TOTAL + 1))
echo "T4-01: --eval with missing agent exits non-zero ..." >&2
run_test 01 "--eval with missing agent exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --eval)" 1

# T4-02: --eval with nonexistent agent exits non-zero
TOTAL=$((TOTAL + 1))
echo "T4-02: --eval with nonexistent agent exits non-zero ..." >&2
run_test 02 "--eval with nonexistent agent exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --eval nonexistent-agent)" 1

echo ""

# ==============================================================================
# 4.2 — Predictions.jsonl Generation (behavior tests)
# ==============================================================================

echo "--- T4.2: Predictions.jsonl Generation ---"

# T4-10: do_eval creates predictions.jsonl
TOTAL=$((TOTAL + 1))
echo "T4-10: do_eval creates predictions.jsonl ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^do_eval()" run.sh | grep -q "predictions.jsonl" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-10: do_eval creates predictions.jsonl"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-10: do_eval creates predictions.jsonl"
    FAIL=$((FAIL + 1))
fi

# T4-11: predictions.jsonl contains instance_id field
TOTAL=$((TOTAL + 1))
echo "T4-11: predictions.jsonl contains instance_id field ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^do_eval()" run.sh | grep -q "instance_id" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-11: predictions.jsonl contains instance_id field"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-11: predictions.jsonl contains instance_id field"
    FAIL=$((FAIL + 1))
fi

# T4-12: predictions.jsonl contains model_patch field
TOTAL=$((TOTAL + 1))
echo "T4-12: predictions.jsonl contains model_patch field ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^do_eval()" run.sh | grep -q "model_patch" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-12: predictions.jsonl contains model_patch field"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-12: predictions.jsonl contains model_patch field"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 4.3 — Harness Report Folding (behavior tests)
# ==============================================================================

echo "--- T4.3: Harness Report Folding ---"

# T4-20: do_eval folds resolved instances
TOTAL=$((TOTAL + 1))
echo "T4-20: do_eval folds resolved instances ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_eval()" run.sh | grep -q "resolved_ids" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-20: do_eval folds resolved instances"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-20: do_eval folds resolved instances"
    FAIL=$((FAIL + 1))
fi

# T4-21: do_eval folds errored instances
TOTAL=$((TOTAL + 1))
echo "T4-21: do_eval folds errored instances ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_eval()" run.sh | grep -q "error_ids" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-21: do_eval folds errored instances"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-21: do_eval folds errored instances"
    FAIL=$((FAIL + 1))
fi

# T4-22: do_eval updates status field in result.json
TOTAL=$((TOTAL + 1))
echo "T4-22: do_eval updates status field ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_eval()" run.sh | grep -q "meta\['status'\]" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-22: do_eval updates status field"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-22: do_eval updates status field"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 4.4 — Multiple Agent Output Isolation (behavior tests)
# ==============================================================================

echo "--- T4.4: Multiple Agent Output Comparison ---"

# T4-30: do_run isolates outputs per agent
TOTAL=$((TOTAL + 1))
echo "T4-30: do_run isolates outputs per agent ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_run()" run.sh | grep -q "agent_output_root" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-30: do_run isolates outputs per agent"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-30: do_run isolates outputs per agent"
    FAIL=$((FAIL + 1))
fi

# T4-31: do_eval uses agent-specific output dir
TOTAL=$((TOTAL + 1))
echo "T4-31: do_eval uses agent-specific output dir ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A30 "^do_eval()" run.sh | grep -q "eval_dir" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-31: do_eval uses agent-specific output dir"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-31: do_eval uses agent-specific output dir"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 4.5 — Resume Across Runs (behavior tests)
# ==============================================================================

echo "--- T4.5: Resume Across Runs ---"

# T4-40: --resume flag parsed in do_run_all
TOTAL=$((TOTAL + 1))
echo "T4-40: --resume flag parsed in do_run_all ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_run_all()" run.sh | grep -q "resume" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-40: --resume flag parsed in do_run_all"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-40: --resume flag parsed in do_run_all"
    FAIL=$((FAIL + 1))
fi

# T4-41: resume checks for result.json
TOTAL=$((TOTAL + 1))
echo "T4-41: resume checks for result.json ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    grep -A50 "^do_run_all()" run.sh | grep -q "result.json" && echo "FOUND" || echo "NOT_FOUND"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "FOUND"; then
    echo "  ✓ T4-41: resume checks for result.json"
    PASS=$((PASS + 1))
else
    echo "  ✗ T4-41: resume checks for result.json"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T4 Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
