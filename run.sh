#!/bin/bash
# ==============================================================================
# SWE-bench Orchestrator — unified build, index, list, run, and eval
#
# Usage:
#   ./run.sh --help              Show this help
#   ./run.sh --index             Fetch and cache problem listings from HuggingFace
#   ./run.sh --list [filter]     List problems (optional grep filter)
#   ./run.sh --build [agent]     Build base + all (or one) agent Docker image(s)
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
# CLEANUP — remove any containers this script created on exit
# ==============================================================================
cleanup() {
    docker rm -f "$(docker ps -aq --filter 'name=swe_' 2>/dev/null)" 2>/dev/null || true
}
trap cleanup EXIT

# ==============================================================================
# CONFIGURATION
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
AGENTS_DIR="${REPO_ROOT}/agents"
OUTPUT_DIR="${SWE_WORKSPACE_DIR:-${REPO_ROOT}/workspace}/outputs"
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
    local needs_fetch=0
    if [ ! -f "$CACHE_FILE" ]; then
        needs_fetch=1
    elif [ ! -s "$CACHE_FILE" ]; then
        echo "Cache file is empty, re-fetching..."
        needs_fetch=1
    elif ! python3 -c "import json; d=json.load(open('$CACHE_FILE')); assert isinstance(d, list) and len(d)>0" 2>/dev/null; then
        echo "Cache file is corrupted, re-fetching..."
        needs_fetch=1
    fi

    if [ "$needs_fetch" -eq 1 ]; then
        echo "Fetching dataset from HuggingFace (may take a moment)..."
        docker run --rm -e HF_DATASET="${HF_DATASET}" python:3.10-slim bash -c '
pip install -q datasets >/dev/null 2>&1
python3 << "PYEOF"
from datasets import load_dataset
import json, os
ds = load_dataset(os.environ["HF_DATASET"], split="test")
data = [dict(i) for i in ds]
print(json.dumps(data))
PYEOF
' > "$CACHE_FILE" 2>/dev/null
    fi
    cat "$CACHE_FILE"
}

