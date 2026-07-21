#!/bin/bash
# ==============================================================================
# T0 — Pure Shell Logic Tests (No Docker Required)
#
# Usage: ./tests/t0_pure_shell.sh [--verbose]
# Log:  tests/t0_pure_shell.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/t0_pure_shell.log"
PASS=0
FAIL=0
TOTAL=0
VERBOSE=0

# Parse args
for arg in "$@"; do
    case "$arg" in
        --verbose) VERBOSE=1 ;;
    esac
done

# Start logging
exec > >(tee -a "$LOG_FILE") 2>&1
: > "$LOG_FILE"  # truncate log at start

echo "=== T0 Pure Shell Tests ==="
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

    echo "T0-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T0-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T0-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T0-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if echo "$output" | grep -qF "$expected_pattern"; then
        echo "  ✓ T0-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T0-${id}: ${name} (pattern '${expected_pattern}' not found)"
        if [ "$VERBOSE" -eq 1 ]; then
            echo "    Output was:"
            sed 's/^/      /' <<< "$output"
        fi
        FAIL=$((FAIL + 1))
    fi
}

# ==============================================================================
# 0.1 — Argument Parsing & Help
# ==============================================================================

echo "--- T0.1: Argument Parsing & Help ---"

run_test 01 "no args prints help, exits 0" \
    "(cd '$REPO_ROOT' && bash run.sh)" 0

run_test 02 "--help prints help, exits 0" \
    "(cd '$REPO_ROOT' && bash run.sh --help)" 0

run_test 03 "-h prints help, exits 0" \
    "(cd '$REPO_ROOT' && bash run.sh -h)" 0

run_test 04 "unknown flag prints error, exits 1" \
    "(cd '$REPO_ROOT' && bash run.sh --unknown-flag)" 1

run_test 05 "--run with missing args exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --run)" 1

run_test 06 "--run-all with missing agent exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --run-all)" 1

run_test 07 "--eval with missing agent exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --eval)" 1

run_test 08 "--interactive with missing args exits non-zero" \
    "(cd '$REPO_ROOT' && bash run.sh --interactive)" 1

run_test 09 "non-numeric timeout rejected" \
    "(cd '$REPO_ROOT' && bash run.sh --run-all pi --timeout abc)" 1

echo ""

# ==============================================================================
# 0.1b — Flag ordering (flags before positional args)
# ==============================================================================

echo "--- T0.1b: Flag Ordering ---"

run_test 10 "--help after positional arg treated as unknown" \
    "(cd '$REPO_ROOT' && bash run.sh --run-all pi --timeout 3600 --resume --help)" 1

# ==============================================================================
# 0.2 — Configuration & Environment Defaults
# ==============================================================================

echo "--- T0.2: Configuration & Environment ---"

run_test_output 11 "MAX_STORAGE_PCT defaults to 80" \
    "grep 'MAX_STORAGE_PCT' '$REPO_ROOT/run.sh'" "MAX_STORAGE_PCT:-80"

run_test_output 12 "HF_DATASET defaults to SWE-bench_Verified" \
    "grep 'HF_DATASET=' '$REPO_ROOT/run.sh' | head -1" "princeton-nlp/SWE-bench_Verified"

run_test_output 13 "CACHE_FILE defaults to /tmp/swe_verified_cache.json" \
    "grep 'CACHE_FILE=' '$REPO_ROOT/run.sh' | head -1" "/tmp/swe_verified_cache.json"

run_test_output 14 "OUTPUT_DIR derived from SWE_WORKSPACE_DIR" \
    "grep 'OUTPUT_DIR=' '$REPO_ROOT/run.sh' | head -1" "SWE_WORKSPACE_DIR"

run_test_output 15 "SWEBENCH_VENV at .venv/swebench" \
    "grep 'SWEBENCH_VENV=' '$REPO_ROOT/run.sh'" ".venv/swebench"

echo ""

# ==============================================================================
# 0.3 — Storage Check (check_storage)
# ==============================================================================

echo "--- T0.3: Storage Check ---"

run_test_output 16 "check_storage uses df --output=pcent" \
    "grep -A5 'check_storage()' '$REPO_ROOT/run.sh'" "df --output=pcent"

