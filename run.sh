#!/bin/bash
# ==============================================================================
# SWE-bench Orchestrator — unified build, index, list, run, and eval
#
# Architecture: Self-contained agent bundles mounted into swebench eval images.
# Each instance has a pre-built swebench image; we mount our bundle inside it.
#
# Usage:
#   ./run.sh --help              Show this help
#   ./run.sh --index             Fetch and cache problem listings from HuggingFace
#   ./run.sh --list [filter]     List problems (optional grep filter)
#   ./run.sh --build [agent]     Build agent bundle(s) only
#   ./run.sh --rebuild [scope] Rebuild from scratch (--no-cache): all|<agent>
#   ./run.sh --run <agent> <id>  Run an agent against a specific instance
#   ./run.sh --run-all <agent>   Run agent against all 500 instances
#   ./run.sh --eval <agent>      Evaluate collected patches (official swebench harness)
#   ./run.sh --summarize [agent]  Combine and summarize collected results
#   ./run.sh --status            Show completion status
#   ./run.sh --interactive <id>  Drop into interactive shell in swebench image
#   ./run.sh --init              Install swebench harness (creates .venv/swebench)
#
# Workflow:
#   1. ./run.sh --index          (one-time: cache the dataset)
#   2. ./run.sh --build          (build agent bundles — no Docker images)
#   3. ./run.sh --run <agent> <id>  (spins up swebench image, mounts our bundle)
#   4. ./run.sh --init           (install swebench harness — one-time)
#   5. ./run.sh --eval <agent>   (evaluate collected patches via official harness)
#   6. ./run.sh --status         (inspect results)
# ==============================================================================

set -euo pipefail

# ==============================================================================
# CLEANUP — docker --rm handles container removal on exit
# ==============================================================================

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

# SWE-bench image registry
SWEBENCH_REGISTRY="swebench"

# Storage management (percentage threshold to trigger cleanup)
MAX_STORAGE_PCT=${MAX_STORAGE_PCT:-80}

# SWE-bench image cache directory (for Docker images on NAS/external storage)
# Set this to a path on your NAS to avoid filling local disk with swebench images
# Images are saved as tarballs and loaded on demand
# Example: export SWEBENCH_IMAGE_CACHE=/mnt/starcluster/documents/swe-bench-images
SWEBENCH_IMAGE_CACHE=${SWEBENCH_IMAGE_CACHE:-}

# ==============================================================================
# STORAGE — check and cleanup disk usage
# ==============================================================================
check_storage() {
    local usage_pct
    usage_pct=$(df --output=pcent "${REPO_ROOT}" 2>/dev/null | tail -1 | tr -d ' %')
    if [ "${usage_pct:-0}" -ge "${MAX_STORAGE_PCT}" ]; then
        echo "WARNING: Disk at ${usage_pct}% (threshold: ${MAX_STORAGE_PCT}%)"
        return 1
    fi
    return 0
}

do_cleanup() {
    echo "=== Cleaning up Docker resources ==="
    docker stop $(docker ps -q) 2>/dev/null || true
    docker rm -f $(docker ps -aq) 2>/dev/null || true
    docker rmi $(docker images -q) 2>/dev/null || true
    echo "=== Cleanup complete ==="
}

# Save swebench image to cache (NAS/external storage)
save_image_to_cache() {
    local image_name="$1"
    [ -z "$SWEBENCH_IMAGE_CACHE" ] && return 0
    local cache_dir="${SWEBENCH_IMAGE_CACHE}/images"
    mkdir -p "$cache_dir"
    local safe_name=$(echo "$image_name" | tr '/:' '__')
    local tar_file="${cache_dir}/${safe_name}.tar"
    if [ ! -f "$tar_file" ]; then
        echo "  Saving image to cache: ${safe_name}.tar"
        docker save "$image_name" -o "$tar_file" 2>/dev/null || true
    fi
}

