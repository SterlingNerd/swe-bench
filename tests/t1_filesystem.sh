#!/bin/bash
# ==============================================================================
# T1 — Filesystem & Dataset Operations Tests
#
# Tests dataset cache, instance lookup, index/list, bundle build,
# cleanup-partial, and other filesystem operations.
# Docker only needed for --build (bundle building).
#
# Log:  tests/t1_filesystem.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/t1_filesystem.log"
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

echo "=== T1 Filesystem & Dataset Tests ==="
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

    echo "T1-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T1-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T1-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T1-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if echo "$output" | grep -qF "$expected_pattern"; then
        echo "  ✓ T1-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T1-${id}: ${name} (pattern '${expected_pattern}' not found)"
        if [ "$VERBOSE" -eq 1 ]; then
            echo "    Output was:"
            sed 's/^/      /' <<< "$output"
        fi
        FAIL=$((FAIL + 1))
    fi
}

# ==============================================================================
# Setup: Create test workspace with mock data and agent
# ==============================================================================

TEST_WORKSPACE=$(mktemp -d /tmp/swe-bench-t1.XXXXXX)
TEST_OUTPUTS="${TEST_WORKSPACE}/outputs"
TEST_AGENTS="${TEST_WORKSPACE}/agents"
mkdir -p "$TEST_OUTPUTS/pi/django__django-11039"
mkdir -p "$TEST_OUTPUTS/pi/django__django-12000"
mkdir -p "$TEST_OUTPUTS/flask__flask-1000"

# Create mock agent with bundle
mkdir -p "$TEST_AGENTS/pi/bundle"
cat > "$TEST_AGENTS/pi/build_bundle.sh" <<'BUILDEOF'
#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Mock bundle built at $BUNDLE_DIR"
BUILDEOF
chmod +x "$TEST_AGENTS/pi/build_bundle.sh"

# Create mock agent without bundle (for testing skip behavior)
mkdir -p "$TEST_AGENTS/nobundle"

# Cleanup on exit
cleanup_t1() {
    rm -rf "$TEST_WORKSPACE"
}
trap cleanup_t1 EXIT

echo "--- T1 Setup: Test workspace at ${TEST_WORKSPACE} ---"
echo ""

# ==============================================================================
# 1.1 — Dataset Cache (fetch_dataset behavior)
# ==============================================================================

echo "--- T1.1: Dataset Cache ---"

# These tests verify the cache validation logic by checking the script's behavior
run_test_output 01 "fetch_dataset checks for missing cache file" \
    "grep -A5 'fetch_dataset()' '$REPO_ROOT/run.sh'" "needs_fetch=1"

run_test_output 02 "fetch_dataset checks for empty cache file" \
    "grep -A5 'fetch_dataset()' '$REPO_ROOT/run.sh'" "empty"

run_test_output 03 "fetch_dataset checks for invalid JSON" \
    "grep -A10 'fetch_dataset()' '$REPO_ROOT/run.sh'" "json.load"

run_test_output 04 "--list uses cached data when available" \
    "(cd '$REPO_ROOT' && bash run.sh --list django 2>&1 | head -3)" "django__django"

echo ""

# ==============================================================================
# 1.2 — Index & List (do_index, do_list)
# ==============================================================================

echo "--- T1.2: Index & List ---"

run_test_output 05 "--index prints cached count" \
    "(cd '$REPO_ROOT' && bash run.sh --index)" "Cached"

run_test_output 06 "--list without filter shows instances" \
    "(cd '$REPO_ROOT' && bash run.sh --list)" "Total:"

run_test_output 07 "--list django filters to django instances" \
    "(cd '$REPO_ROOT' && bash run.sh --list django)" "django__django"

run_test_output 08 "--list flask filters to flask instances" \
    "(cd '$REPO_ROOT' && bash run.sh --list flask)" "pallets__flask"

run_test_output 09 "--list nonexistent shows 0 results" \
    "(cd '$REPO_ROOT' && bash run.sh --list NONEXISTENTXYZ)" "Total: 0"

echo ""

# ==============================================================================
# 1.3 — Build Agent Bundle (do_build, build_agent_bundle)
# ==============================================================================

echo "--- T1.3: Build Agent Bundle ---"

run_test 10 "--build pi with existing bundle skips" \
    "(cd '$REPO_ROOT' && AGENTS_DIR='$TEST_AGENTS' bash run.sh --build pi)" 0

