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
    # Write output to a temp file to avoid subshell issues
    local tmpfile
    tmpfile=$(mktemp)
    printf '%s\n' "$output" > "$tmpfile"
    local count
    # Try grep -E first (regex), then grep -F (fixed string)
    count=$(grep -cE -- "$pattern" "$tmpfile" 2>/dev/null) || \
    count=$(grep -cF -- "$pattern" "$tmpfile" 2>/dev/null) || \
    count=0
    rm -f "$tmpfile"
    [ "$count" -gt 0 ]
}