# Load swebench image from cache (NAS/external storage)
load_image_from_cache() {
    local image_name="$1"
    [ -z "$SWEBENCH_IMAGE_CACHE" ] && return 1
    local safe_name=$(echo "$image_name" | tr '/:' '__')
    local tar_file="${SWEBENCH_IMAGE_CACHE}/images/${safe_name}.tar"
    if [ -f "$tar_file" ]; then
        echo "  Loading image from cache: ${safe_name}.tar"
        docker load -i "$tar_file" 2>/dev/null && return 0
    fi
    return 1
}

# SWE-bench venv (for harness)
SWEBENCH_VENV="${REPO_ROOT}/.venv/swebench"
SWEBENCH_PY="${SWEBENCH_VENV}/bin/python"

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
        if ! docker run --rm -e HF_DATASET="${HF_DATASET}" python:3.10-slim bash -c '
pip install -q datasets >/dev/null 2>&1
python3 << "PYEOF"
from datasets import load_dataset
import json, os
ds = load_dataset(os.environ["HF_DATASET"], split="test")
data = [dict(i) for i in ds]
print(json.dumps(data))
PYEOF
' > "$CACHE_FILE" 2>/dev/null; then
            echo "ERROR: Failed to fetch dataset from HuggingFace."
            rm -f "$CACHE_FILE"
            exit 1
        fi
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
Self-contained agent bundles mounted into swebench eval images.

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
      Build agent bundle(s) only. No Docker images are built.
      Each agent's self-contained bundle is created under agents/<agent>/bundle/
      containing Node.js, pi CLI, config files, and entrypoint.sh.
      If AGENT is given (e.g. pi), only that agent's bundle is built;
      otherwise all agent bundles are built. Existing bundles are skipped.

  --rebuild [SCOPE]
      Always rebuild from scratch (--no-cache) so the latest deps are pulled.
      SCOPE controls what is rebuilt:
        (none)/all  all agent bundles
        <agent>     only that agent bundle
      Use this to upgrade pi or refresh cached layers. Skips nothing.

  --run <AGENT> <INSTANCE_ID>
      Run an agent against a single instance. AGENT is a folder name under
      agents/ (e.g. pi, codex). INSTANCE_ID is like django__django-11039.
      Spins up the swebench eval image for that instance, mounts our agent
      bundle read-only at /agent, and calls entrypoint.sh inside it.
      Results are written to <workspace>/outputs/<INSTANCE_ID>/.

  --run-all <AGENT> [--timeout N] [--resume]
      Run an agent against every cached instance (all 500 verified instances),
      collecting one patch per instance to <workspace>/outputs/<INSTANCE_ID>/.
      --timeout N  Kill containers exceeding N seconds (default: 3600 = 1 hour)
      --resume     Skip instances that already have a result.json
      Long-running: consider running in the background.

  --eval <AGENT>
      Evaluate the patches collected for AGENT in a previous run using the
      official swebench harness. Requires Docker (pulls eval images per
      instance) and network access. Run './run.sh --init' first to install
      the swebench Python package.

  --summarize [AGENT]
      Combine and summarize all collected results into outputs/summary.json and
      print a table (status / local_eval / patch size). Optional AGENT filter
      matches instance ids prefixed with '<agent>__'.

  --status
      Summarize progress of collected runs: totals plus per-instance status
      (resolved / failed / no patch / unknown) read from result.json files.

  --interactive <INSTANCE_ID>
      Drop into an interactive shell inside the swebench eval image for that
      instance. Our agent bundle is mounted read-only at /agent. Useful for
      debugging entrypoint.sh or testing pi manually inside the harness image.

  --init
      Install the official swebench Python package in a local venv
      (.venv/swebench/). Required before --eval to use the official harness.

  --cleanup
      Stop and remove all Docker containers and images. Run this when disk
      space is running low (default threshold: 80%).

