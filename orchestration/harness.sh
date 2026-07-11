#!/bin/bash
# ==============================================================================
# SWE-bench Verified Harness — run pi against any subset of instances
#
# Usage:
#   ./harness.sh <instance_id>...    Run specific instances
#   ./harness.sh --all               Run all 500 verified instances
#   ./harness.sh --list [filter]     List available instances (grep filter)
#   ./harness.sh --info <id>         Show problem details for one instance
#   ./harness.sh --status            Show completion status of all instances
# ==============================================================================

set -euo pipefail

# ==============================================================================
# CONFIGURATION
# ==============================================================================
OUTPUT_DIR="${SWE_OUTPUT_DIR:-$(pwd)/outputs}"
WORKSPACE_DIR="${SWE_WORKSPACE_DIR:-$(pwd)/workspace}"
REPOS_DIR="${WORKSPACE_DIR}/repos"
CONTAINER_NAME="swe_harness_runner"
HF_DATASET="princeton-nlp/SWE-bench_Verified"

# ==============================================================================
# INSTANCE DATA — fetched from HuggingFace on demand
# ==============================================================================
CACHE_FILE="/tmp/swe_verified_cache.json"

fetch_dataset() {
    if [ ! -f "$CACHE_FILE" ]; then
        echo "Fetching dataset from HuggingFace (first time, may take a moment)..."
        docker run --rm python:3.10-slim bash -c "
pip install datasets -q 2>&1 | tail -1
python3 -c \"
from datasets import load_dataset
import json
ds = load_dataset('${HF_DATASET}', split='test')
data = [dict(i) for i in ds]
print(json.dumps(data))
\"
" > "$CACHE_FILE" 2>/dev/null
    fi
    cat "$CACHE_FILE"
}

get_instance() {
    local instance_id="$1"
    fetch_dataset | python3 -c "
import sys, json
data = json.load(sys.stdin)
for inst in data:
    if inst['instance_id'] == '${instance_id}':
        print(json.dumps(inst))
        sys.exit(0)
print('ERROR: Instance not found: ${instance_id}', file=sys.stderr)
sys.exit(1)
"
}

