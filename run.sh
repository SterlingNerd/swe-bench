#!/bin/bash
# ==============================================================================
# SWE-bench Orchestrator — unified build, index, work, and eval
#
# Architecture: Self-contained agent bundles mounted into swebench eval images.
# Each instance has a pre-built swebench image; we mount the bundle read-only
# at /agent. Outputs are written inside the container and copied out via docker cp.
#
# Two phases:
#   [WORK]  --run / --run-all    Run agents against instances (collect patches)
#   [EVAL]  --eval               Evaluate collected patches via swebench harness
#
# Usage:
#   ./run.sh --help              Show this help
#   ./run.sh --index             Fetch and cache problem listings from HuggingFace
#   ./run.sh --list [filter]     List problems (optional grep filter)
#   ./run.sh --build [agent]     Build agent bundle(s) only
#   ./run.sh --rebuild [scope] Rebuild from scratch (--no-cache): all|<agent>
#   ./run.sh --run <agent> <id>         [WORK] Run agent against a specific instance
#   ./run.sh --run-all <agent> [--timeout N] [--resume]  [WORK] Run all instances
#   ./run.sh --eval <agent>             [EVAL] Evaluate collected patches
#   ./run.sh --summarize [agent]        Combine and summarize results
#   ./run.sh --status [agent]           Show completion status
#   ./run.sh --interactive <agent> <id> Drop into an agent's eval image
#   ./run.sh --init              Install swebench harness (creates .venv/swebench)
#
# Workflow:
#   1. ./run.sh --index          (one-time: cache the dataset)
#   2. ./run.sh --build          (build agent bundles — no Docker images)
#   3. ./run.sh --run <agent> <id>  [WORK] spins up swebench image, mounts our bundle
#   4. ./run.sh --init           (install swebench harness — one-time)
#   5. ./run.sh --eval <agent>   [EVAL] evaluate collected patches via official harness
#   6. ./run.sh --status         (inspect results)
# ==============================================================================

set -euo pipefail

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

# ==============================================================================
# LOG FILE — tee all output here for reference
# ==============================================================================
LOG_FILE="${WORKSPACE_DIR}/run.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

# ==============================================================================
# SIGNAL HANDLING — clean ^C / ERR / EXIT
# ==============================================================================
STOPPED=0
stop_running_containers() {
    [ "$STOPPED" -eq 1 ] && return
    STOPPED=1
    docker ps --format '{{.Names}}' 2>/dev/null | grep '^swe_' | while read -r cname; do
        echo "  Stopping container: ${cname}"
        docker stop "$cname" >/dev/null 2>&1 || true
    done
}
on_interrupt() {
    echo ""
    echo "=============================================================================="
    echo "  ^C received — shutting down..."
    echo "=============================================================================="
    stop_running_containers
    echo "  Cleanup complete. Goodbye."
    echo "=============================================================================="
    exit 130
}
trap on_interrupt INT TERM
trap stop_running_containers EXIT

# ==============================================================================
# SINGLE INSTANCE LOCK — ensure only one instance of this script runs at a time
# ==============================================================================
LOCK_FILE="/tmp/swe-bench-run.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "ERROR: Another instance is already running (lock: ${LOCK_FILE})"
    exit 1
fi

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

require_docker() {
    if ! docker info >/dev/null 2>&1; then
        echo "ERROR: Docker is unavailable from this WSL shell." >&2
        echo "Restart Docker Desktop and WSL, then verify: docker run --rm hello-world" >&2
        return 1
    fi
}

DOCKER_READY=0
ensure_docker() {
    [ "$DOCKER_READY" -eq 1 ] && return 0
    require_docker || return 1
    DOCKER_READY=1
}

record_host_result() {
    local result_file="$1"
    local status="$2"
    local container_exit_code="$3"
    local elapsed_seconds="$4"

    RESULT_FILE="$result_file" RESULT_STATUS="$status" \
        CONTAINER_EXIT_CODE="$container_exit_code" ELAPSED_SECONDS="$elapsed_seconds" \
        python3 - <<'PY'
import json
import os

path = os.environ["RESULT_FILE"]
result = {}
try:
    with open(path) as handle:
        result = json.load(handle)
except (FileNotFoundError, json.JSONDecodeError, ValueError):
    pass

result.update({
    "status": os.environ["RESULT_STATUS"],
    "container_exit_code": int(os.environ["CONTAINER_EXIT_CODE"]),
    "elapsed_seconds": int(os.environ["ELAPSED_SECONDS"]),
})
result.setdefault("patch_bytes", 0)
with open(path, "w") as handle:
    json.dump(result, handle, indent=2)
PY
}