ENVIRONMENT
  SWEBENCH_IMAGE_CACHE
      Path to cache swebench Docker images as tarballs (e.g., on NAS).
      Images are saved after first pull and loaded on demand.
      Example:
        export SWEBENCH_IMAGE_CACHE=/mnt/starcluster/documents/swe-bench-images
      This keeps images off local disk while allowing normal Docker operation.
  MAX_STORAGE_PCT
      Disk usage percentage threshold to trigger cleanup warning (default: 80)
  SWE_WORKSPACE_DIR
      Root of the workspace (default: ./workspace). The outputs directory is
      derived from it: outputs = \$workspace/outputs

PREREQUISITES
  - Docker must be installed and running (used for --run, --run-all, and the
    dataset fetch behind --index/--list).
  - A HuggingFace dataset fetch happens on first --index / --list.

EXAMPLES
  $(basename "$0") --index
  $(basename "$0") --list "django"
  $(basename "$0") --build
  $(basename "$0") --rebuild           # force fresh build of all bundles (latest pi CLI)
  $(basename "$0") --rebuild pi        # rebuild only the 'pi' agent bundle
  $(basename "$0") --run pi django__django-11039
  $(basename "$0") --run-all pi
  $(basename "$0") --eval pi
  $(basename "$0") --status
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
# BUILD — build agent bundle(s) only (no Docker images)
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

    echo "=== Building Agent Bundles (no Docker images) ==="

    for agent_dir in "${AGENTS_DIR}"/*/; do
        [ -d "$agent_dir" ] || continue
        agent_name=$(basename "$agent_dir")
        [ "$agent_name" = "base" ] && continue
        [ -n "$only_agent" ] && [ "$agent_name" != "$only_agent" ] && continue

        bundle_script="${agent_dir}/build_bundle.sh"
        if [ ! -f "$bundle_script" ]; then
            echo "WARNING: No build_bundle.sh for agent '${agent_name}', skipping."
            continue
        fi

        echo "Building ${agent_name} agent bundle..."
        bash "$bundle_script" "${agent_dir}/bundle"
    done

    echo ""
    echo "=== Built Bundles ==="
    for agent_dir in "${AGENTS_DIR}"/*/; do
        [ -d "$agent_dir" ] || continue
        agent_name=$(basename "$agent_dir")
        [ "$agent_name" = "base" ] && continue
        if [ -d "${agent_dir}/bundle" ]; then
            echo "  ${agent_name} bundle: $(du -sh "${agent_dir}/bundle" 2>/dev/null | cut -f1)"
        fi
    done
}

# ==============================================================================
# REBUILD — always from scratch (--no-cache)
# ==============================================================================
build_agent_bundle() {
    local agent_dir="$1"
    local agent_name bundle_script
    agent_name=$(basename "$agent_dir")
    [ "$agent_name" = "base" ] && return 0
    bundle_script="${agent_dir}/build_bundle.sh"

    if [ ! -f "$bundle_script" ]; then
        echo "WARNING: No build_bundle.sh for agent '${agent_name}', skipping."
        return 0
    fi

    echo "Building ${agent_name} agent bundle..."
    bash "$bundle_script" "${agent_dir}/bundle"
}

