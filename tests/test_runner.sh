#!/bin/bash
# ==============================================================================
# SWE-bench Orchestrator — Unified Test Runner
#
# Usage: ./tests/test_runner.sh [category] [--verbose]
#   category: T0, T1, T2, T3, or all (default)
#   --verbose: print output for failed tests
#
# Logs are written to tests/<category>.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TOTAL_PASS=0
TOTAL_FAIL=0
TOTAL_SKIP=0
VERBOSE=0

# Parse args
CATEGORIES="all"
for arg in "$@"; do
    case "$arg" in
        T0|T1|T1b|T2|T2b|T2c|T2d|T3|T3b) CATEGORIES="$arg" ;;
        --verbose) VERBOSE=1 ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

run_category() {
    local category="$1"
    local test_file="${SCRIPT_DIR}/${category}_*.sh"
    local test_script
    
    for test_script in $test_file; do
        [ -f "$test_script" ] || continue
        
        echo ""
        echo "=============================================="
        echo " Running: $(basename "$test_script")"
        echo "=============================================="
        
        local result
        if [ "$VERBOSE" -eq 1 ]; then
            bash "$test_script" --verbose
            result=$?
        else
            bash "$test_script"
            result=$?
        fi
        
        # Parse results from log file
        local log_file="${SCRIPT_DIR}/${category}_*.log"
        for lf in $log_file; do
            [ -f "$lf" ] || continue
            if grep -q "Results:" "$lf"; then
                local line
                line=$(grep "Results:" "$lf" | tail -1)
                echo ""
                echo "  → $line"
                
                # Parse counts
                local p f s
                p=$(echo "$line" | grep -oP '\d+(?= passed)' || echo 0)
                f=$(echo "$line" | grep -oP '\d+(?= failed)' || echo 0)
                s=$(echo "$line" | grep -oP '\d+(?= skipped)' || echo 0)
                
                TOTAL_PASS=$((TOTAL_PASS + p))
                TOTAL_FAIL=$((TOTAL_FAIL + f))
                TOTAL_SKIP=$((TOTAL_SKIP + s))
            fi
        done
        
        return $result
    done
}

echo "=============================================="
echo " SWE-bench Orchestrator — Test Suite"
echo " Repo: ${REPO_ROOT}"
echo " Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "=============================================="

# Convert category to lowercase for file matching
CATEGORY_LOWER=$(echo "$CATEGORIES" | tr 'A-Z' 'a-z')

case "$CATEGORIES" in
    T0) run_category "t0" ;;
    T1) run_category "t1"; run_category "t1b" ;;
    T1b) run_category "t1b" ;;
    T2) run_category "t2"; run_category "t2b"; run_category "t2c"; run_category "t2d" ;;
    T2b) run_category "t2b" ;;
    T2c) run_category "t2c" ;;
    T2d) run_category "t2d" ;;
    T3) run_category "t3"; run_category "t3b" ;;
    T3b) run_category "t3b" ;;
    all)
        run_category "t0"
        run_category "t1"
        run_category "t1b"
        run_category "t2"
        run_category "t2b"
        run_category "t2c"
        run_category "t2d"
        run_category "t3"
        run_category "t3b"
        ;;
esac

echo ""
echo "=============================================="
echo " TOTAL: ${TOTAL_PASS} passed, ${TOTAL_FAIL} failed, ${TOTAL_SKIP} skipped"
echo "=============================================="

[ "$TOTAL_FAIL" -eq 0 ]
