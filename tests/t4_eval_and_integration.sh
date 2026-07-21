#!/bin/bash
# ==============================================================================
# T4 — Eval & Integration Tests
#
# Tests do_eval() and integration between work/eval phases.
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
# 4.1 — do_eval() Argument Validation
# ==============================================================================

echo "--- T4.1: do_eval() Argument Validation ---"

run_test 01 "--eval with missing agent exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --eval)" 1

run_test 02 "--eval with nonexistent agent exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --eval nonexistent-agent)" 1

echo ""

# ==============================================================================
# 4.2 — Predictions.jsonl Generation
# ==============================================================================

echo "--- T4.2: Predictions.jsonl Generation ---"

run_test_output 10 "do_eval creates predictions.jsonl" \
    "grep 'predictions.jsonl' '$REPO_ROOT/run.sh'" "predictions.jsonl"

run_test_output 11 "predictions.jsonl contains instance_id field" \
    "grep 'instance_id' '$REPO_ROOT/run.sh'" "instance_id"

run_test_output 12 "predictions.jsonl contains model_patch field" \
    "grep 'model_patch' '$REPO_ROOT/run.sh'" "model_patch"

echo ""

# ==============================================================================
# 4.3 — Harness Report Folding
# ==============================================================================

echo "--- T4.3: Harness Report Folding ---"

run_test_output 20 "do_eval folds resolved instances" \
    "grep 'resolved' '$REPO_ROOT/run.sh'" "resolved"

run_test_output 21 "do_eval folds errored instances" \
    "grep 'error_ids' '$REPO_ROOT/run.sh'" "error_ids"

run_test_output 22 "do_eval updates status field" \
    "grep \"meta\\['status'\\]\" '$REPO_ROOT/run.sh'" "meta"

echo ""

# ==============================================================================
# 4.4 — Multiple Agent Output Comparison
# ==============================================================================

echo "--- T4.4: Multiple Agent Output Comparison ---"

run_test_output 30 "do_run isolates outputs per agent" \
    "grep 'agent_output_root' '$REPO_ROOT/run.sh'" "agent_output_root"

run_test_output 31 "do_eval uses agent-specific output dir" \
    "grep 'eval_dir' '$REPO_ROOT/run.sh'" "eval_dir"

echo ""

# ==============================================================================
# 4.5 — Resume Across Runs
# ==============================================================================

echo "--- T4.5: Resume Across Runs ---"

run_test_output 40 "--resume flag parsed in do_run_all" \
    "grep 'resume' '$REPO_ROOT/run.sh'" "resume"

run_test_output 41 "resume checks for result.json" \
    "grep 'result.json' '$REPO_ROOT/run.sh'" "result.json"

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T4 Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