get_instance() {
    local instance_id="$1"
    fetch_dataset | INSTANCE_ID="${instance_id}" python3 -c "
import sys, json, os
data = json.load(sys.stdin)
for inst in data:
    if inst['instance_id'] == os.environ.get('INSTANCE_ID', ''):
        print(json.dumps(inst))
        sys.exit(0)
print(f\"ERROR: Instance not found: {os.environ['INSTANCE_ID']}\", file=sys.stderr)
sys.exit(1)
"
}

# ==============================================================================
# HELP
# ==============================================================================
show_help() {
    cat <<EOF
$(basename "$0") — SWE-bench Orchestrator
Unified build, index, list, run, evaluate, and inspect workflow for SWE-bench.

USAGE
  $(basename "$0") [COMMAND] [ARGS]
  $(basename "$0") --help            Show this help (also printed when run with no args)

COMMANDS
  --index
      Fetch the SWE-bench_Verified dataset from HuggingFace and cache it
      locally at /tmp/swe_verified_cache.json. Required before --list, --run,
      --run-all, and --eval (run it once; subsequent runs reuse the cache).

  --list [FILTER]
      Print all cached instances (instance_id, repo, version, difficulty),
      sorted by repo then version. Optional FILTER is a case-insensitive
      substring match against any field (e.g. a repo name or instance id).

  --build [AGENT]
      Build the Docker images. The shared 'swe-base' image is always built
      first, then each agent folder under agents/ (named 'swe-<agent>').
      If AGENT is given (e.g. pi, codex), only that agent's image is built;
      otherwise all agent images are built. Existing images are skipped.
      Run this before --run, --run-all, --eval, or --interactive.

  --run <AGENT> <INSTANCE_ID>
      Run an agent against a single instance. AGENT is a folder name under
      agents/ (e.g. pi, codex, claude). INSTANCE_ID is like django__django-11039.
      The agent runs inside its Docker image and writes a patch; the patch is
      collected to <workspace>/outputs/<INSTANCE_ID>/.
      Note: this only produces a patch — it does NOT run the test harness.

  --run-all <AGENT>
      Run an agent against every cached instance (all 500 verified instances),
      collecting one patch per instance to <workspace>/outputs/<INSTANCE_ID>/.
      Long-running: consider running in the background.

  --eval <AGENT>
      Evaluate the patches collected for AGENT in a previous --run/--run-all.
      Builds predictions.json from the collected patch.diff files and runs the
      swebench evaluation harness (which spins up its own test containers).
      Writes per-instance result.json (resolved/failed/no_patch) and eval.log.

  --status
      Summarize progress of collected runs: totals plus per-instance status
      (resolved / failed / no patch / unknown) read from result.json files.

  --interactive [AGENT]
      Start an interactive shell inside the agent's Docker image for manual
      debugging. AGENT defaults to 'pi' if not given.

ENVIRONMENT
  SWE_WORKSPACE_DIR
      Root of the workspace (default: ./workspace). The outputs directory and
      shared volume are derived from it:
        workspace  = \${SWE_WORKSPACE_DIR:-./workspace}
        outputs    = \$workspace/outputs

  (Note: there is no SWE_OUTPUT_DIR; outputs live under the workspace above.)

WORKFLOW
  1. ./run.sh --index          (one-time: cache the dataset)
  2. ./run.sh --build          (one-time: build Docker images)
  3. ./run.sh --run <agent> <id>  or  --run-all <agent>   (collect patches)
  4. ./run.sh --eval <agent>   (run the test harness on collected patches)
  5. ./run.sh --status         (inspect results)

PREREQUISITES
  - Docker must be installed and running (used for --build, --run, --run-all,
    --eval, --interactive, and the dataset fetch behind --index/--list).
  - A HuggingFace dataset fetch happens on first --index / --list.

EXAMPLES
  $(basename "$0") --index
  $(basename "$0") --list "django"
  $(basename "$0") --build
  $(basename "$0") --run pi django__django-11039
  $(basename "$0") --run-all pi
  $(basename "$0") --eval pi
  $(basename "$0") --status
  $(basename "$0") --interactive codex
EOF
}

# ==============================================================================
# INDEX — fetch and cache dataset
# ==============================================================================
do_index() {
    echo "=== Indexing SWE-bench Verified ==="
    fetch_dataset >/dev/null
    local count
    count=$(CACHE_FILE="${CACHE_FILE}" python3 -c "import json, os; print(len(json.load(open(os.environ['CACHE_FILE']))))")
    echo "Cached ${count} instances at ${CACHE_FILE}"
}

# ==============================================================================
# LIST — list problems
# ==============================================================================
do_list() {
    local filter="${1:-}"
    echo "=== SWE-bench Verified Instances ==="
    fetch_dataset | FILTER="${filter}" python3 -c "
import sys, json, os
filter_val = os.environ.get('FILTER', '')
data = json.load(sys.stdin)
if filter_val:
    data = [i for i in data if filter_val.lower() in str(i).lower()]
for inst in sorted(data, key=lambda x: (x['repo'], x['version'])):
    print(f\"{inst['instance_id']:40s} {inst['repo']:30s} v{inst['version']:10s} {inst['difficulty']:20s}\")
print(f'\nTotal: {len(data)} instances')
"
}

# ==============================================================================
# BUILD — build all Docker images
# ==============================================================================
do_build() {
    local only_agent="${1:-}"
    if [ -n "$only_agent" ] && [ ! -d "${AGENTS_DIR}/${only_agent}" ]; then
        echo "ERROR: Agent '${only_agent}' not found. Available agents:"
        for d in "${AGENTS_DIR}"/*/; do
            [ -d "$d" ] && [ "$(basename "$d")" != "base" ] && echo "  $(basename "$d")"
        done
        exit 1
    fi

    echo "=== Building Docker Images ==="

    # Build base image (always built first)
    if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
        echo "Building ${BASE_IMAGE}..."
        docker build -f "${AGENTS_DIR}/base/Dockerfile.base" -t "$BASE_IMAGE" "${AGENTS_DIR}/base/"
    else
        echo "${BASE_IMAGE} already exists."
    fi

    # Build agent images (skip 'base' — built explicitly above)
    for agent_dir in "${AGENTS_DIR}"/*/; do
        [ -d "$agent_dir" ] || continue
        agent_name=$(basename "$agent_dir")
        [ "$agent_name" = "base" ] && continue
        [ -n "$only_agent" ] && [ "$agent_name" != "$only_agent" ] && continue
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
    repo_url=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['repo'])")
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
    while read -r instance_id; do
        do_run "$agent" "$instance_id"
    done < <(fetch_dataset | python3 -c "
import sys, json
data = json.load(sys.stdin)
for inst in data:
    print(inst['instance_id'])
")
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
    EVAL_DIR="${eval_dir}" AGENT_NAME="${agent}" TMP_PREDS="${tmp_preds}" python3 -c "
import sys, json, os
results = []
for instance_id in sys.argv[1:]:
    patch_path = os.path.join(os.environ['EVAL_DIR'], instance_id, 'patch.diff')
    if not os.path.exists(patch_path):
        continue
    with open(patch_path) as f:
        patch = f.read()
    results.append({
        'instance_id': instance_id,
        'model_name_or_path': os.environ['AGENT_NAME'],
        'model_patch': patch
    })
with open(os.environ['TMP_PREDS'], 'w') as f:
    json.dump(results, f)
print(f'Collected {len(results)} patches')
" "${instance_ids[@]}"

    # Run evaluation directly on the host (swebench harness creates its own
    # test containers as needed — no need to wrap in another container).
    local results_dir="${eval_dir}/results"
    python3 -m swebench.harness.run_evaluation \
        --dataset_name princeton-nlp/SWE-bench_Verified \
        --predictions_path "$tmp_preds" \
        --max_workers 1 \
        --namespace '' \
        --output_dir "$results_dir" \
        2>&1 | tee "${eval_dir}/eval.log"

    rm -f "$tmp_preds"

    # Update result.json for each instance with actual eval status
    if [ -d "$results_dir" ]; then
        RESULTS_DIR="${results_dir}" OUTPUT_DIR="${eval_dir}" python3 -c "
import json, os, glob

results_dir = os.environ['RESULTS_DIR']
output_dir  = os.environ['OUTPUT_DIR']

# swebench writes per-instance results as JSONL in results/<dataset>/test_results.jsonl
for test_file in glob.glob(os.path.join(results_dir, '*', 'test_results.jsonl')):
    with open(test_file) as f:
        for line in f:
            entry = json.loads(line)
            iid   = entry.get('instance_id')
            if not iid:
                continue
            result_path = os.path.join(output_dir, iid, 'result.json')
            if not os.path.exists(result_path):
                continue
            # Determine status from the test results
            logs_to_analyze = entry.get('logs_to_analyze', [])
            test_results    = entry.get('test_results', {})
            resolved        = test_results.get('resolved', False)
            if resolved:
                status = 'resolved'
            elif logs_to_analyze:
                status = 'failed'
            else:
                status = 'failed'  # no logs means it didn't pass
            with open(result_path) as f:
                meta = json.load(f)
            meta['status'] = status
            with open(result_path, 'w') as f:
                json.dump(meta, f, indent=2)
            print(f'  Updated {iid}: {status}')
" 2>/dev/null || echo "  (Could not parse eval results — check eval.log)"
    fi
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
    local DOCKER_RUN="docker run --rm -i"
    [ -t 0 ] && DOCKER_RUN="$DOCKER_RUN -t"  # allocate TTY only if stdin is a terminal
    $DOCKER_RUN \
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
        "$image_name" --interactive
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
            status=$(INSTANCE_DIR="${instance_dir}" python3 -c "import json, os; print(json.load(open(os.environ['INSTANCE_DIR'] + 'result.json'))['status'])" 2>/dev/null || echo "unknown")
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
        do_build "${2:-}"
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
