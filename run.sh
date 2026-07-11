#!/bin/bash
# ==============================================================================
# SWE-bench Orchestrator — unified build, index, list, run, and eval
#
# Usage:
#   ./run.sh --help              Show this help
#   ./run.sh --index             Fetch and cache problem listings from HuggingFace
#   ./run.sh --list [filter]     List problems (optional grep filter)
#   ./run.sh --build             Build all Docker images
#   ./run.sh --run <agent> <id>  Run an agent against a specific instance
#   ./run.sh --run-all <agent>   Run agent against all 500 instances
#   ./run.sh --eval <agent>      Evaluate collected patches for an agent
#   ./run.sh --interactive       Start interactive container for manual debugging
#
# Workflow:
#   1. --run / --run-all  → collects patches to outputs/<id>/
#   2. --eval              → runs swebench harness with Docker access on collected patches
# ==============================================================================

set -euo pipefail

# ==============================================================================
# CONFIGURATION
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENTS_DIR="${REPO_ROOT}/agents"
OUTPUT_DIR="${SWE_OUTPUT_DIR:-${WORKSPACE_DIR}/outputs}"
WORKSPACE_DIR="${SWE_WORKSPACE_DIR:-${REPO_ROOT}/workspace}"
CACHE_FILE="/tmp/swe_verified_cache.json"
HF_DATASET="princeton-nlp/SWE-bench_Verified"

# Image names
BASE_IMAGE="swe-base"
AGENT_IMAGES=()
for agent_dir in "${AGENTS_DIR}"/*/; do
    [ -d "$agent_dir" ] || continue
    agent_name=$(basename "$agent_dir")
    AGENT_IMAGES+=("swe-${agent_name}")
done

# ==============================================================================
# DATASET — fetch and cache from HuggingFace
# ==============================================================================
fetch_dataset() {
    if [ ! -f "$CACHE_FILE" ]; then
        echo "Fetching dataset from HuggingFace (first time, may take a moment)..."
        docker run --rm python:3.10-slim bash -c "
pip install -q datasets >/dev/null 2>&1
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

# ==============================================================================
# HELP
# ==============================================================================
show_help() {
    cat <<EOF
Usage: ./run.sh [OPTIONS]

Commands:
  --index                Fetch and cache problem listings from HuggingFace
  --list [filter]        List problems (optional grep filter on any field)
  --build                Build all Docker images (base + all agents)
  --run <agent> <id>     Run an agent against a specific instance
                         agent: pi, codex, claude, etc. (folder name under agents/)
                         id: instance_id (e.g., django__django-11039)
                         Collects patch to outputs/<id>/ — no Docker needed.
  --run-all <agent>      Run agent against all 500 verified instances
                         Collects patches to outputs/<id>/ — no Docker needed.
  --eval <agent>         Evaluate collected patches for an agent
                         Runs swebench harness with Docker access.

Environment:
  SWE_OUTPUT_DIR         Output directory (default: ./outputs)
  SWE_WORKSPACE_DIR      Workspace directory (default: ./workspace)

Examples:
  ./run.sh --index
  ./run.sh --list "django"
  ./run.sh --build
  ./run.sh --interactive
  ./run.sh --run pi django__django-11039
  ./run.sh --run-all pi
  ./run.sh --eval pi
EOF
}

# ==============================================================================
# INDEX — fetch and cache dataset
# ==============================================================================
do_index() {
    echo "=== Indexing SWE-bench Verified ==="
    fetch_dataset
    local count
    count=$(python3 -c "import json; print(len(json.load(open('${CACHE_FILE}'))))")
    echo "Cached ${count} instances at ${CACHE_FILE}"
}

# ==============================================================================
# LIST — list problems
# ==============================================================================
do_list() {
    local filter="${1:-}"
    echo "=== SWE-bench Verified Instances ==="
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

# ==============================================================================
# BUILD — build all Docker images
# ==============================================================================
do_build() {
    echo "=== Building Docker Images ==="

    # Build base image
    if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
        echo "Building ${BASE_IMAGE}..."
        docker build -f "${AGENTS_DIR}/base/Dockerfile.base" -t "$BASE_IMAGE" "${REPO_ROOT}"
    else
        echo "${BASE_IMAGE} already exists."
    fi

    # Build agent images
    for agent_dir in "${AGENTS_DIR}"/*/; do
        [ -d "$agent_dir" ] || continue
        agent_name=$(basename "$agent_dir")
        image_name="swe-${agent_name}"
        dockerfile="${agent_dir}/Dockerfile.${agent_name}"

        # Try Dockerfile.agent_name first, fall back to Dockerfile.pi
        if [ -f "$dockerfile" ]; then
            df="$dockerfile"
        elif [ -f "${agent_dir}/Dockerfile.pi" ]; then
            df="${agent_dir}/Dockerfile.pi"
        else
            echo "WARNING: No Dockerfile found for agent '${agent_name}', skipping."
            continue
        fi

        if ! docker image inspect "$image_name" >/dev/null 2>&1; then
            echo "Building ${image_name}..."
            docker build -f "$df" -t "$image_name" "${agent_dir}"
        else
            echo "${image_name} already exists."
        fi
    done

    echo ""
    echo "=== Built Images ==="
    docker images | grep -E "^(${BASE_IMAGE}|swe-)" || true
}