list_instances() {
    local filter="${1:-}"
    fetch_dataset | python3 -c "
import sys, json
data = json.load(sys.stdin)
if '${filter}':
    data = [i for i in data if '${filter}'.lower() in str(i).lower()]
for inst in sorted(data, key=lambda x: (x['repo'], x['version'])):
    print(f\"{inst['instance_id']:40s} {inst['repo']:30s} v{inst['version']:10s} {inst['difficulty']:20s}\")
print(f'\nTotal: {len(data)} instances')
"
}

show_info() {
    local instance_id="$1"
    local inst
    inst=$(get_instance "$instance_id")
    echo "=== Instance: $instance_id ==="
    echo "$inst" | python3 -c "
import sys, json
inst = json.load(sys.stdin)
print(f'Repo:        {inst[\"repo\"]}')
print(f'Version:     {inst[\"version\"]}')
print(f'Difficulty:  {inst[\"difficulty\"]}')
print(f'Created:     {inst[\"created_at\"]}')
print(f'Base commit: {inst[\"base_commit\"]}')
print()
print('--- Problem Statement ---')
print(inst['problem_statement'])
if inst.get('hints_text'):
    print()
    print('--- Hints ---')
    print(inst['hints_text'][:500])
print()
print(f'FAIL_TO_PASS tests: {len(inst[\"FAIL_TO_PASS\"])}')
for t in inst['FAIL_TO_PASS'][:5]:
    print(f'  - {t}')
if len(inst['FAIL_TO_PASS']) > 5:
    print(f'  ... and {len(inst[\"FAIL_TO_PASS\"]) - 5} more')
print()
print(f'PASS_TO_PASS tests: {len(inst[\"PASS_TO_PASS\"])}')
"
}

# ==============================================================================
# INSTANCE WORKFLOW — clone, run pi, extract patch, eval
# ==============================================================================
run_instance() {
    local instance_id="$1"
    local instance_dir="${OUTPUT_DIR}/${instance_id}"
    local repo_dir="${REPOS_DIR}/$(echo "$instance_id" | awk -F'__' '{print $1}')"
    local start_time
    start_time=$(date +%s)

    echo "=============================================================================="
    echo "Running: ${instance_id}"
    echo "=============================================================================="

    # Create output directory structure
    mkdir -p "${instance_dir}/eval"

    # Save problem statement
    get_instance "$instance_id" | python3 -c "
import sys, json
inst = json.load(sys.stdin)
with open('${instance_dir}/problem_statement.txt', 'w') as f:
    f.write(inst['problem_statement'])
if inst.get('hints_text'):
    with open('${instance_dir}/hints.txt', 'w') as f:
        f.write(inst['hints_text'])
print(json.dumps({'base_commit': inst['base_commit'], 'repo': inst['repo'], 'version': inst['version']}))
" > "${instance_dir}/meta.json"

    local base_commit
    base_commit=$(python3 -c "import json; print(json.load(open('${instance_dir}/meta.json'))['base_commit'])")
    local repo_name
    repo_name=$(python3 -c "import json; print(json.load(open('${instance_dir}/meta.json'))['repo'])")

    # Clone repo at base commit (skip if already cloned)
    if [ ! -d "$repo_dir" ]; then
        echo "  Cloning ${repo_name} @ ${base_commit:0:8}..."
        mkdir -p "${REPOS_DIR}"
        git clone "https://github.com/${repo_name}.git" "$repo_dir" 2>&1 | tail -1
        cd "$repo_dir" && git checkout "$base_commit" >/dev/null 2>&1
        cd - >/dev/null
    else
        echo "  Repo already cloned. Checking out ${base_commit:0:8}..."
        cd "$repo_dir" && git checkout "$base_commit" >/dev/null 2>&1
        cd - >/dev/null
    fi

    # Run pi in non-interactive mode inside the container
    echo "  Running pi (non-interactive)..."
    local session_file="${instance_dir}/session.jsonl"
    local pi_output="${instance_dir}/pi_output.txt"

    docker run --rm \
        --name "${CONTAINER_NAME}_${instance_id}" \
        --memory 8g \
        --memory-swap 8g \
        --pids-limit 500 \
        --tmpfs /tmp:rw,noexec,nosuid,size=2g \
        --cap-drop ALL \
        --cap-add NET_RAW \
        --security-opt no-new-privileges:true \
        --add-host host.docker.internal:host-gateway \
        -v "${WORKSPACE_DIR}:/home/agent/workspace:rw" \
        -v "$(pwd)/.pi/auth.json:/home/agent/.pi/auth.json:ro" \
        swe-pi-sandbox \
        bash -c "
cd /home/agent/workspace/repos/$(echo '${instance_id}' | awk -F'__' '{print \$1}')
export PI_OFFLINE=1
# Run pi with the problem statement, capture output and session
pi -p --session-dir /tmp/pi-sessions --session-id '${instance_id}' \
    \"You are fixing a bug in this codebase. Read the problem statement below, understand the code, make the fix, and then run git diff to produce your patch.

PROBLEM STATEMENT:
$(get_instance "$instance_id" | python3 -c "import sys,json; print(json.load(sys.stdin)['problem_statement'])")

Instructions:
1. Read the problem statement carefully
2. Explore the codebase to understand the issue
3. Make the necessary code changes
4. Run 'git diff' and save the output to /home/agent/workspace/${instance_dir}/patch.diff
5. Exit when done\" 2>&1 | tee ${pi_output}

# Save session file (copied to workspace volume before container stops)
if [ -f /tmp/pi-sessions/${instance_id}/session.jsonl ]; then
    cp /tmp/pi-sessions/${instance_id}/session.jsonl /home/agent/workspace/${instance_dir}/session.jsonl 2>/dev/null || true
fi
" 2>&1 | tee "${pi_output}" || true

    # Extract patch via git diff (more reliable than parsing pi output)
    echo "  Extracting patch..."
    cd "$repo_dir" && git diff > "${instance_dir}/patch.diff" 2>/dev/null || true
    cd - >/dev/null

    # Check if we got a non-empty patch
    local patch_size=0
    if [ -f "${instance_dir}/patch.diff" ]; then
        patch_size=$(wc -c < "${instance_dir}/patch.diff")
    fi

    if [ "$patch_size" -eq 0 ]; then
        echo "  WARNING: No patch generated (0 bytes)"
        echo '{"status": "no_patch", "patch_bytes": 0}' > "${instance_dir}/result.json"
    else
        # Evaluate the patch using swebench harness
        echo "  Evaluating patch (${patch_size} bytes)..."

        # Create predictions JSON
        python3 -c "
import json, sys
with open('${instance_dir}/patch.diff', 'r') as f:
    patch = f.read()
# Escape for JSON
patch = patch.replace('\\\\', '\\\\\\\\').replace('\"', '\\\\\"').replace('\n', '\\\\n')
pred = [{
    'instance_id': '${instance_id}',
    'model_name_or_path': 'pi_local_agent',
    'model_patch': patch
}]
with open('${instance_dir}/eval/predictions.json', 'w') as f:
    json.dump(pred, f)
"

        # Run evaluation
        python3 -m swebench.harness.run_evaluation \
            --dataset_name "$HF_DATASET" \
            --predictions_path "${instance_dir}/eval/predictions.json" \
            --max_workers 1 \
            --namespace "" \
            2>&1 | tee "${instance_dir}/eval/harness.log" || true

        # Parse result
        local status="unknown"
        if grep -q '"resolved": true' "${instance_dir}/eval/harness.log" 2>/dev/null; then
            status="resolved"
        elif grep -q '"no_test_changes"' "${instance_dir}/eval/harness.log" 2>/dev/null; then
            status="no_test_changes"
        elif grep -q '"failed"' "${instance_dir}/eval/harness.log" 2>/dev/null; then
            status="failed"
        fi

        local end_time
        end_time=$(date +%s)
        local elapsed=$((end_time - start_time))

        echo '{"status": "'$status'", "patch_bytes": '$patch_size', "elapsed_seconds": '$elapsed'}' > "${instance_dir}/result.json"
        echo "  Result: ${status} (${elapsed}s)"
    fi

    echo "  Output saved to: ${instance_dir}/"
    echo ""
}

# ==============================================================================
# STATUS — check which instances have been completed
# ==============================================================================
show_status() {
    echo "=== SWE-bench Harness Status ==="
    echo "Output directory: ${OUTPUT_DIR}"
    echo ""

    local total=0
    local resolved=0
    local failed=0
    local no_patch=0
    local unknown=0

    if [ ! -d "$OUTPUT_DIR" ]; then
        echo "No outputs found. Run instances first."
        return
    fi

    for instance_dir in "${OUTPUT_DIR}"/*/; do
        [ -d "$instance_dir" ] || continue
        local instance_id=$(basename "$instance_dir")
        total=$((total + 1))

        if [ -f "${instance_dir}result.json" ]; then
            local status
            status=$(python3 -c "import json; print(json.load(open('${instance_dir}result.json'))['status'])" 2>/dev/null || echo "unknown")
            case "$status" in
                resolved) resolved=$((resolved + 1)); echo -e "\033[32m✓\033[0m $instance_id ($status)" ;;
                failed)   failed=$((failed + 1));   echo -e "\033[31m✗\033[0m $instance_id ($status)" ;;
                no_patch) no_patch=$((no_patch + 1)); echo -e "\033[33m—\033[0m $instance_id (no patch)" ;;
                *)        unknown=$((unknown + 1));   echo -e "\033[90m?\033[0m $instance_id ($status)" ;;
            esac
        else
            unknown=$((unknown + 1))
            echo -e "\033[90m?\033[0m $instance_id (no result)"
        fi
    done

    echo ""
    echo "Total: $total | Resolved: $resolved | Failed: $failed | No patch: $no_patch | Unknown: $unknown"
}

# ==============================================================================
# PRE-FLIGHT CHECKS
# ==============================================================================
preflight() {
    # Check Docker is available
    if ! command -v docker &>/dev/null; then
        echo "ERROR: Docker is not installed or not in PATH"
        exit 1
    fi

    # Check swe-pi-sandbox image exists
    if ! docker image inspect swe-pi-sandbox &>/dev/null; then
        echo "ERROR: Image 'swe-pi-sandbox' not found. Run './run.sh' first to build images."
        exit 1
    fi

    # Check workspace directory
    mkdir -p "${WORKSPACE_DIR}" "${OUTPUT_DIR}"
}

# ==============================================================================
# MAIN
# ==============================================================================
if [ $# -eq 0 ]; then
    cat <<EOF
Usage: ./harness.sh [OPTIONS] [INSTANCE_ID...]

Commands:
  --list [filter]        List available instances (optional grep filter)
  --info <instance_id>   Show problem details for one instance
  --status               Show completion status of all run instances
  --all                  Run all 500 verified instances
  <instance_id>...       Run specific instances (e.g., django__django-11039)

Environment:
  SWE_OUTPUT_DIR         Output directory (default: ./outputs)
  SWE_WORKSPACE_DIR      Workspace directory (default: ./workspace)

Examples:
  ./harness.sh --list "django"
  ./harness.sh --info django__django-11039
  ./harness.sh django__django-11039 pytest-dev__pytest-7407
  ./harness.sh --all
EOF
    exit 0
fi

preflight

case "$1" in
    --list)
        list_instances "${2:-}"
        ;;
    --info)
        if [ -z "${2:-}" ]; then
            echo "Error: --info requires an instance_id"
            exit 1
        fi
        show_info "$2"
        ;;
    --status)
        show_status
        ;;
    --all)
        # Get all instance IDs and run them
        fetch_dataset | python3 -c "
import sys, json
data = json.load(sys.stdin)
for inst in data:
    print(inst['instance_id'])
" | while read -r instance_id; do
            run_instance "$instance_id"
        done
        ;;
    --*)
        echo "Unknown option: $1"
        exit 1
        ;;
    *)
        # Run specified instances
        for instance_id in "$@"; do
            run_instance "$instance_id"
        done
        ;;
esac
