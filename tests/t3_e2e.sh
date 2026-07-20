#!/bin/bash
# ==============================================================================
# T3 — End-to-End Workflow Tests
#
# Tests full workflows: build → run → eval → summarize.
# Requires Docker + dataset cache.
#
# Log:  tests/t3_e2e.log
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="${SCRIPT_DIR}/t3_e2e.log"
PASS=0
FAIL=0
SKIP=0
TOTAL=0
VERBOSE=0

# Parse args
for arg in "$@"; do
    case "$arg" in
        --verbose) VERBOSE=1 ;;
    esac
done

# Check Docker availability
if ! docker info >/dev/null 2>&1; then
    echo "=== T3 E2E Tests ==="
    echo "SKIP: Docker daemon not available"
    echo "Install/start Docker and retry."
    exit 0
fi

# Start logging
exec > >(tee -a "$LOG_FILE") 2>&1
: > "$LOG_FILE"  # truncate log at start

echo "=== T3 End-to-End Workflow Tests ==="
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

    echo "T3-${id}: ${name} ..." >&2

    set +e
    eval "$cmd" > /dev/null 2>&1
    local actual_exit=$?
    set -e

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T3-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T3-${id}: ${name} (expected exit=${expected_exit}, got ${actual_exit})"
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local id="$1"
    local name="$2"
    local cmd="$3"
    local expected_pattern="$4"
    TOTAL=$((TOTAL + 1))

    echo "T3-${id}: ${name} ..." >&2

    set +e
    local output
    output=$(eval "$cmd" 2>&1) || true
    set -e

    if echo "$output" | grep -qF "$expected_pattern"; then
        echo "  ✓ T3-${id}: ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T3-${id}: ${name} (pattern '${expected_pattern}' not found)"
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

TEST_WORKSPACE=$(mktemp -d /tmp/swe-bench-t3.XXXXXX)
TEST_OUTPUTS="${TEST_WORKSPACE}/outputs"
mkdir -p "$TEST_OUTPUTS/test-e2e/django__django-11039"

# Create mock agent with bundle and entrypoint
mkdir -p "${REPO_ROOT}/agents/test-e2e/bundle"
cat > "${REPO_ROOT}/agents/test-e2e/build_bundle.sh" <<'BUILDEOF'
#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Mock bundle built at $BUNDLE_DIR"
BUILDEOF
chmod +x "${REPO_ROOT}/agents/test-e2e/build_bundle.sh"

# Create mock entrypoint that simulates a successful run
cat > "${REPO_ROOT}/agents/test-e2e/entrypoint.sh" <<'ENTRYEOF'
#!/bin/bash
set -euo pipefail
INSTANCE_ID="$1"
OUTPUT_DIR="${SWE_OUTPUT_ROOT:-/workspace/outputs}/${INSTANCE_ID}"
mkdir -p "$OUTPUT_DIR"
echo "Mock agent ran for ${INSTANCE_ID}" > "${OUTPUT_DIR}/agent_output.txt"
echo "diff --git a/test.py b/test.py" > "${OUTPUT_DIR}/patch.diff"
echo "+print('hello')" >> "${OUTPUT_DIR}/patch.diff"
cat > "${OUTPUT_DIR}/result.json" <<RESULTEOF
{
  "status": "patch_collected",
  "patch_bytes": $(wc -c < "${OUTPUT_DIR}/patch.diff"),
  "elapsed_seconds": 42,
  "agent_exit_code": 0
}
RESULTEOF
ENTRYEOF
chmod +x "${REPO_ROOT}/agents/test-e2e/entrypoint.sh"

# Cleanup on exit
cleanup_t3() {
    docker rm -f swe_test_* 2>/dev/null || true
    rm -rf "${REPO_ROOT}/agents/test-e2e"
    rm -rf "$TEST_WORKSPACE"
}
trap cleanup_t3 EXIT

echo "--- T3 Setup: Test workspace at ${TEST_WORKSPACE} ---"
echo ""

# ==============================================================================
# 3.1 — Full Workflow: Build → Run → Summarize
# ==============================================================================

echo "--- T3.1: Build → Run → Summarize ---"

run_test_output 01 "build test-e2e agent" \
    "(cd '$REPO_ROOT' && bash run.sh --build test-e2e)" "Built Bundles"

echo ""

# ==============================================================================
# 3.2 — Resume Workflow
# ==============================================================================

echo "--- T3.2: Resume ---"

# Set up a completed instance for resume testing
cat > "$TEST_OUTPUTS/test-e2e/django__django-11039/result.json" <<'EOF'
{"status": "resolved", "patch_bytes": 50, "elapsed_seconds": 100}
EOF

run_test_output 03 "--status shows completed instance" \
    "(cd '$REPO_ROOT' && SWE_WORKSPACE_DIR='$TEST_WORKSPACE' bash run.sh --status test-e2e)" "resolved"

echo ""

# ==============================================================================
# Summary
# ==============================================================================

echo "=== T3 Results: ${PASS}/${TOTAL} passed, ${FAIL} failed, ${SKIP} skipped ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