# ==============================================================================
# RUN — run an agent against a specific instance
# ==============================================================================
do_run() {
    local agent="${1:?Usage: $0 --run <agent> <instance_id>}"
    local instance_id="${2:?Usage: $0 --run <agent> <instance_id>}"

    # Validate agent exists
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found. Available agents:"
        for d in "${AGENTS_DIR}"/*/; do
            [ -d "$d" ] && echo "  $(basename "$d")"
        done
        exit 1
    fi

    # Validate image exists
    local image_name="swe-${agent}"
    if ! docker image inspect "$image_name" >/dev/null 2>&1; then
        echo "ERROR: Image '${image_name}' not found. Run './run.sh --build' first."
        exit 1
    fi

    # Get instance data
    local inst_data
    inst_data=$(get_instance "$instance_id")
    local repo_url base_commit problem_statement
    repo_url=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['repo'].replace('/', '/'))")
    base_commit=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['base_commit'])")
    problem_statement=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['problem_statement'])")

    # Run the agent container (patch collection only — no Docker needed)
    echo "=============================================================================="
    echo "Running: ${agent} against ${instance_id}"
    echo "=============================================================================="

    docker run --rm \
        --name "swe_${agent}_${instance_id}" \
        --memory 8g \
        --memory-swap 8g \
        --pids-limit 500 \
        --tmpfs /tmp:rw,noexec,nosuid,size=2g \
        --cap-drop ALL \
        --cap-add NET_RAW \
        --security-opt no-new-privileges:true \
        --add-host host.docker.internal:host-gateway \
        -v "${WORKSPACE_DIR}:/home/agent/workspace:rw" \
        -v "${REPO_ROOT}/agents/${agent}/.pi/auth.json:/home/agent/.pi/auth.json:ro" \
        "$image_name" \
        "${instance_id}" \
        "https://github.com/${repo_url}" \
        "${base_commit}" \
        "${problem_statement}"
}

# ==============================================================================
# RUN-ALL — run agent against all instances
# ==============================================================================
do_run_all() {
    local agent="${1:?Usage: $0 --run-all <agent>}"

    # Validate agent exists
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found."
        exit 1
    fi

    # Get all instance IDs
    fetch_dataset | python3 -c "
import sys, json
data = json.load(sys.stdin)
for inst in data:
    print(inst['instance_id'])
" | while read -r instance_id; do
        do_run "$agent" "$instance_id"
    done
}

# ==============================================================================
# EVAL — evaluate collected patches using swebench harness
# ==============================================================================
do_eval() {
    local agent="${1:?Usage: $0 --eval <agent>}"
    local image_name="swe-${agent}"

    # Validate agent exists
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found. Available agents:"
        for d in "${AGENTS_DIR}"/*/; do
            [ -d "$d" ] && echo "  $(basename "$d")"
        done
        exit 1
    fi

    # Validate image exists
    if ! docker image inspect "$image_name" >/dev/null 2>&1; then
        echo "ERROR: Image '${image_name}' not found. Run './run.sh --build' first."
        exit 1
    fi

    # Find all collected output directories for this agent
    local eval_dir="${OUTPUT_DIR}"
    if [ ! -d "$eval_dir" ]; then
        echo "No outputs found. Run instances first (--run or --run-all)."
        return
    fi

    local instance_ids=()
    for instance_dir in "${eval_dir}"/*/; do
        [ -d "$instance_dir" ] || continue
        # Only evaluate directories that have a patch (not just meta)
        if [ -f "${instance_dir}patch.diff" ]; then
            instance_ids+=("$(basename "$instance_dir")")
        fi
    done

    if [ ${#instance_ids[@]} -eq 0 ]; then
        echo "No patches found to evaluate. Run instances first."
        return
    fi

    echo "=============================================================================="
    echo "Evaluating ${#instance_ids[@]} patch(es) for '${agent}'"
    echo "=============================================================================="

    # Build predictions.json from all collected patches
    local tmp_preds=$(mktemp)
    python3 -c "
import sys, json, os
results = []
for instance_id in sys.argv[1:]:
    patch_path = os.path.join('${eval_dir}', instance_id, 'patch.diff')
    if not os.path.exists(patch_path):
        continue
    with open(patch_path) as f:
        patch = f.read()
    results.append({
        'instance_id': instance_id,
        'model_name_or_path': '${agent}',
        'model_patch': patch
    })
with open('${tmp_preds}', 'w') as f:
    json.dump(results, f)
print(f'Collected {len(results)} patches')
" "${instance_ids[@]}"

    # Run evaluation directly on the host (swebench harness creates its own
    # test containers as needed — no need to wrap in another container).
    python3 -m swebench.harness.run_evaluation \
        --dataset_name princeton-nlp/SWE-bench_Verified \
        --predictions_path "$tmp_preds" \
        --max_workers 1 \
        --namespace '' \
        2>&1 | tee "${eval_dir}/eval.log"

    rm -f "$tmp_preds"
}

# ==============================================================================
# INTERACTIVE — start interactive container for manual debugging
# ==============================================================================
do_interactive() {
    local agent="${1:-pi}"
    local image_name="swe-${agent}"

    if ! docker image inspect "$image_name" >/dev/null 2>&1; then
        echo "ERROR: Image '${image_name}' not found. Run './run.sh --build' first."
        exit 1
    fi

    echo "Starting interactive container for '${agent}'..."
    docker run -it \
        --name "pi_swe_evaluator" \
        --memory 8g \
        --memory-swap 8g \
        --pids-limit 500 \
        --tmpfs /tmp:rw,noexec,nosuid,size=2g \
        --tmpfs /home/agent/workspace:rw,noexec,nosuid,size=4g \
        --cap-drop ALL \
        --cap-add NET_RAW \
        --security-opt no-new-privileges:true \
        --add-host host.docker.internal:host-gateway \
        -v "${WORKSPACE_DIR}:/home/agent/workspace:rw" \
        -v "${REPO_ROOT}/agents/${agent}/.pi/auth.json:/home/agent/.pi/auth.json:ro" \
        "$image_name"
}

# ==============================================================================
# STATUS — show completion status
# ==============================================================================
do_status() {
    echo "=== SWE-bench Harness Status ==="
    echo "Output directory: ${OUTPUT_DIR}"
    echo ""

    local total=0 resolved=0 failed=0 no_patch=0 unknown=0

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
# MAIN
# ==============================================================================
if [ $# -eq 0 ]; then
    show_help
    exit 0
fi

case "$1" in
    --help|-h)
        show_help
        ;;
    --index)
        do_index
        ;;
    --list)
        do_list "${2:-}"
        ;;
    --build)
        do_build
        ;;
    --run)
        do_run "${2:-}" "${3:-}"
        ;;
    --run-all)
        do_run_all "${2:-}"
        ;;
    --eval)
        do_eval "${2:-}"
        ;;
    --status)
        do_status
        ;;
    --interactive|-i)
        do_interactive "${2:-pi}"
        ;;
    *)
        echo "Unknown option: $1"
        show_help
        exit 1
        ;;
esac
