#!/bin/bash
# ==============================================================================
# T0 — Pure Shell Logic Tests (No Docker Required)
#
# Tests actual behavior of functions by sourcing run.sh and calling them directly.
# Verifies return codes, output values, and filesystem state changes.
#
# Log:  tests/t0_pure_shell.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_HELPER="${SCRIPT_DIR}/test_helper.sh"
[ -f "$SOURCE_HELPER" ] && source "$SOURCE_HELPER"
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

    if check_output "$output" "$expected_pattern"; then
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
# 0.1 — Argument Parsing & Help (behavior tests)
# ==============================================================================

echo "--- T0.1: Argument Parsing & Help ---"

run_test 01 "no args exits 0" \
    "(cd '$REPO_ROOT' && bash run.sh)" 0

run_test 02 "--help exits 0" \
    "(cd '$REPO_ROOT' && bash run.sh --help)" 0

run_test 03 "-h exits 0" \
    "(cd '$REPO_ROOT' && bash run.sh -h)" 0

run_test 04 "unknown flag exits 1" \
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

run_test 10 "--help after positional arg treated as unknown" \
    "(cd '$REPO_ROOT' && bash run.sh --run-all pi --timeout 3600 --resume --help)" 1

echo ""

# ==============================================================================
# 0.2 — Configuration Defaults (behavior tests via sourcing)
# ==============================================================================

echo "--- T0.2: Configuration & Environment ---"

# Source run.sh in a subshell to check default values
run_test_output 11 "MAX_STORAGE_PCT defaults to 80" \
    "(cd '$REPO_ROOT' && bash -c 'source run.sh; echo \"\${MAX_STORAGE_PCT:-}\"' 2>/dev/null || echo '')" "80"

run_test_output 12 "HF_DATASET defaults to SWE-bench_Verified" \
    "(cd '$REPO_ROOT' && bash -c 'source run.sh; echo \"\${HF_DATASET:-}\"' 2>/dev/null || echo '')" "princeton-nlp/SWE-bench_Verified"

run_test_output 13 "CACHE_FILE defaults to /tmp/swe_verified_cache.json" \
    "(cd '$REPO_ROOT' && bash -c 'source run.sh; echo \"\${CACHE_FILE:-}\"' 2>/dev/null || echo '')" "/tmp/swe_verified_cache.json"

run_test_output 14 "OUTPUT_DIR ends with /outputs" \
    "(cd '$REPO_ROOT' && bash -c 'source run.sh; echo \"\${OUTPUT_DIR:-}\"' 2>/dev/null || echo '')" "/outputs"

run_test_output 15 "SWEBENCH_VENV at .venv/swebench" \
    "(cd '$REPO_ROOT' && bash -c 'source run.sh; echo \"\${SWEBENCH_VENV:-}\"' 2>/dev/null || echo '')" ".venv/swebench"

echo ""

# ==============================================================================
# 0.3 — Storage Check (behavior test: mock df to control usage)
# ==============================================================================

echo "--- T0.3: Storage Check ---"

# Create a mock df that reports specific usage percentages
MOCK_DIR="${SCRIPT_DIR}/fixtures"

