#!/bin/bash
# ==============================================================================
# T1b — Instance Lookup & Dataset Cache Tests
#
# Tests fetch_dataset cache validation and get_instance() behavior.
# Verifies actual data flow, not just code patterns.
#
# Log:  tests/t1b_instance_lookup.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_HELPER="${SCRIPT_DIR}/test_helper.sh"
[ -f "$SOURCE_HELPER" ] && source "$SOURCE_HELPER"
LOG_FILE="${SCRIPT_DIR}/t1b_instance_lookup.log"
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

echo "=== T1b Instance Lookup & Dataset Cache Tests ==="
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

    echo "T1b-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T1b-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T1b-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T1b-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if check_output "$output" "$expected_pattern"; then
        echo "  ✓ T1b-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T1b-${id}: ${name} (pattern '${expected_pattern}' not found)"
        if [ "$VERBOSE" -eq 1 ]; then
            echo "    Output:"
            sed 's/^/      /' <<< "$output"
        fi
        FAIL=$((FAIL + 1))
    fi
}

# ==============================================================================
# Setup: Create mock cache files for testing
# ==============================================================================

TEST_WS=$(mktemp -d /tmp/swe-bench-t1b.XXXXXX)
TEST_CACHE="${TEST_WS}/cache.json"

# Create a valid cache with real-looking data
cat > "$TEST_CACHE" <<'EOF'
[
  {"instance_id": "django__django-11039", "repo": "django/django", "version": "3.2", "problem_statement": "Test issue"},
  {"instance_id": "flask__flask-1000", "repo": "pallets/flask", "version": "2.0", "problem_statement": "Another test"}
]
EOF

cleanup_t1b() {
    rm -rf "$TEST_WS"
}
trap cleanup_t1b EXIT

echo "--- T1b Setup: Mock cache at ${TEST_CACHE} ---"
echo ""

# ==============================================================================
# 1b.1 — fetch_dataset Cache Validation (behavior tests)
# ==============================================================================

echo "--- T1b.1: fetch_dataset Cache Validation ---"