run_test_output 17 "check_storage compares against MAX_STORAGE_PCT" \
    "grep -A5 'check_storage()' '$REPO_ROOT/run.sh'" "MAX_STORAGE_PCT"

run_test_output 18 "check_storage returns 1 when at/above threshold" \
    "grep -A5 'check_storage()' '$REPO_ROOT/run.sh'" "return 1"

echo ""

# ==============================================================================
# 0.4 — Docker Readiness (require_docker, ensure_docker)
# ==============================================================================

echo "--- T0.4: Docker Readiness ---"

run_test_output 19 "require_docker checks docker info" \
    "grep -A3 'require_docker()' '$REPO_ROOT/run.sh'" "docker info"

run_test_output 20 "ensure_docker caches DOCKER_READY flag" \
    "grep -A4 'ensure_docker()' '$REPO_ROOT/run.sh'" "DOCKER_READY"

echo ""

# ==============================================================================
# 0.5 — Instance-to-Image Mapping (instance_to_image, get_arch)
# ==============================================================================

echo "--- T0.5: Image Name Mapping ---"

run_test_output 21 "get_arch maps x86_64 correctly" \
    "uname -m | sed 's/x86_64/x86_64/; s/aarch64/arm64/'" "x86_64"

run_test_output 22 "instance_to_image uses SWEBENCH_REGISTRY prefix" \
    "grep -A15 'instance_to_image()' '$REPO_ROOT/run.sh'" "SWEBENCH_REGISTRY"

run_test_output 23 "instance_to_image converts repo slashes to underscores" \
    "grep -A15 'instance_to_image()' '$REPO_ROOT/run.sh' | grep -F 's|/|_|g'" "s|/|_|g"

run_test_output 24 "instance_to_image includes _1776_ version marker" \
    "grep -A15 'instance_to_image()' '$REPO_ROOT/run.sh'" "_1776_"

echo ""

# ==============================================================================
# 0.6 — Record Host Result (record_host_result)
# ==============================================================================

echo "--- T0.6: Record Host Result ---"

run_test_output 26 "record_host_result writes JSON with status field" \
    "grep -A30 'record_host_result()' '$REPO_ROOT/run.sh' | grep -F '"status"'" '"status"'

run_test_output 27 "record_host_result includes container_exit_code" \
    "grep -A30 'record_host_result()' '$REPO_ROOT/run.sh'" "container_exit_code"

run_test_output 28 "record_host_result includes elapsed_seconds" \
    "grep -A30 'record_host_result()' '$REPO_ROOT/run.sh'" "elapsed_seconds"

run_test_output 29 "record_host_result defaults patch_bytes to 0" \
    "grep -A30 'record_host_result()' '$REPO_ROOT/run.sh' | grep -F 'patch_bytes'" "patch_bytes"

echo ""

# ==============================================================================
# 0.7 — Release Container (release_container)
# ==============================================================================

echo "--- T0.7: Release Container ---"

run_test_output 30 "release_container calls docker rm -f" \
    "grep -A3 'release_container()' '$REPO_ROOT/run.sh'" "docker rm -f"

run_test_output 31 "release_container releases bridge network endpoint" \
    "grep -A3 'release_container()' '$REPO_ROOT/run.sh'" "network disconnect"

echo ""

# ==============================================================================
# 0.8 — Image Cache Helpers (save/load)
# ==============================================================================

echo "--- T0.8: Image Cache Helpers ---"

run_test_output 34 "save_image_to_cache returns 0 if SWEBENCH_IMAGE_CACHE unset" \
    "grep -A5 'save_image_to_cache()' '$REPO_ROOT/run.sh'" "return 0"

run_test_output 35 "load_image_from_cache returns 1 if SWEBENCH_IMAGE_CACHE unset" \
    "grep -A5 'load_image_from_cache()' '$REPO_ROOT/run.sh'" "return 1"

run_test_output 36 "save_image_to_cache sanitizes image name for filename" \
    "grep -A8 'save_image_to_cache()' '$REPO_ROOT/run.sh'" "tr '/:' '__'"

run_test_output 37 "load_image_from_cache checks tar file exists" \
    "grep -A10 'load_image_from_cache()' '$REPO_ROOT/run.sh'" ".tar"

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T0 Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
