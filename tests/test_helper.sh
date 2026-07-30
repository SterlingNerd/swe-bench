#!/bin/bash
# ==============================================================================
# Test Helper — shared across all test files
# Provides check_output() to avoid SIGPIPE with set -o pipefail + grep -q
# ==============================================================================

# Check if output contains a pattern without SIGPIPE issues.
# Uses grep -c (counts matches) instead of grep -q (exits early),
# which avoids the SIGPIPE that occurs when pipefail is set.
check_output() {
    local output="$1"
    local pattern="$2"
    local count
    count=$(printf '%s\n' "$output" | grep -cF -- "$pattern" 2>/dev/null) || true
    [ "${count:-0}" -gt 0 ]
}