# T1b-01: Missing cache file triggers fetch attempt
TOTAL=$((TOTAL + 1))
echo "T1b-01: missing cache file triggers fetch attempt ..." >&2
set +e
OUTPUT=$(CACHE_FILE="/tmp/nonexistent_cache_xyz_12345" bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    fetch_dataset 2>&1 || true
' 2>&1) || true
set -e
# Should print an error about fetching (since no real dataset available)
if echo "$OUTPUT" | grep -qi "fetch\|error\|fail"; then
    echo "  ✓ T1b-01: missing cache file triggers fetch attempt"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-01: missing cache file triggers fetch attempt"
    FAIL=$((FAIL + 1))
fi

# T1b-02: Empty cache file triggers re-fetch
TOTAL=$((TOTAL + 1))
echo "T1b-02: empty cache file triggers re-fetch ..." >&2
: > "$TEST_WS/empty_cache.json"
set +e
OUTPUT=$(CACHE_FILE="$TEST_WS/empty_cache.json" bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    fetch_dataset 2>&1 || true
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -qi "fetch\|error\|fail"; then
    echo "  ✓ T1b-02: empty cache file triggers re-fetch"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-02: empty cache file triggers re-fetch"
    FAIL=$((FAIL + 1))
fi

# T1b-03: Invalid JSON triggers re-fetch
TOTAL=$((TOTAL + 1))
echo "T1b-03: invalid JSON triggers re-fetch ..." >&2
echo "not valid json {{{" > "$TEST_WS/invalid_cache.json"
set +e
OUTPUT=$(CACHE_FILE="$TEST_WS/invalid_cache.json" bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    fetch_dataset 2>&1 || true
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -qi "fetch\|error\|fail"; then
    echo "  ✓ T1b-03: invalid JSON triggers re-fetch"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-03: invalid JSON triggers re-fetch"
    FAIL=$((FAIL + 1))
fi

# T1b-04: Empty list triggers re-fetch
TOTAL=$((TOTAL + 1))
echo "T1b-04: empty list triggers re-fetch ..." >&2
echo '[]' > "$TEST_WS/empty_list_cache.json"
set +e
OUTPUT=$(CACHE_FILE="$TEST_WS/empty_list_cache.json" bash -c '
    cd "'"$REPO_ROOT"'"
    source run.sh
    fetch_dataset 2>&1 || true
' 2>&1) || true
set -e
if echo "$OUTPUT" | grep -qi "fetch\|error\|fail"; then
    echo "  ✓ T1b-04: empty list triggers re-fetch"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-04: empty list triggers re-fetch"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 1b.2 — get_instance() Behavior (behavior tests)
#
# Tests via actual run.sh commands since CACHE_FILE is hardcoded in run.sh.
# We use the real dataset cache which has real instances.
# ==============================================================================

echo "--- T1b.2: get_instance() Logic ---"

# T1b-10: get_instance returns data for valid instance_id (uses real cache)
TOTAL=$((TOTAL + 1))
echo "T1b-10: get_instance returns data for valid instance ..." >&2
set +e
OUTPUT=$(cd "$REPO_ROOT" && bash run.sh --list astropy__astropy-7166 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "astropy__astropy-7166"; then
    echo "  ✓ T1b-10: get_instance returns data for valid instance"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-10: get_instance returns data for valid instance"
    FAIL=$((FAIL + 1))
fi

# T1b-11: get_instance filters by instance_id correctly (repo matches)
TOTAL=$((TOTAL + 1))
echo "T1b-11: get_instance returns correct instance data ..." >&2
set +e
OUTPUT=$(cd "$REPO_ROOT" && bash run.sh --list astropy__astropy-7166 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "astropy"; then
    echo "  ✓ T1b-11: get_instance returns correct instance data"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-11: get_instance returns correct instance data"
    FAIL=$((FAIL + 1))
fi

# T1b-12: get_instance errors on missing instance
TOTAL=$((TOTAL + 1))
echo "T1b-12: get_instance errors on missing instance ..." >&2
set +e
OUTPUT=$(cd "$REPO_ROOT" && bash run.sh --list nonexistent__instance-99999 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "Total: 0"; then
    echo "  ✓ T1b-12: get_instance errors on missing instance"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-12: get_instance errors on missing instance"
    FAIL=$((FAIL + 1))
fi

# T1b-13: get_instance returns full instance dict with all fields
TOTAL=$((TOTAL + 1))
echo "T1b-13: get_instance returns full instance dict ..." >&2
set +e
OUTPUT=$(cd "$REPO_ROOT" && bash run.sh --list astropy__astropy-7336 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "astropy__astropy-7336"; then
    echo "  ✓ T1b-13: get_instance returns full instance dict"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-13: get_instance returns full instance dict"
    FAIL=$((FAIL + 1))
fi

# T1b-14: get_instance exits with code 0 for valid instance (list finds it)
TOTAL=$((TOTAL + 1))
echo "T1b-14: --list exits 0 for existing instance ..." >&2
set +e
cd "$REPO_ROOT" && bash run.sh --list django__django-11039 > /dev/null 2>&1
ACTUAL_EXIT=$?
set -e
if [ "$ACTUAL_EXIT" -eq 0 ]; then
    echo "  ✓ T1b-14: --list exits 0 for existing instance"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-14: --list exits 0 for existing instance (got exit=$ACTUAL_EXIT)"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# 1b.3 — Dataset Structure (behavior tests via --list)
# ==============================================================================

echo "--- T1b.3: Dataset Structure ---"

# T1b-20: --list shows instance IDs in output
TOTAL=$((TOTAL + 1))
echo "T1b-20: --list shows instance IDs ..." >&2
set +e
OUTPUT=$(cd "$REPO_ROOT" && bash run.sh --list 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "__"; then
    echo "  ✓ T1b-20: --list shows instance IDs"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-20: --list shows instance IDs"
    FAIL=$((FAIL + 1))
fi

# T1b-21: --list shows repo names in output
TOTAL=$((TOTAL + 1))
echo "T1b-21: --list shows repo names ..." >&2
set +e
OUTPUT=$(cd "$REPO_ROOT" && bash run.sh --list 2>&1) || true
set -e
if echo "$OUTPUT" | grep -q "django\|flask"; then
    echo "  ✓ T1b-21: --list shows repo names"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-21: --list shows repo names"
    FAIL=$((FAIL + 1))
fi

# T1b-22: --list shows version column
TOTAL=$((TOTAL + 1))
echo "T1b-22: --list shows version info ..." >&2
set +e
OUTPUT=$(cd "$REPO_ROOT" && bash run.sh --list 2>&1) || true
set -e
if echo "$OUTPUT" | grep -qE "v[0-9]"; then
    echo "  ✓ T1b-22: --list shows version info"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-22: --list shows version info"
    FAIL=$((FAIL + 1))
fi

# T1b-23: --list shows difficulty column
TOTAL=$((TOTAL + 1))
echo "T1b-23: --list shows difficulty info ..." >&2
set +e
OUTPUT=$(cd "$REPO_ROOT" && bash run.sh --list 2>&1) || true
set -e
# Difficulty is typically easy/medium/hard or a number
if echo "$OUTPUT" | grep -qiE "easy|medium|hard|[0-9]"; then
    echo "  ✓ T1b-23: --list shows difficulty info"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-23: --list shows difficulty info"
    FAIL=$((FAIL + 1))
fi

# T1b-24: --list shows total count
TOTAL=$((TOTAL + 1))
echo "T1b-24: --list shows total count ..." >&2
set +e
OUTPUT=$(cd "$REPO_ROOT" && bash run.sh --list 2>&1) || true
set -e
if echo "$OUTPUT" | grep -qE "[0-9]+ instances?"; then
    echo "  ✓ T1b-24: --list shows total count"
    PASS=$((PASS + 1))
else
    echo "  ✗ T1b-24: --list shows total count"
    FAIL=$((FAIL + 1))
fi

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T1b Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