do_rebuild() {
    local scope="${1:-all}"
    if [ "$scope" != "all" ] && [ ! -d "${AGENTS_DIR}/${scope}" ]; then
        echo "ERROR: Unknown rebuild target '${scope}'. Use 'all' or an agent name."
        echo "Available agents:"
        for d in "${AGENTS_DIR}"/*/; do
            [ -d "$d" ] && [ "$(basename "$d")" != "base" ] && echo "  $(basename "$d")"
        done
        exit 1
    fi

    echo "=== Rebuilding (--no-cache) ==="

    if [ "$scope" = "all" ]; then
        for agent_dir in "${AGENTS_DIR}"/*/; do
            [ -d "$agent_dir" ] || continue
            build_agent_bundle "$agent_dir"
        done
    else
        build_agent_bundle "${AGENTS_DIR}/${scope}"
    fi

    echo ""
    echo "=== Built Bundles ==="
    for agent_dir in "${AGENTS_DIR}"/*/; do
        [ -d "$agent_dir" ] || continue
        agent_name=$(basename "$agent_dir")
        [ "$agent_name" = "base" ] && continue
        if [ -d "${agent_dir}/bundle" ]; then
            echo "  ${agent_name} bundle: $(du -sh "${agent_dir}/bundle" 2>/dev/null | cut -f1)"
        fi
    done
}

# ==============================================================================
# SWE-BENCH IMAGE HELPERS
# ==============================================================================
get_arch() {
    uname -m | sed 's/x86_64/x86_64/; s/aarch64/arm64/'
}

# Convert instance_id to swebench image name
# django__django-11039 -> swebench/sweb.eval.x86_64.django_1776_django-11039:latest
instance_to_image() {
    local instance_id="$1"
    local arch
    arch=$(get_arch)

    # Extract repo and issue from instance_id
    # Format: repo__repo-issue  (e.g. django__django-11039)
    local repo_part="${instance_id%%__*}"
    local issue_part="${instance_id#*__}"

    # Convert repo slashes to underscores for image name
    local repo_image_name
    repo_image_name=$(echo "$repo_part" | sed 's|/|_|g')

    echo "${SWEBENCH_REGISTRY}/sweb.eval.${arch}.${repo_image_name}_1776_${issue_part}:latest"
}

# ==============================================================================
# RUN — run an agent against a specific instance
#
# Spins up the swebench eval image for this instance, mounts our agent bundle
# read-only at /agent, and calls entrypoint.sh inside it.
# ==============================================================================
DOCKER_RUN_FLAGS=(
    --memory 8g
    --memory-swap 16g
    --pids-limit 500
    --tmpfs /tmp:rw,noexec,nosuid,size=2g
    --cap-drop ALL
    --security-opt no-new-privileges:true
    --add-host host.docker.internal:host-gateway
)

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

    # Validate agent bundle exists
    local bundle_dir="${AGENTS_DIR}/${agent}/bundle"
    if [ ! -d "$bundle_dir" ]; then
        echo "ERROR: Agent bundle not found at ${bundle_dir}. Run './run.sh --build ${agent}' first."
        exit 1
    fi

    # Get instance data
    local inst_data
    inst_data=$(get_instance "$instance_id")
    local repo_url base_commit problem_statement
    repo_url=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['repo'])")
    base_commit=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['base_commit'])")
    problem_statement=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['problem_statement'])")

    # Determine swebench image for this instance
    local image_name
    image_name=$(instance_to_image "$instance_id")

    # Check storage before pulling
    if ! check_storage; then
        echo "Run './run.sh --cleanup' to free space, or set MAX_STORAGE_PCT"
        exit 1
    fi

    # Pull the image if not present (try cache first)
    if ! docker image inspect "$image_name" >/dev/null 2>&1; then
        # Try loading from cache first
        if load_image_from_cache "$image_name"; then
            echo "  Loaded from cache: ${image_name}"
        else
            echo "Pulling swebench image: ${image_name}..."
            docker pull "$image_name" 2>&1 | tail -3
            # Save to cache for next time
            save_image_to_cache "$image_name"
        fi
    fi

    # Pre-create output directory writable by container uid 1001
    mkdir -p "${OUTPUT_DIR}/${instance_id}"
    chmod 777 "${OUTPUT_DIR}/${instance_id}"

    # Run the agent container
    echo "=============================================================================="
    echo "Running: ${agent} against ${instance_id}"
    echo "Image: ${image_name}"
    echo "=============================================================================="

    docker run --rm \
        --name "swe_${agent}_${instance_id}" \
        "${DOCKER_RUN_FLAGS[@]}" \
        -v "${WORKSPACE_DIR}:/workspace:rw" \
        -v "${bundle_dir}:/agent:ro" \
        "$image_name" \
        /agent/entrypoint.sh \
        "${instance_id}" \
        "https://github.com/${repo_url}" \
        "${base_commit}" \
        "${problem_statement}" || true
}

# ==============================================================================
# RUN-ALL — run agent against all instances
#
# Usage: ./run.sh --run-all <agent> [--timeout <seconds>] [--resume]
#   --timeout N  Kill containers that exceed N seconds (default: 3600 = 1 hour)
#   --resume     Skip instances that already have a result.json
# ==============================================================================
do_run_all() {
    local agent="${1:?Usage: $0 --run-all <agent> [--timeout N] [--resume]}"
    shift

    local timeout_sec="3600" resume=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --timeout) timeout_sec="${2:?--timeout requires a number}"; shift 2 ;;
            --resume)  resume=1; shift ;;
            *)         echo "Unknown option: $1"; exit 1 ;;
        esac
    done

    # Validate agent exists
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found."
        exit 1
    fi

    local count=0 skipped=0
    set +e  # Don't exit on errors — we handle them per-instance
    # Get all instance IDs
    while read -r instance_id; do
        # Resume: skip instances that already have a result.json
        if [ "$resume" = 1 ] && [ -f "${OUTPUT_DIR}/${instance_id}/result.json" ]; then
            skipped=$((skipped + 1))
            continue
        fi
        count=$((count + 1))
        # Determine swebench image for this instance
        local image_name
        image_name=$(instance_to_image "$instance_id")

        # Check storage before pulling
        if ! check_storage; then
            echo "Run './run.sh --cleanup' to free space, or set MAX_STORAGE_PCT"
            break
        fi

        # Pull the image if not present (try cache first)
        if ! docker image inspect "$image_name" >/dev/null 2>&1; then
            # Try loading from cache first
            if load_image_from_cache "$image_name"; then
                echo "  Loaded from cache: ${image_name}"
            else
                echo "Pulling swebench image: ${image_name}..."
                docker pull "$image_name" 2>&1 | tail -3
                # Save to cache for next time
                save_image_to_cache "$image_name"
            fi
        fi

        # Run instance (do_run handles output dir creation)
        do_run "$agent" "$instance_id"
    done < <(fetch_dataset | python3 -c "
import sys, json
data = json.load(sys.stdin)
for inst in data:
    print(inst['instance_id'])
")
    set -e  # Restore error handling
    echo ""
    echo "Done: ${count} run, ${skipped} skipped (resume)"
}

# ==============================================================================
# EVAL — use official swebench harness
# ==============================================================================
do_eval() {
    local agent="${1:?Usage: $0 --eval <agent>}"
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found. Available agents:"
        for d in "${AGENTS_DIR}"/*/; do
            [ -d "$d" ] && echo "  $(basename "$d")"
        done
        exit 1
    fi

    # Check swebench is installed
    if [ ! -f "${SWEBENCH_PY}" ]; then
        echo "ERROR: swebench not installed. Run './run.sh --init' first."
        exit 1
    fi

    local eval_dir="${OUTPUT_DIR}"
    [ -d "$eval_dir" ] || { echo "No outputs found. Run instances first (--run or --run-all)."; return; }

    # Collect instance IDs that have patches
    local instance_ids=()
    for d in "${eval_dir}"/*/; do
        [ -d "$d" ] && [ -f "${d}patch.diff" ] && instance_ids+=("$(basename "$d")")
    done
    [ ${#instance_ids[@]} -eq 0 ] && { echo "No patches found to evaluate. Run instances first."; return; }

    # Build predictions file in swebench format
    local preds="${eval_dir}/predictions.json"
    EVAL_DIR="${eval_dir}" AGENT_NAME="${agent}" PREDS="${preds}" "${SWEBENCH_PY}" -c "
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
with open(os.environ['PREDS'], 'w') as f:
    json.dump(results, f)
print(f'Wrote {len(results)} predictions to {os.environ[\"PREDS\"]}')
" "${instance_ids[@]}"

    echo "=============================================================================="
    echo "Running swebench harness on ${#instance_ids[@]} patch(es) for '${agent}'"
    echo "=============================================================================="

    # Run the official swebench harness
    "${SWEBENCH_PY}" -m swebench.harness.run_evaluation \
        --dataset_name "${HF_DATASET}" \
        --split "test" \
        --predictions_path "${preds}" \
        --max_workers 1 \
        --cache_level instance \
        --report_dir "${eval_dir}" \
        --run_id "${agent}" \
        -i "${instance_ids[@]}"

    # Fold harness results back into each result.json
    echo "Folding harness results into result.json..."
    EVAL_DIR="${eval_dir}" "${SWEBENCH_PY}" -c "
import json, os, glob
report_dir = os.environ['EVAL_DIR']
# swebench writes aggregate report to CWD as <model>.<run_id>.json or <model>__<run_id>.json
cands = glob.glob(os.path.join(report_dir, '*.json')) + glob.glob('*__*.json') + glob.glob('*.pi.json')
rep = None
for c in cands:
    try:
        d = json.load(open(c))
        if 'resolved_ids' in d and 'unresolved_ids' in d:
            rep = d; break
    except Exception:
        pass
if not rep:
    print('WARNING: swebench report not found, skipping fold')
    exit(0)
resolved = set(rep.get('resolved_ids', []))
errored  = set(rep.get('error_ids', []))
folded = 0
for iid in set(resolved) | set(rep.get('unresolved_ids', [])) | errored:
    rf = os.path.join(report_dir, iid, 'result.json')
    if not os.path.exists(rf): continue
    try:
        # Read existing meta (preserve patch_bytes, elapsed_seconds)
        old_meta = {}
        if os.path.exists(rf):
            try:
                old_meta = json.load(open(rf))
            except Exception:
                pass
            os.unlink(rf)
        meta = {
            'status': old_meta.get('status', 'patch_collected'),
            'patch_bytes': old_meta.get('patch_bytes', 0),
            'elapsed_seconds': old_meta.get('elapsed_seconds', 0)
        }
        if iid in resolved:   meta['local_eval'] = 'resolved'; meta['status'] = 'resolved'
        elif iid in errored:  meta['local_eval'] = 'error';    meta['status'] = 'error'
        else:                 meta['local_eval'] = 'failed';    meta['status'] = 'failed'
        json.dump(meta, open(rf, 'w'), indent=2)
        folded += 1
    except PermissionError:
        print(f'WARNING: Cannot write {rf}, skipping')
print(f'Folded results for {folded} instances ({len(resolved) + len(errored)} total)')
"
}

# ==============================================================================
# SUMMARIZE — combine and summarize all collected results
# ==============================================================================
do_summarize() {
    local agent="${1:-}"
    local eval_dir="${OUTPUT_DIR}"
    [ -d "$eval_dir" ] || { echo "No outputs found."; return; }
    local out_json="${eval_dir}/summary.json"
    AGENT="${agent}" EVAL_DIR="${eval_dir}" OUT_JSON="${out_json}" python3 - <<'PY'
import json, os, glob
agent=(os.environ.get('AGENT') or '').strip().lower()
eval_dir=os.environ['EVAL_DIR']; out_json=os.environ['OUT_JSON']
rows=[]
for d in sorted(glob.glob(os.path.join(eval_dir,'*',''))):
    iid=os.path.basename(d.rstrip('/'))
    if not iid: continue
    rp=os.path.join(d,'result.json')
    if not os.path.exists(rp): continue
    try:
        meta=json.load(open(rp))
    except (json.JSONDecodeError, ValueError):
        continue
    rows.append({
      'instance_id': iid,
      'status': meta.get('status'),
      'patch_bytes': meta.get('patch_bytes'),
      'elapsed_seconds': meta.get('elapsed_seconds'),
      'local_eval': meta.get('local_eval'),
    })
total=len(rows)
resolved=sum(1 for r in rows if r['local_eval']=='resolved')
failed=sum(1 for r in rows if r['local_eval']=='failed')
errored=sum(1 for r in rows if r['local_eval']=='error')
no_patch=sum(1 for r in rows if r['status']=='no_patch')
summary={'agent_filter':agent or None,'total':total,
         'resolved':resolved,'failed':failed,'errored':errored,'no_patch':no_patch,'rows':rows}
json.dump(summary, open(out_json,'w'), indent=2)
print(f"{'instance_id':42s} {'status':12s} {'local_eval':12s} {'patch_B':>8s} {'elapsed_s':>10s}")
for r in rows:
    print(f"{r['instance_id']:42s} {str(r['status']):12s} {str(r['local_eval']):12s} {str(r['patch_bytes'] or 0):>8s} {str(r['elapsed_seconds'] or 0):>10s}")
print(f"\nTotal: {total} | resolved: {resolved} | failed: {failed} | error: {errored} | no_patch: {no_patch}")
print(f"Summary written to {out_json}")
PY
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
# INIT — install swebench harness in a local venv
# ==============================================================================
do_init() {
    echo "=== Installing swebench harness ==="
    if [ -f "${SWEBENCH_PY}" ]; then
        echo "swebench already installed at ${SWEBENCH_VENV}"
        "${SWEBENCH_PY}" -c "import swebench; print(f'  Version: {swebench.__version__}')"
        return 0
    fi

    echo "Creating venv at ${SWEBENCH_VENV}..."
    python3 -m venv "${SWEBENCH_VENV}"
    echo "Installing swebench..."
    "${SWEBENCH_VENV}/bin/pip" install swebench 2>&1 | tail -3
    "${SWEBENCH_PY}" -c "import swebench; print(f'  Version: {swebench.__version__}')"
    echo "=== swebench installed ==="
}

# ==============================================================================
# INTERACTIVE — drop into interactive shell in swebench eval image
# ==============================================================================
do_interactive() {
    local instance_id="${1:?Usage: $0 --interactive <instance_id>}"

    # Get instance data to determine repo for image name
    local inst_data
    inst_data=$(get_instance "$instance_id")

    # Determine swebench image for this instance
    local image_name
    image_name=$(instance_to_image "$instance_id")

    # Pull the image if not present
    if ! docker image inspect "$image_name" >/dev/null 2>&1; then
        echo "Pulling swebench image: ${image_name}..."
        docker pull "$image_name" 2>&1 | tail -3
    fi

    echo "Starting interactive shell in ${image_name}..."
    local DOCKER_RUN="docker run --rm -i"
    [ -t 0 ] && DOCKER_RUN="$DOCKER_RUN -t"
    $DOCKER_RUN \
        --memory 8g \
        --memory-swap 16g \
        --pids-limit 500 \
        --tmpfs /tmp:rw,noexec,nosuid,size=2g \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        --add-host host.docker.internal:host-gateway \
        -u 1001:1001 \
        -v "${WORKSPACE_DIR}:/workspace:rw" \
        -v "${AGENTS_DIR}/pi/bundle:/agent:ro" \
        "$image_name" /agent/entrypoint.sh --interactive
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
    --rebuild)
        do_rebuild "${2:-}"
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
    --summarize)
        do_summarize "${2:-}"
        ;;
    --status)
        do_status
        ;;
    --init)
        do_init
        ;;
    --interactive|-i)
        do_interactive "${2:-}"
        ;;
    --cleanup)
        do_cleanup
        ;;
    *)
        echo "Unknown option: $1"
        show_help
        exit 1
        ;;
esac