run_test_output 11 "--build nonexistent prints error" \
    "(cd '$REPO_ROOT' && AGENTS_DIR='$TEST_AGENTS' bash run.sh --build nonexistent)" "not found"

run_test_output 12 "--build with no agent builds all" \
    "(cd '$REPO_ROOT' && AGENTS_DIR='$TEST_AGENTS' bash run.sh --build)" "Built Bundles"

# Test that agent without build_bundle.sh is skipped (not an error)
# Note: AGENTS_DIR is hardcoded in run.sh, so we test by creating agent in actual agents dir
mkdir -p "${REPO_ROOT}/agents/no-build-test/bundle"
run_test 13 "agent without build_bundle.sh is skipped" \
    "(cd '$REPO_ROOT' && bash run.sh --build no-build-test)" 0
rm -rf "${REPO_ROOT}/agents/no-build-test"

echo ""

# ==============================================================================
# 1.4 — Rebuild Agent Bundle (do_rebuild)
# ==============================================================================

echo "--- T1.4: Rebuild Agent Bundle ---"

run_test 14 "--rebuild pi forces rebuild" \
    "(cd '$REPO_ROOT' && AGENTS_DIR='$TEST_AGENTS' bash run.sh --rebuild pi)" 0

run_test_output 15 "--rebuild nonexistent prints error" \
    "(cd '$REPO_ROOT' && AGENTS_DIR='$TEST_AGENTS' bash run.sh --rebuild nonexistent)" "Unknown rebuild target"

run_test_output 16 "--rebuild all rebuilds everything" \
    "(cd '$REPO_ROOT' && AGENTS_DIR='$TEST_AGENTS' bash run.sh --rebuild all)" "Built Bundles"

echo ""

# ==============================================================================
# 1.5 — Cleanup Partial (do_cleanup_partial)
# ==============================================================================

echo "--- T1.5: Cleanup Partial ---"

# Set up test scenarios
# Instance with both result.json and patch.diff → should be KEPT
cat > "$TEST_OUTPUTS/pi/django__django-11039/result.json" <<'EOF'
{"status": "resolved", "patch_bytes": 1234}
EOF
echo "some patch content" > "$TEST_OUTPUTS/pi/django__django-11039/patch.diff"

# Instance missing result.json → should be REMOVED
echo "some patch content" > "$TEST_OUTPUTS/flask__flask-1000/patch.diff"

# Instance with empty patch.diff (0 bytes) → BUG: currently kept
cat > "$TEST_OUTPUTS/pi/django__django-12000/result.json" <<'EOF'
{"status": "no_patch", "patch_bytes": 0}
EOF
: > "$TEST_OUTPUTS/pi/django__django-12000/patch.diff"

run_test_output 17 "cleanup-partial keeps complete instances" \
    "(cd '$REPO_ROOT' && SWE_WORKSPACE_DIR='$TEST_WORKSPACE' bash run.sh --cleanup-partial)" "complete"

# Create a fresh test dir for this specific test
TEST_WS_18=$(mktemp -d /tmp/swe-bench-t1-18.XXXXXX)
mkdir -p "$TEST_WS_18/outputs/incomplete-instance"
echo "patch" > "$TEST_WS_18/outputs/incomplete-instance/patch.diff"
run_test_output 18 "cleanup-partial removes incomplete instances" \
    "(cd '$REPO_ROOT' && SWE_WORKSPACE_DIR=\"$TEST_WS_18\" bash run.sh --cleanup-partial)" "Removing:"
rm -rf "$TEST_WS_18"

run_test_output 19 "cleanup-partial with no outputs dir returns cleanly" \
    "(cd '$REPO_ROOT' && SWE_WORKSPACE_DIR='/tmp/nonexistent_xyz' bash run.sh --cleanup-partial)" "No outputs directory"

echo ""

# ==============================================================================
# 1.6 — Storage Check Edge Cases
# ==============================================================================

echo "--- T1.6: Storage Check ---"

run_test_output 20 "check_storage function exists and uses df" \
    "grep -A5 'check_storage()' '$REPO_ROOT/run.sh'" "df --output=pcent"

run_test_output 21 "check_storage compares against MAX_STORAGE_PCT" \
    "grep -A5 'check_storage()' '$REPO_ROOT/run.sh'" "MAX_STORAGE_PCT"

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T1 Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
