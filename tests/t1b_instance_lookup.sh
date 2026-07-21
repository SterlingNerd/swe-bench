#!/bin/bash
# ==============================================================================
# T1b — Instance Lookup & Dataset Cache Tests
#
# Tests get_instance() and fetch_dataset cache validation logic.
# No Docker required.
#
# Log:  tests/t1b_instance_lookup.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
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

    if echo "$output" | grep -qF "$expected_pattern"; then
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
# Setup: Create mock cache files
# ==============================================================================

TEST_CACHE_DIR=$(mktemp -d /tmp/swe-bench-t1b.XXXXXX)

# Valid cache with known instances
cat > "${TEST_CACHE_DIR}/valid.json" <<'JSONEOF'
[
  {
    "instance_id": "django__django-11039",
    "repo": "django/django",
    "base_commit": "abc123def456",
    "problem_statement": "Test problem",
    "version": "3.0",
    "difficulty": "medium"
  },
  {
    "instance_id": "flask__flask-1000",
    "repo": "pallets/flask",
    "base_commit": "def456ghi789",
    "problem_statement": "Flask test problem",
    "version": "2.0",
    "difficulty": "easy"
  }
]
JSONEOF

# Empty cache
: > "${TEST_CACHE_DIR}/empty.json"

# Invalid JSON
echo "not valid json {{{" > "${TEST_CACHE_DIR}/invalid.json"

# Valid but empty list
echo "[]" > "${TEST_CACHE_DIR}/empty_list.json"

# Cleanup
cleanup_t1b() {
    rm -rf "$TEST_CACHE_DIR"
}
trap cleanup_t1b EXIT

echo "--- T1b Setup: Mock cache files created ---"
echo ""

# ==============================================================================
# 1b.1 — fetch_dataset Cache Validation
# ==============================================================================

echo "--- T1b.1: fetch_dataset Cache Validation ---"

run_test_output 01 "missing cache file triggers fetch attempt" \
    "grep -A10 'fetch_dataset()' '$REPO_ROOT/run.sh'" "needs_fetch=1"

run_test_output 02 "empty cache file triggers re-fetch" \
    "grep -A10 'fetch_dataset()' '$REPO_ROOT/run.sh'" "empty"

run_test_output 03 "invalid JSON triggers re-fetch" \
    "grep -A10 'fetch_dataset()' '$REPO_ROOT/run.sh'" "json.load"

run_test_output 04 "empty list triggers re-fetch" \
    "grep -A10 'fetch_dataset()' '$REPO_ROOT/run.sh'" "len(d)>0"

echo ""

# ==============================================================================
# 1b.2 — get_instance() Logic
# ==============================================================================

echo "--- T1b.2: get_instance() Logic ---"

run_test_output 10 "get_instance uses fetch_dataset" \
    "grep -A5 'get_instance()' '$REPO_ROOT/run.sh'" "fetch_dataset"

run_test_output 11 "get_instance filters by instance_id" \
    "grep -A10 'get_instance()' '$REPO_ROOT/run.sh'" "instance_id"

run_test_output 12 "get_instance prints error for missing instance" \
    "grep -A10 'get_instance()' '$REPO_ROOT/run.sh'" "Instance not found"

run_test_output 13 "get_instance returns full instance dict" \
    "grep -A10 'get_instance()' '$REPO_ROOT/run.sh'" "json.dumps(inst)"

run_test_output 14 "get_instance prints error for missing instance" \
    "grep -A10 'get_instance()' '$REPO_ROOT/run.sh'" "Instance not found"

run_test_output 15 "get_instance exits with code 1 on missing instance" \
    "grep -A10 'get_instance()' '$REPO_ROOT/run.sh'" "sys.exit(1)"

echo ""

# ==============================================================================
# 1b.3 — Dataset Structure Validation
# ==============================================================================

echo "--- T1b.3: Dataset Structure ---"

run_test_output 20 "--list shows instance IDs" \
    "(cd '$REPO_ROOT' && bash run.sh --list django)" "django__django"

run_test_output 21 "--list shows repo names" \
    "(cd '$REPO_ROOT' && bash run.sh --list flask)" "pallets__flask"

run_test_output 22 "--list shows version column" \
    "(cd '$REPO_ROOT' && bash run.sh --list flask)" "v2.3"

run_test_output 23 "--list shows difficulty column" \
    "(cd '$REPO_ROOT' && bash run.sh --list flask)" "<15 min fix"

run_test_output 24 "--list shows total count" \
    "(cd '$REPO_ROOT' && bash run.sh --list)" "Total:"

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T1b Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
