#!/bin/bash
# ==============================================================================
# SWE-bench Orchestrator — Test Helper
#
# Extracts and sources run.sh functions in a test-friendly environment.
# Strips the exec tee redirect, main() call, and global side effects.
# ==============================================================================

set -euo pipefail

REPO_ROOT="${SWE_BENCH_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUN_SH="${REPO_ROOT}/run.sh"

# Extract function definitions from run.sh (strip exec, main call, and global assignments we don't need)
extract_functions() {
    # Remove: exec > tee line, main() call at bottom, and any lines that must run once
    sed -n '
        /^exec > /d
        /^if \[\[.*BASH_SOURCE.*== \$0 \]\]/,$ d
        p
    ' "$RUN_SH"
}

# Source run.sh with a test-friendly workspace
# Usage: source_test_run_sh [optional overrides]
source_test_run_sh() {
    # Set up test workspace if not already set
    export SWE_WORKSPACE_DIR="${SWE_WORKSPACE_DIR:-$(mktemp -d /tmp/swe-bench-test.XXXXXX)}"
    
    # Source the extracted functions with our test overrides
    eval "$(extract_functions)"
}

# Create a mock dataset cache file
create_mock_cache() {
    local cache_file="${1:-/tmp/mock_swe_cache.json}"
    cat > "$cache_file" <<'JSONEOF'
[
  {
    "instance_id": "django__django-11039",
    "repo": "django/django",
    "base_commit": "abc123def456",
    "problem_statement": "Test problem statement",
    "version": "3.0",
    "difficulty": "medium"
  },
  {
    "instance_id": "django__django-12000",
    "repo": "django/django",
    "base_commit": "def456ghi789",
    "problem_statement": "Another test problem",
    "version": "3.0",
    "difficulty": "hard"
  },
  {
    "instance_id": "flask__flask-1000",
    "repo": "pallets/flask",
    "base_commit": "ghi789jkl012",
    "problem_statement": "Flask test problem",
    "version": "2.0",
    "difficulty": "easy"
  }
]
JSONEOF
    echo "$cache_file"
}

# Create a mock agent bundle for testing
create_mock_agent() {
    local agents_dir="${1:-${REPO_ROOT}/agents}"
    local agent_name="${2:-test-agent}"
    local agent_path="${agents_dir}/${agent_name}"
    
    mkdir -p "${agent_path}/bundle"
    
    # Create a minimal build_bundle.sh
    cat > "${agent_path}/build_bundle.sh" <<'BUILDEOF'
#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Mock bundle built at $BUNDLE_DIR"
BUILDEOF
    chmod +x "${agent_path}/build_bundle.sh"
    
    # Create a minimal entrypoint.sh
    cat > "${agent_path}/entrypoint.sh" <<'ENTRYEOF'
#!/bin/bash
set -euo pipefail
INSTANCE_ID="$1"
REPO_URL="$2"
BASE_COMMIT="$3"
PROBLEM_STATEMENT="$4"
OUTPUT_DIR="${SWE_OUTPUT_ROOT:-/workspace/outputs}/${INSTANCE_ID}"
mkdir -p "$OUTPUT_DIR"
echo "Mock agent ran for ${INSTANCE_ID}" > "${OUTPUT_DIR}/agent_output.txt"
echo "" > "${OUTPUT_DIR}/patch.diff"
cat > "${OUTPUT_DIR}/result.json" <<RESULTEOF
{
  "status": "no_patch",
  "patch_bytes": 0,
  "elapsed_seconds": 1,
  "agent_exit_code": 0
}
RESULTEOF
ENTRYEOF
    chmod +x "${agent_path}/entrypoint.sh"
    
    echo "$agent_path"
}

# Assert helper
assert_eq() {
    local description="$1"
    local expected="$2"
    local actual="$3"
    if [ "$expected" = "$actual" ]; then
        return 0
    else
        echo "FAIL: ${description}: expected='${expected}', got='${actual}'"
        return 1
    fi
}

assert_file_exists() {
    local description="$1"
    local path="$2"
    if [ -f "$path" ]; then
        return 0
    else
        echo "FAIL: ${description}: file not found at ${path}"
        return 1
    fi
}

assert_file_contains() {
    local description="$1"
    local path="$2"
    local pattern="$3"
    if grep -q "$pattern" "$path" 2>/dev/null; then
        return 0
    else
        echo "FAIL: ${description}: pattern '${pattern}' not found in ${path}"
        return 1
    fi
}