# Remove a named container AND release its bridge-network endpoint.
# `docker rm` cannot free a bridge endpoint whose container no longer exists
# (common after an interrupted/^C run), which causes "endpoint already exists
# in network bridge" (exit 125) on the next `docker run`. `network disconnect
# -f` clears those orphaned bindings even when the container is already gone.
release_container() {
    local name="$1"
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker network disconnect -f bridge "$name" >/dev/null 2>&1 || true
}

do_cleanup() {
    ensure_docker || return 1
    echo "=== Cleaning up SWE-bench Docker resources ==="

    local containers=()
    local images=()
    mapfile -t containers < <(docker ps -aq --filter 'name=^/swe_')
    if [ ${#containers[@]} -gt 0 ]; then
        docker rm -f "${containers[@]}" >/dev/null
        echo "Removed ${#containers[@]} SWE-bench container(s)."
    fi

    # Also release orphaned bridge-network endpoints left behind by containers
    # that were removed without their network binding being cleaned up. These
    # have no container object anymore, so `docker rm` cannot free them and
    # they would block future `docker run` calls with exit 125.
    local orphan_endpoints=()
    mapfile -t orphan_endpoints < <(docker network inspect bridge \
        -f '{{range .Containers}}{{.Name}}{{println}}{{end}}' 2>/dev/null | grep '^swe_')
    if [ ${#orphan_endpoints[@]} -gt 0 ]; then
        for ep in "${orphan_endpoints[@]}"; do
            docker network disconnect -f bridge "$ep" >/dev/null 2>&1 || true
        done
        echo "Released ${#orphan_endpoints[@]} orphaned network endpoint(s)."
    fi

    mapfile -t images < <(
        docker images --format '{{.Repository}} {{.ID}}' |
            awk '$1 ~ /^swebench\/sweb\./ {print $2}' |
            sort -u
    )
    if [ ${#images[@]} -gt 0 ]; then
        docker rmi "${images[@]}"
        echo "Removed ${#images[@]} SWE-bench image(s)."
    fi

    if [ ${#containers[@]} -eq 0 ] && [ ${#images[@]} -eq 0 ]; then
        echo "No SWE-bench Docker resources found."
    fi
    echo "=== Cleanup complete ==="
}

do_cleanup_partial() {
    echo "=== Cleaning up partial/empty output directories ==="
    local removed=0 kept=0
    [ -d "$OUTPUT_DIR" ] || { echo "No outputs directory found."; return 0; }
    for d in "${OUTPUT_DIR}"/*/; do
        [ -d "$d" ] || continue
        local iid=$(basename "$d")
        # Skip if it has a complete result
        if [ -f "${d}result.json" ] && [ -f "${d}patch.diff" ]; then
            kept=$((kept + 1))
            continue
        fi
        echo "  Removing: ${iid}/"
        rm -rf "$d"
        removed=$((removed + 1))
    done
    echo "=== Removed ${removed}, kept ${kept} complete ==="
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
            return 1
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

  --run <AGENT> <INSTANCE_ID> [TIMEOUT]    [WORK]
      Run an agent against a single instance. AGENT is a folder name under
      agents/ (e.g. pi, codex). INSTANCE_ID is like django__django-11039.
      Optional TIMEOUT overrides the default 3600s per-instance timeout.
      Spins up the swebench eval image for that instance, mounts our agent
      bundle read-only at /agent, and calls entrypoint.sh inside it.
      Results are written to <workspace>/outputs/<AGENT>/<INSTANCE_ID>/. 

  --run-all <AGENT> [--timeout N] [--resume]    [WORK]
      Run an agent against every cached instance (all 500 verified instances),
      collecting one patch per instance under <workspace>/outputs/<AGENT>/. 
      --timeout N  Kill containers exceeding N seconds (default: 3600 = 1 hour)
      --resume     Skip instances that already have a result.json
      Long-running: consider running in the background.

  --eval <AGENT>    [EVAL]
      Evaluate the patches collected for AGENT in a previous run using the
      official swebench harness. Requires Docker (pulls eval images per
      instance) and network access. Run './run.sh --init' first to install
      the swebench Python package.

  --summarize [AGENT]
      Combine and summarize results into outputs/<agent>/summary.json and print
      a table (status / local_eval / patch size). If AGENT is omitted, summarize
      each agent output directory independently.

  --status [AGENT]
      Summarize progress of collected runs: totals plus per-instance status
      (resolved / failed / no patch / timeout / error). If AGENT is omitted,
      show every agent output directory.

  --interactive <AGENT> <INSTANCE_ID>
      Drop into an interactive shell inside the swebench eval image for that
      instance with the selected bundle mounted read-only at /agent. Useful for
      debugging entrypoint.sh or testing an agent manually inside the image.

  --init
      Install the official swebench Python package in a local venv
      (.venv/swebench/). Required before --eval to use the official harness.

  --cleanup
      Remove only harness-owned swe_* containers and swebench/sweb.* images.
      Unrelated Docker resources are never touched.

  --cleanup-partial
      Remove output directories that are missing result.json or patch.diff
      (i.e., failed or interrupted runs). Keeps complete runs intact so you
      can inspect their debug files (agent_output.txt, pi-sessions/) first.

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
  $(basename "$0") --run pi django__django-11039 1800   # 30 min timeout
  $(basename "$0") --run-all pi
  $(basename "$0") --eval pi
  $(basename "$0") --status pi
  $(basename "$0") --interactive pi django__django-11039
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
        return 1
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
        return 1
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
# WORK — run an agent against a specific instance
#
# This is the "work" phase: spins up the swebench eval image for this instance,
# mounts our agent bundle read-only at /agent, and calls entrypoint.sh inside it.
# ==============================================================================
DOCKER_RUN_FLAGS=(
    --memory 32g
    --memory-swap 64g
    --pids-limit 500
    --tmpfs /tmp:rw,noexec,nosuid,size=2g
    --cap-drop ALL
    --security-opt no-new-privileges:true
    --add-host host.docker.internal:host-gateway
)

do_run() {
    local agent="${1:?Usage: $0 --run <agent> <instance_id>}"
    local instance_id="${2:?Usage: $0 --run <agent> <instance_id>}"
    local timeout_sec="${3:-3600}"

    if ! [[ "$timeout_sec" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Timeout must be a non-negative integer, got '${timeout_sec}'."
        return 2
    fi

    # Validate agent exists
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found. Available agents:"
        for d in "${AGENTS_DIR}"/*/; do
            [ -d "$d" ] && echo "  $(basename "$d")"
        done
        return 1
    fi

    # Validate agent bundle exists
    local bundle_dir="${AGENTS_DIR}/${agent}/bundle"
    if [ ! -d "$bundle_dir" ]; then
        echo "ERROR: Agent bundle not found at ${bundle_dir}. Run './run.sh --build ${agent}' first."
        return 1
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
        return 1
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

    # Keep each agent's artifacts isolated so comparisons cannot overwrite or
    # mislabel another agent's patch.
    local agent_output_root="${OUTPUT_DIR}/${agent}"
    local instance_output_dir="${agent_output_root}/${instance_id}"
    mkdir -p "$instance_output_dir"
    chmod 777 "$agent_output_root" "$instance_output_dir"

    # Run the agent container
    echo "=============================================================================="
    echo "  [WORK] Running: ${agent} against ${instance_id}"
    echo "         Image: ${image_name}"
    echo "=============================================================================="

    local container_name="swe_${agent}_${instance_id}"
    # Remove any stale container/network endpoint from a previous interrupted
    # run. release_container also drops orphaned bridge endpoints (whose
    # container is already gone) that would otherwise cause exit 125.
    release_container "${container_name}"
    local started_at docker_status elapsed
    local docker_command=(
        docker run
        --name "$container_name"
        "${DOCKER_RUN_FLAGS[@]}"
        -e "SWE_AGENT_NAME=${agent}"
        -e "SWE_OUTPUT_ROOT=/workspace/outputs/${agent}"
        -v "${bundle_dir}:/agent:ro"
        -v "${agent_output_root}:/workspace/outputs"
        "$image_name"
        /agent/entrypoint.sh
        "$instance_id"
        "https://github.com/${repo_url}"
        "$base_commit"
        "$problem_statement"
    )

    started_at=$(date +%s)
    if [ "$timeout_sec" -gt 0 ]; then
        if timeout --foreground --signal=TERM --kill-after=30s "${timeout_sec}s" \
            "${docker_command[@]}"; then
            docker_status=0
        else
            docker_status=$?
        fi
    elif "${docker_command[@]}"; then
        docker_status=0
    else
        docker_status=$?
    fi
    elapsed=$(( $(date +%s) - started_at ))

    if [ "$docker_status" -eq 124 ]; then
        docker rm -f "$container_name" >/dev/null 2>&1 || true
        record_host_result "${instance_output_dir}/result.json" "timed_out" \
            "$docker_status" "$elapsed"
        echo "ERROR: ${agent}/${instance_id} timed out after ${timeout_sec}s."
        return $docker_status
    fi

    if [ "$docker_status" -ne 0 ]; then
        docker rm -f "$container_name" >/dev/null 2>&1 || true
        record_host_result "${instance_output_dir}/result.json" "container_error" \
            "$docker_status" "$elapsed"
        echo "ERROR: ${agent}/${instance_id} container exited with ${docker_status}."
        return $docker_status
    fi

    # Copy outputs out before removing the container.
    # Check docker inspect to distinguish violent deaths from clean exits.
    local container_state=""
    if ! container_state=$(docker inspect --format '{{.State.Status}}' "$container_name" 2>/dev/null); then
        echo "  WARNING: Cannot inspect container state, attempting copy anyway."
    fi

    mkdir -p "$instance_output_dir"
    local cp_ok=0
    local cp_tmp
    cp_tmp=$(mktemp -d)
    if docker cp "${container_name}:/workspace/outputs/${agent}/${instance_id}" \
                 "${cp_tmp}/"; then
        # Flatten: docker cp nests the instance dir; move its contents into place.
        if [ -d "${cp_tmp}/${instance_id}" ]; then
            mv "${cp_tmp}/${instance_id}/"* "${instance_output_dir}/" 2>/dev/null || true
        fi
        # Verify that the copy actually produced output files.
        if [ -f "${instance_output_dir}/result.json" ] || [ -f "${instance_output_dir}/patch.diff" ]; then
            echo "  Copied outputs from container."
            cp_ok=1
        else
            echo "  WARNING: Copy succeeded but no output files found in container."
        fi
    else
        echo "  WARNING: Failed to copy outputs from container (state=$container_state)."
        case "$container_state" in
            dead|error)
                echo "  Container died violently — leaving it for inspection."
                ;;
            *)
                echo "  Leaving container for manual inspection: ${container_name}"
                ;;
        esac
    fi
    rm -rf "${cp_tmp}"

    # Only remove the container once we have the outputs.
    if [ "$cp_ok" -eq 1 ]; then
        docker rm -f "$container_name" >/dev/null 2>&1 || true
    fi

    # Fix ownership — files written by root in the container need to be owned by us.
    if [ "$cp_ok" -eq 1 ]; then
        chown -R "$(id -u):$(id -g)" "${instance_output_dir}" 2>/dev/null || true
    fi

    # If we couldn't copy outputs, treat as failure.
    if [ "$cp_ok" -eq 0 ]; then
        return 1
    fi

    # Check result.json for failure statuses — do_run_all relies on this return code.
    local final_status
    final_status=$(RESULT_FILE="${instance_output_dir}/result.json" python3 -c \
        "import json, os; print(json.load(open(os.environ['RESULT_FILE'])).get('status', 'unknown'))" \
        2>/dev/null || echo "unknown")
    case "$final_status" in
        agent_error|container_error|timed_out|invalid_result)
            return 1
            ;;
    esac
    return 0
}

# ==============================================================================
# WORK-ALL — run agent against all instances
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
            *)         echo "Unknown option: $1"; return 1 ;;
        esac
    done

    # Validate agent exists
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found."
        return 1
    fi

    local count=0 skipped=0 failed=0
    local agent_output_root="${OUTPUT_DIR}/${agent}"
    set +e  # Don't exit on errors — we handle them per-instance
    # Get all instance IDs to temp file
    local inst_file
    inst_file=$(mktemp)
    if ! fetch_dataset | python3 -c "
import sys, json
data = json.load(sys.stdin)
for inst in data:
    print(inst['instance_id'])
" > "$inst_file"; then
        echo "ERROR: Failed to enumerate SWE-bench instances."
        rm -f "$inst_file"
        return 1
    fi
    # Get all instance IDs
    while read -r instance_id; do
        echo "=============================================================================="
        echo "  [WORK] Processing: ${instance_id}"
        echo "=============================================================================="
        # Wait for any running swe containers to finish
        local wait_count=0
        while docker ps --format "{{.Names}}" | grep -q "swe_${agent}_"; do
            if [ $wait_count -gt 0 ] && [ $((wait_count % 6)) -eq 0 ]; then
                echo "  Waiting for container to finish... (${wait_count}s)"
            fi
            sleep 1
            wait_count=$((wait_count + 1))
            if [ $wait_count -gt 3600 ]; then
                echo "  ERROR: Timeout waiting for container, killing all swe containers"
                docker ps --format "{{.Names}}" | grep "swe_${agent}_" | while read -r c; do release_container "$c"; done 2>/dev/null || true
                break
            fi
        done
        # Double check — release any remaining containers/endpoints from previous runs
        docker ps --format "{{.Names}}" | grep "swe_${agent}_" | while read -r c; do release_container "$c"; done 2>/dev/null || true
        # Resume: skip instances that already have a result.json
        if [ "$resume" = 1 ] && [ -f "${agent_output_root}/${instance_id}/result.json" ]; then
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
        if ! do_run "$agent" "$instance_id" "$timeout_sec"; then
            failed=$((failed + 1))
        fi
    done < "$inst_file"
    rm -f "$inst_file"
    set -e  # Restore error handling
    echo ""
    echo "Done: ${count} run, ${skipped} skipped (resume), ${failed} failed"
    [ "$failed" -eq 0 ]
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
        return 1
    fi

    # Check swebench is installed
    if [ ! -f "${SWEBENCH_PY}" ]; then
        echo "ERROR: swebench not installed. Run './run.sh --init' first."
        return 1
    fi

    local eval_dir="${OUTPUT_DIR}/${agent}"
    [ -d "$eval_dir" ] || { echo "No outputs found. Run instances first (--run or --run-all)."; return; }

    # Collect instance IDs that have non-empty patches
    local instance_ids=()
    for d in "${eval_dir}"/*/; do
        [ -d "$d" ] && [ -s "${d}patch.diff" ] && instance_ids+=("$(basename "$d")")
    done
    [ ${#instance_ids[@]} -eq 0 ] && { echo "No patches found to evaluate. Run instances first."; return; }

    # Build predictions file in swebench format (JSONL)
    local preds="${eval_dir}/predictions.jsonl"
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
    for result in results:
        f.write(json.dumps(result) + '\\n')
print(f'Wrote {len(results)} predictions to {os.environ[\"PREDS\"]}')
" "${instance_ids[@]}"

    echo "=============================================================================="
    echo "  [EVAL] Running swebench harness on ${#instance_ids[@]} patch(es) for '${agent}'"
    echo "=============================================================================="

    # Run the official swebench harness from the selected agent's output tree.
    # This keeps its aggregate reports from being confused with another agent.
    local report_dir="${eval_dir}/eval"
    mkdir -p "$report_dir"
    (
        cd "$eval_dir"
        "${SWEBENCH_PY}" -m swebench.harness.run_evaluation \
            --dataset_name "${HF_DATASET}" \
            --split "test" \
            --predictions_path "${preds}" \
            --max_workers 1 \
            --cache_level instance \
            --report_dir "${report_dir}" \
            --run_id "${agent}" \
            -i "${instance_ids[@]}"
    )

    # Fold harness results back into each result.json
    echo "Folding harness results into result.json..."
    EVAL_DIR="${eval_dir}" AGENT_NAME="${agent}" "${SWEBENCH_PY}" -c "
import json, os, glob
report_dir = os.environ['EVAL_DIR']
agent = os.environ['AGENT_NAME']
# swebench versions have used both separators; prefer the exact agent/run id,
# then fall back to the newest aggregate report inside this agent directory.
cands = [
    os.path.join(report_dir, f'{agent}.{agent}.json'),
    os.path.join(report_dir, f'{agent}__{agent}.json'),
]
cands.extend(sorted(glob.glob(os.path.join(report_dir, '*.json')), key=os.path.getmtime, reverse=True))
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
        try:
            meta = json.load(open(rf))
        except Exception:
            meta = {'status': 'patch_collected'}
        if iid in resolved:   meta['local_eval'] = 'resolved'; meta['status'] = 'resolved'
        elif iid in errored:  meta['local_eval'] = 'error';    meta['status'] = 'error'
        else:                 meta['local_eval'] = 'failed';    meta['status'] = 'failed'
        with open(rf, 'w') as handle:
            json.dump(meta, handle, indent=2)
        folded += 1
    except PermissionError:
        print(f'WARNING: Cannot write {rf}, skipping')
print(f'Folded results for {folded} instances ({len(resolved) + len(errored)} total)')
"
}

# ==============================================================================
# SUMMARIZE — combine and summarize all collected results
# ==============================================================================
summarize_agent() {
    local agent="$1"
    local eval_dir="${OUTPUT_DIR}/${agent}"
    [ -d "$eval_dir" ] || { echo "No outputs found for agent '${agent}'."; return; }
    local out_json="${eval_dir}/summary.json"
    AGENT="${agent}" EVAL_DIR="${eval_dir}" OUT_JSON="${out_json}" python3 - <<'PY'
import json, os, glob
agent=os.environ['AGENT']
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
timed_out=sum(1 for r in rows if r['status']=='timed_out')
agent_errors=sum(1 for r in rows if r['status'] in ('agent_error','container_error'))
summary={'agent':agent,'total':total, 'resolved':resolved,'failed':failed,
         'errored':errored,'no_patch':no_patch,'timed_out':timed_out,
         'agent_errors':agent_errors,'rows':rows}
json.dump(summary, open(out_json,'w'), indent=2)
print(f"Agent: {agent}")
print(f"{'instance_id':42s} {'status':12s} {'local_eval':12s} {'patch_B':>8s} {'elapsed_s':>10s}")
for r in rows:
    print(f"{r['instance_id']:42s} {str(r['status']):12s} {str(r['local_eval']):12s} {str(r['patch_bytes'] or 0):>8s} {str(r['elapsed_seconds'] or 0):>10s}")
print(f"\nTotal: {total} | resolved: {resolved} | failed: {failed} | error: {errored} | no_patch: {no_patch} | timed_out: {timed_out} | agent_error: {agent_errors}")
print(f"Summary written to {out_json}")
PY
}

do_summarize() {
    local requested_agent="${1:-}"
    if [ -n "$requested_agent" ]; then
        summarize_agent "$requested_agent"
        return
    fi

    [ -d "$OUTPUT_DIR" ] || { echo "No outputs found."; return; }
    local found=0 agent_dir
    for agent_dir in "${OUTPUT_DIR}"/*/; do
        [ -d "$agent_dir" ] || continue
        found=1
        summarize_agent "$(basename "$agent_dir")"
        echo ""
    done
    [ "$found" -eq 1 ] || echo "No agent outputs found."
}

# ==============================================================================
# STATUS — show completion status
# ==============================================================================
show_agent_status() {
    local agent="$1"
    local agent_output_dir="${OUTPUT_DIR}/${agent}"
    local total=0 resolved=0 failed=0 no_patch=0 timed_out=0 errors=0 unknown=0

    echo "Agent: ${agent}"
    if [ ! -d "$agent_output_dir" ]; then
        echo "  No outputs found."
        return
    fi

    local instance_dir instance_id status
    for instance_dir in "${agent_output_dir}"/*/; do
        [ -d "$instance_dir" ] || continue
        instance_id=$(basename "$instance_dir")
        case "$instance_id" in
            eval|logs) continue ;;
        esac
        total=$((total + 1))

        if [ -f "${instance_dir}result.json" ]; then
            status=$(INSTANCE_DIR="${instance_dir}" python3 -c "import json, os; print(json.load(open(os.environ['INSTANCE_DIR'] + 'result.json'))['status'])" 2>/dev/null || echo "unknown")
            case "$status" in
                resolved) resolved=$((resolved + 1)); echo -e "\033[32m✓\033[0m $instance_id ($status)" ;;
                failed)   failed=$((failed + 1));   echo -e "\033[31m✗\033[0m $instance_id ($status)" ;;
                no_patch) no_patch=$((no_patch + 1)); echo -e "\033[33m—\033[0m $instance_id (no patch)" ;;
                timed_out) timed_out=$((timed_out + 1)); echo -e "\033[33m⌛\033[0m $instance_id (timed out)" ;;
                error|agent_error|container_error) errors=$((errors + 1)); echo -e "\033[31m!\033[0m $instance_id ($status)" ;;
                *)        unknown=$((unknown + 1));   echo -e "\033[90m?\033[0m $instance_id ($status)" ;;
            esac
        else
            unknown=$((unknown + 1))
            echo -e "\033[90m?\033[0m $instance_id (no result)"
        fi
    done

    echo ""
    echo "Total: $total | Resolved: $resolved | Failed: $failed | No patch: $no_patch | Timed out: $timed_out | Errors: $errors | Unknown: $unknown"
}

do_status() {
    local requested_agent="${1:-}"
    echo "=== SWE-bench Harness Status ==="
    echo "Output directory: ${OUTPUT_DIR}"
    echo ""

    if [ -n "$requested_agent" ]; then
        show_agent_status "$requested_agent"
        return
    fi

    if [ ! -d "$OUTPUT_DIR" ]; then
        echo "No outputs found. Run instances first."
        return
    fi

    local found=0 agent_dir
    for agent_dir in "${OUTPUT_DIR}"/*/; do
        [ -d "$agent_dir" ] || continue
        found=1
        show_agent_status "$(basename "$agent_dir")"
        echo ""
    done
    [ "$found" -eq 1 ] || echo "No agent outputs found."
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
    local agent="${1:?Usage: $0 --interactive <agent> <instance_id>}"
    local instance_id="${2:?Usage: $0 --interactive <agent> <instance_id>}"

    ensure_docker || return 1

    local bundle_dir="${AGENTS_DIR}/${agent}/bundle"
    if [ ! -d "$bundle_dir" ]; then
        echo "ERROR: Agent bundle not found at ${bundle_dir}. Run './run.sh --build ${agent}' first."
        return 1
    fi

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

    echo "Starting interactive shell for ${agent} in ${image_name}..."
    local docker_command=(docker run --rm -i)
    [ -t 0 ] && docker_command+=(-t)
    docker_command+=(
        "${DOCKER_RUN_FLAGS[@]}"
        -e "SWE_AGENT_NAME=${agent}"
        -e "SWE_OUTPUT_ROOT=/workspace/outputs/${agent}"
        -v "${WORKSPACE_DIR}:/workspace:rw"
        -v "${bundle_dir}:/agent:ro"
        "$image_name"
        /agent/entrypoint.sh
        --interactive
    )
    "${docker_command[@]}"
}

# ==============================================================================
# MAIN
# ==============================================================================
main() {
    if [ $# -eq 0 ]; then
        show_help
        return 0
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
            do_run "${2:-}" "${3:-}" "${4:-}"
            ;;
        --run-all)
            do_run_all "${@:2}"
            ;;
        --eval)
            do_eval "${2:-}"
            ;;
        --summarize)
            do_summarize "${2:-}"
            ;;
        --status)
            do_status "${2:-}"
            ;;
        --init)
            do_init
            ;;
        --interactive|-i)
            do_interactive "${2:-}" "${3:-}"
            ;;
        --cleanup)
            do_cleanup
            ;;
        --cleanup-partial)
            do_cleanup_partial
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            return 1
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