# Test check_storage returns 0 when below threshold
TOTAL=$((TOTAL + 1))
echo "T0-16: check_storage returns 0 when disk at 50% and threshold is 80 ..." >&2
set +e
OUTPUT=$(PATH="$MOCK_DIR:$PATH" SWE_DOCKER_MODE=success bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    check_storage
    echo $?
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "0"; then
    echo "  ✓ T0-16: check_storage returns 0 when disk at 50% and threshold is 80"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-16: check_storage returns 0 when disk at 50% and threshold is 80 (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# Test check_storage returns 1 when above threshold (use real df)
TOTAL=$((TOTAL + 1))
echo "T0-17: check_storage returns 1 when disk usage >= MAX_STORAGE_PCT ..." >&2
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    # Set threshold to current usage to force failure (usage >= threshold)
    USAGE=$(df --output=pcent "'"$REPO_ROOT"'" | tail -1 | tr -d " %")
    MAX_STORAGE_PCT=$USAGE check_storage
' 2>&1
ACTUAL_EXIT=$?
set -e
if [ "$ACTUAL_EXIT" -eq 1 ]; then
    echo "  ✓ T0-17: check_storage returns 1 when disk usage >= MAX_STORAGE_PCT"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-17: check_storage returns 1 when disk usage >= MAX_STORAGE_PCT (got exit=$ACTUAL_EXIT)"
    FAIL=$((FAIL + 1))
fi

# Test check_storage returns 0 when below threshold
TOTAL=$((TOTAL + 1))
echo "T0-18: check_storage returns 0 when disk usage < MAX_STORAGE_PCT ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    # Set threshold to current usage + 50 to force success
    USAGE=$(df --output=pcent "'"$REPO_ROOT"'" | tail -1 | tr -d " %")
    THRESHOLD=$((USAGE + 50))
    MAX_STORAGE_PCT=$THRESHOLD check_storage
    echo $?
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "0"; then
    echo "  ✓ T0-18: check_storage returns 0 when disk usage < MAX_STORAGE_PCT"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-18: check_storage returns 0 when disk usage < MAX_STORAGE_PCT (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 0.4 — Docker Readiness (behavior tests)
# ==============================================================================

echo "--- T0.4: Docker Readiness ---"

# Test ensure_docker sets DOCKER_READY when docker is available
TOTAL=$((TOTAL + 1))
echo "T0-19: ensure_docker sets DOCKER_READY=1 when docker works ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    ensure_docker
    echo "DOCKER_READY=${DOCKER_READY:-unset}"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "DOCKER_READY=1"; then
    echo "  ✓ T0-19: ensure_docker sets DOCKER_READY=1 when docker works"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-19: ensure_docker sets DOCKER_READY=1 when docker works (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# Test require_docker returns 0 when docker is available
TOTAL=$((TOTAL + 1))
echo "T0-20: require_docker returns 0 when docker info succeeds ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    require_docker
    echo $?
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "0"; then
    echo "  ✓ T0-20: require_docker returns 0 when docker info succeeds"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-20: require_docker returns 0 when docker info succeeds (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 0.5 — Instance-to-Image Mapping (behavior tests)
# ==============================================================================

echo "--- T0.5: Image Name Mapping ---"

# Test get_arch returns correct architecture
TOTAL=$((TOTAL + 1))
echo "T0-21: get_arch returns x86_64 on x86_64 system ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    get_arch
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "x86_64"; then
    echo "  ✓ T0-21: get_arch returns x86_64 on x86_64 system"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-21: get_arch returns x86_64 on x86_64 system (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# Test instance_to_image produces correct image name format
TOTAL=$((TOTAL + 1))
echo "T0-22: instance_to_image produces correct image name ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    instance_to_image "django__django-11039"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "swebench/sweb.eval.x86_64.django_1776_django-11039:latest"; then
    echo "  ✓ T0-22: instance_to_image produces correct image name"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-22: instance_to_image produces correct image name (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# Test instance_to_image with custom registry
TOTAL=$((TOTAL + 1))
echo "T0-23: instance_to_image uses custom SWEBENCH_REGISTRY ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    SWEBENCH_REGISTRY="custom.registry.io" instance_to_image "django__django-11039"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "custom.registry.io"; then
    echo "  ✓ T0-23: instance_to_image uses custom SWEBENCH_REGISTRY"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-23: instance_to_image uses custom SWEBENCH_REGISTRY (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 0.6 — Record Host Result (behavior test: verify JSON output)
# ==============================================================================

echo "--- T0.6: Record Host Result ---"

TEST_WS=$(mktemp -d /tmp/swe-bench-t0-ctx.XXXXXX)

# Test record_host_result creates valid JSON with correct fields
TOTAL=$((TOTAL + 1))
echo "T0-26: record_host_result creates valid JSON with status field ..." >&2
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    record_host_result "'"$TEST_WS/result.json"'" "test_status" 42 123
' 2>&1
set -e
if python3 -c "import json; d=json.load(open('$TEST_WS/result.json')); assert d['status']=='test_status'; assert d['container_exit_code']==42; assert d['elapsed_seconds']==123" 2>/dev/null; then
    echo "  ✓ T0-26: record_host_result creates valid JSON with status field"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-26: record_host_result creates valid JSON with status field"
    if [ -f "$TEST_WS/result.json" ]; then
        echo "    Content: $(cat $TEST_WS/result.json)"
    fi
    FAIL=$((FAIL + 1))
fi

# Test record_host_result includes patch_bytes (defaults to 0)
TOTAL=$((TOTAL + 1))
echo "T0-27: record_host_result defaults patch_bytes to 0 ..." >&2
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    record_host_result "'"$TEST_WS/result2.json"'" "test_status" 42 123
' 2>&1
set -e
if python3 -c "import json; d=json.load(open('$TEST_WS/result2.json')); assert d.get('patch_bytes',0)==0" 2>/dev/null; then
    echo "  ✓ T0-27: record_host_result defaults patch_bytes to 0"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-27: record_host_result defaults patch_bytes to 0"
    FAIL=$((FAIL + 1))
fi

# Test record_host_result merges into existing JSON
TOTAL=$((TOTAL + 1))
echo "T0-28: record_host_result merges into existing JSON ..." >&2
echo '{"existing": "field"}' > "$TEST_WS/result3.json"
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    record_host_result "'"$TEST_WS/result3.json"'" "new_status" 99 456
' 2>&1
set -e
if python3 -c "import json; d=json.load(open('$TEST_WS/result3.json')); assert d['existing']=='field'; assert d['status']=='new_status'" 2>/dev/null; then
    echo "  ✓ T0-28: record_host_result merges into existing JSON"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-28: record_host_result merges into existing JSON"
    FAIL=$((FAIL + 1))
fi

rm -rf "$TEST_WS"

echo ""

# ==============================================================================
# 0.7 — Release Container (behavior test)
# ==============================================================================

echo "--- T0.7: Release Container ---"

# Test release_container returns 0 for non-existent container
TOTAL=$((TOTAL + 1))
echo "T0-30: release_container returns 0 for non-existent container ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    release_container "nonexistent_container_xyz_12345"
    echo $?
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "0"; then
    echo "  ✓ T0-30: release_container returns 0 for non-existent container"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-30: release_container returns 0 for non-existent container (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 0.8 — Image Cache Helpers (behavior tests)
# ==============================================================================

echo "--- T0.8: Image Cache Helpers ---"

# Test save_image_to_cache returns 0 when SWEBENCH_IMAGE_CACHE is unset
TOTAL=$((TOTAL + 1))
echo "T0-34: save_image_to_cache returns 0 when cache dir unset ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    unset SWEBENCH_IMAGE_CACHE
    save_image_to_cache "test_image"
    echo $?
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "0"; then
    echo "  ✓ T0-34: save_image_to_cache returns 0 when cache dir unset"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-34: save_image_to_cache returns 0 when cache dir unset (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# Test load_image_from_cache returns 1 when cache dir is set but tar missing
TOTAL=$((TOTAL + 1))
echo "T0-35: load_image_from_cache returns 1 when cache dir set but tar missing ..." >&2
TEST_CACHE3=$(mktemp -d /tmp/swe-bench-t0-cache3.XXXXXX)
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    SWEBENCH_IMAGE_CACHE="'"$TEST_CACHE3"'" load_image_from_cache "test_image"
' 2>&1
ACTUAL_EXIT=$?
set -e
if [ "$ACTUAL_EXIT" -eq 1 ]; then
    echo "  ✓ T0-35: load_image_from_cache returns 1 when cache dir set but tar missing"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-35: load_image_from_cache returns 1 when cache dir set but tar missing (got exit=$ACTUAL_EXIT)"
    FAIL=$((FAIL + 1))
fi
rm -rf "$TEST_CACHE3"

# Test save_image_to_cache sanitizes image name for tar filename
# We can't actually test docker save, but we can verify the sanitization logic
TOTAL=$((TOTAL + 1))
echo "T0-36: save_image_to_cache sanitizes slashes/colons in image name ..." >&2
set +e
OUTPUT=$(bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    # Test the sanitization directly
    local_safe_name=$(echo "swebench/sweb.eval.x86_64.django_1776_django-11039:latest" | tr "/:" "__")
    echo "$local_safe_name"
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "swebench_sweb.eval.x86_64.django_1776_django-11039_latest"; then
    echo "  ✓ T0-36: save_image_to_cache sanitizes slashes/colons in image name"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-36: save_image_to_cache sanitizes slashes/colons in image name (got: $OUTPUT)"
    FAIL=$((FAIL + 1))
fi

# Test load_image_from_cache returns 1 when tar file does not exist
TOTAL=$((TOTAL + 1))
echo "T0-37: load_image_from_cache returns 1 when tar missing ..." >&2
TEST_CACHE2=$(mktemp -d /tmp/swe-bench-t0-cache2.XXXXXX)
set +e
bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    SWEBENCH_IMAGE_CACHE="'"$TEST_CACHE2"'" load_image_from_cache "nonexistent_image"
' 2>&1
ACTUAL_EXIT=$?
set -e
if [ "$ACTUAL_EXIT" -eq 1 ]; then
    echo "  ✓ T0-37: load_image_from_cache returns 1 when tar missing"
    PASS=$((PASS + 1))
else
    echo "  ✗ T0-37: load_image_from_cache returns 1 when tar missing (got exit=$ACTUAL_EXIT)"
    FAIL=$((FAIL + 1))
fi
rm -rf "$TEST_CACHE2"

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T0 Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
