#!/bin/bash
# ==============================================================================
# SWE-bench Orchestrator — unified build, index, work, and eval
#
# Architecture: Self-contained agent bundles mounted into swebench eval images.
# Each instance has a pre-built swebench image; we mount the bundle read-only
# at /agent. Every invocation writes to a new manifest-owned attempt directory.
# Containers are inspected before removal and invalid/incomplete attempts are
# retained for diagnosis.
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
#   ./run.sh --run <agent> <id> [TIMEOUT] [--run-id ID]  Run one instance
#   ./run.sh --run-all <agent> [--timeout N] [--resume] [--run-id ID]
#   ./run.sh --eval <agent> [--run-id ID]  Evaluate selected attempts
#   ./run.sh --summarize [agent] [--run-id ID]
#   ./run.sh --status [agent] [--run-id ID]
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
WORKSPACE_DIR="${SWE_WORKSPACE_DIR:-${REPO_ROOT}/workspace}"
RUNS_DIR="${WORKSPACE_DIR}/runs"
ARTIFACT_TOOL="${REPO_ROOT}/scripts/run_artifacts.py"
CACHE_FILE="/tmp/swe_verified_cache.json"
HF_DATASET="princeton-nlp/SWE-bench_Verified"

# ==============================================================================
# LOG FILE — tee all output here for reference
# ==============================================================================
LOG_FILE="${WORKSPACE_DIR}/run.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

# ==============================================================================
# SIGNAL HANDLING — checkpoint and stop only the active container
# ==============================================================================
ACTIVE_CONTAINER=""
ACTIVE_ATTEMPT_DIR=""
INTERRUPT_REQUESTED=0

write_termination_request() {
    local requested_status="$1"
    local reason="$2"
    local timeout_seconds="${3:-0}"
    [ -n "$ACTIVE_ATTEMPT_DIR" ] || return 0
    ATTEMPT_DIR="$ACTIVE_ATTEMPT_DIR" REQUESTED_STATUS="$requested_status" \
        TERMINATION_REASON="$reason" TIMEOUT_SECONDS="$timeout_seconds" python3 - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

target = Path(os.environ["ATTEMPT_DIR"]) / "termination-request.json"
payload = {
    "schema_version": 1,
    "requested_status": os.environ["REQUESTED_STATUS"],
    "reason": os.environ["TERMINATION_REASON"],
    "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "timeout_seconds": int(os.environ["TIMEOUT_SECONDS"]),
}
fd, name = tempfile.mkstemp(prefix=".termination-request.", dir=target.parent)
try:
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(name, target)
finally:
    Path(name).unlink(missing_ok=True)
PY
}

on_interrupt() {
    INTERRUPT_REQUESTED=1
    echo ""
    echo "=============================================================================="
    echo "  Interrupt received — requesting an artifact checkpoint..."
    echo "=============================================================================="
    if [ -n "$ACTIVE_CONTAINER" ]; then
        write_termination_request "operator_cancelled" "operator_interrupt" 0 || true
        docker stop --signal=TERM --time 30 "$ACTIVE_CONTAINER" >/dev/null 2>&1 || true
    else
        exit 130
    fi
}
trap on_interrupt INT TERM
on_exit() {
    if [ -n "$ACTIVE_CONTAINER" ]; then
        echo "  Stopping active container during runner exit: ${ACTIVE_CONTAINER}" >&2
        write_termination_request "container_error" "runner_exit" 0 || true
        docker stop --signal=TERM --time 30 "$ACTIVE_CONTAINER" >/dev/null 2>&1 || true
    fi
}
trap on_exit EXIT

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
    local status_override="$2"
    local container_exit_code="$3"
    local elapsed_seconds="$4"
    local wait_monitor_exit_code="$5"
    local timeout_seconds="$6"
    local container_state_file="$7"
    local artifacts_complete="$8"
    local retained_container="${9:-}"

    RESULT_FILE="$result_file" RESULT_STATUS_OVERRIDE="$status_override" \
        CONTAINER_EXIT_CODE="$container_exit_code" ELAPSED_SECONDS="$elapsed_seconds" \
        WAIT_MONITOR_EXIT_CODE="$wait_monitor_exit_code" TIMEOUT_SECONDS="$timeout_seconds" \
        CONTAINER_STATE_FILE="$container_state_file" ARTIFACTS_COMPLETE="$artifacts_complete" \
        RETAINED_CONTAINER="$retained_container" \
        python3 - <<'PY'
import json
import os
import tempfile
from pathlib import Path

path = Path(os.environ["RESULT_FILE"])
try:
    with path.open() as handle:
        result = json.load(handle)
except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
    raise SystemExit(f"cannot augment invalid result: {path}: {exc}")

state = {}
state_path = Path(os.environ["CONTAINER_STATE_FILE"])
if state_path.is_file():
    try:
        state = json.load(state_path.open())
    except (json.JSONDecodeError, OSError):
        state = {}

termination_request = {}
request_path = path.with_name("termination-request.json")
if request_path.is_file():
    try:
        termination_request = json.load(request_path.open())
    except (json.JSONDecodeError, OSError):
        termination_request = {}

result.update({
    "schema_version": 1,
    "container_exit_code": int(os.environ["CONTAINER_EXIT_CODE"]),
    "elapsed_seconds": int(os.environ["ELAPSED_SECONDS"]),
    "wait_monitor_exit_code": int(os.environ["WAIT_MONITOR_EXIT_CODE"]),
    "timeout_seconds": int(os.environ["TIMEOUT_SECONDS"]),
    "artifacts_complete": os.environ["ARTIFACTS_COMPLETE"] == "1",
    "container": {
        "oom_killed": bool(state.get("OOMKilled", False)),
        "error": state.get("Error", ""),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
    },
})
override = os.environ["RESULT_STATUS_OVERRIDE"]
if override:
    result["status"] = override
if termination_request:
    host_reasons = {
        "timed_out": "hard_timeout",
        "operator_cancelled": "operator_interrupt",
        "container_error": "wait_monitor_error",
    }
    result["finalization_reason"] = host_reasons.get(
        override,
        termination_request.get("reason", result.get("finalization_reason")),
    )
    result["termination_requested_at"] = termination_request.get(
        "requested_at", result.get("termination_requested_at")
    )
    result["termination_signal"] = result.get("termination_signal") or "TERM"
    if override in {"timed_out", "operator_cancelled"}:
        result["partial_patch"] = True
retained = os.environ["RETAINED_CONTAINER"]
if retained:
    result["retained_container"] = retained
else:
    result.pop("retained_container", None)

patch_path = path.with_name("patch.diff")
result["patch_bytes"] = patch_path.stat().st_size
fd, name = tempfile.mkstemp(prefix=".result.", dir=path.parent)
try:
    with os.fdopen(fd, "w") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(name, path)
finally:
    Path(name).unlink(missing_ok=True)
PY
}

record_host_observation() {
    local observation_file="$1" status="$2" elapsed="$3" monitor_rc="$4" retained="$5"
    OBSERVATION_FILE="$observation_file" OBSERVATION_STATUS="$status" \
        ELAPSED_SECONDS="$elapsed" WAIT_MONITOR_EXIT_CODE="$monitor_rc" \
        RETAINED_CONTAINER="$retained" python3 - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["OBSERVATION_FILE"])
payload = {
    "schema_version": 1,
    "status": os.environ["OBSERVATION_STATUS"],
    "elapsed_seconds": int(os.environ["ELAPSED_SECONDS"]),
    "wait_monitor_exit_code": int(os.environ["WAIT_MONITOR_EXIT_CODE"]),
    "retained_container": os.environ["RETAINED_CONTAINER"],
    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
}
fd, name = tempfile.mkstemp(prefix=".host-observation.", dir=path.parent)
try:
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(name, path)
finally:
    Path(name).unlink(missing_ok=True)
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

resolve_run_dir() {
    local agent="$1" run_id="${2:-}"
    local args=(resolve-run --runs-dir "$RUNS_DIR" --agent "$agent")
    [ -n "$run_id" ] && args+=(--run-id "$run_id")
    python3 "$ARTIFACT_TOOL" "${args[@]}"
}

do_cleanup_partial() {
    local agent="" run_id="" apply=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --agent) agent="${2:?--agent requires a value}"; shift 2 ;;
            --run-id) run_id="${2:?--run-id requires a value}"; shift 2 ;;
            --apply) apply=1; shift ;;
            *) echo "Unknown option: $1"; return 2 ;;
        esac
    done
    if [ -z "$agent" ]; then
        echo "ERROR: --cleanup-partial requires --agent so cleanup cannot cross run ownership." >&2
        return 2
    fi
    local run_dir
    run_dir=$(resolve_run_dir "$agent" "$run_id") || return
    local args=(cleanup-partial --run-dir "$run_dir")
    [ "$apply" -eq 1 ] && args+=(--apply)
    python3 "$ARTIFACT_TOOL" "${args[@]}"
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

  --run <AGENT> <INSTANCE_ID> [TIMEOUT] [--run-id ID] [--profile NAME]    [WORK]
      Run an agent against a single instance. AGENT is a folder name under
      agents/ (e.g. pi, codex). INSTANCE_ID is like django__django-11039.
      Optional TIMEOUT overrides the default 3600s per-instance timeout.
      Spins up the swebench eval image for that instance, mounts our agent
      bundle read-only at /agent, and calls entrypoint.sh inside it.
      Allocates a new immutable attempt under
      <workspace>/runs/<RUN_ID>/tasks/<INSTANCE_ID>/attempts/. If --run-id is
      omitted, a unique run ID is generated.

  --run-all <AGENT> [--timeout N] [--resume] [--run-id ID] [--profile NAME]    [WORK]
      Run an agent against every cached instance (all 500 verified instances),
      recording ordered tasks and isolated attempts in one run manifest.
      --timeout N  Request checkpoint/termination after N seconds (default: 3600)
      --resume     Continue the named/latest run; run only untouched tasks
      --run-id ID  Create or resume an explicit durable run
      Long-running: consider running in the background.

  --eval <AGENT> [--run-id ID]    [EVAL]
      Evaluate only the attempts explicitly selected in the run manifest.
      Patch digests are checked before predictions are created. Evaluation is
      recorded as a report overlay and never rewrites finalized attempts.
      Omitting --run-id resolves the latest recorded run for AGENT.

  --summarize [AGENT] [--run-id ID]
      Derive reports/summary.json from a run manifest, its attempts, and latest
      evaluation overlay. If AGENT is omitted, summarize each latest agent run.

  --status [AGENT] [--run-id ID]
      Show manifest-backed progress without mutating attempt artifacts. If
      AGENT is omitted, show each agent's latest recorded run.

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

  --cleanup-partial --agent AGENT [--run-id ID] [--apply]
      List only manifest-owned, unfinalized attempt directories. This is a
      dry-run unless --apply is supplied. Finalized or selected attempts and
      legacy workspace/outputs trees are never deletion candidates.

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
      Root of the workspace (default: ./workspace). Durable runs are stored at
      \$workspace/runs. Legacy \$workspace/outputs data is read-only and is not
      auto-selected, migrated, evaluated, or cleaned.

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
  $(basename "$0") --run pi django__django-11039 1800 --profile baseline-local
  $(basename "$0") --run-all pi --run-id verified-pi-pilot --timeout 1800
  $(basename "$0") --run-all pi --resume --run-id verified-pi-pilot
  $(basename "$0") --eval pi --run-id verified-pi-pilot
  $(basename "$0") --status pi --run-id verified-pi-pilot
  $(basename "$0") --cleanup-partial --agent pi --run-id verified-pi-pilot
  $(basename "$0") --cleanup-partial --agent pi --run-id verified-pi-pilot --apply
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

append_agent_runtime_env() {
    local agent="$1"
    local args_name="$2"
    local -n args_ref="$args_name"
    [ "$agent" = "codex" ] || return 0

    local name
    for name in \
        SWE_CODEX_MODEL \
        SWE_CODEX_BASE_URL \
        SWE_CODEX_API_KEY \
        SWE_CODEX_CONTEXT_WINDOW \
        SWE_CODEX_AUTO_COMPACT_TOKEN_LIMIT; do
        if [ -n "${!name:-}" ]; then
            if [ "$name" = "SWE_CODEX_API_KEY" ]; then
                # Let Docker copy the value from the host environment without
                # placing the secret itself in this process's argument list.
                args_ref+=(-e "$name")
            else
                args_ref+=(-e "${name}=${!name}")
            fi
        fi
    done
}

new_run_id() {
    local agent="$1"
    printf 'run-%s-%s-%s\n' "$agent" "$(date -u +%Y%m%dT%H%M%S%NZ)" "$$"
}

dataset_fingerprint() {
    local instances_file="$1"
    if [ -s "$CACHE_FILE" ]; then
        sha256sum "$CACHE_FILE" | awk '{print $1}'
    else
        sha256sum "$instances_file" | awk '{print $1}'
    fi
}

create_run_manifest() {
    local agent="$1" timeout_sec="$2" instances_file="$3"
    local requested_run_id="${4:-}" profile="${5:-default}"
    local run_id="${requested_run_id:-$(new_run_id "$agent")}"
    local runner_commit dataset_sha
    runner_commit=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)
    dataset_sha=$(dataset_fingerprint "$instances_file")
    local dirty_args=()
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]; then
        dirty_args+=(--runner-dirty)
    fi
    python3 "$ARTIFACT_TOOL" create-run \
        --runs-dir "$RUNS_DIR" \
        --run-id "$run_id" \
        --agent "$agent" \
        --profile "$profile" \
        --dataset-name "$HF_DATASET" \
        --dataset-split test \
        --dataset-sha256 "$dataset_sha" \
        --runner-commit "$runner_commit" \
        "${dirty_args[@]}" \
        --timeout-seconds "$timeout_sec" \
        --instances-file "$instances_file"
}

write_host_terminal_result() {
    local attempt_dir="$1" status="$2" elapsed="$3" container_exit="$4"
    ATTEMPT_DIR="$attempt_dir" TERMINAL_STATUS="$status" ELAPSED_SECONDS="$elapsed" \
        CONTAINER_EXIT_CODE="$container_exit" python3 - <<'PY'
import json
import os
import tempfile
from pathlib import Path

attempt = Path(os.environ["ATTEMPT_DIR"])
patch = attempt / "patch.diff"
if not patch.exists():
    patch.write_bytes(b"")
result = {
    "schema_version": 1,
    "status": os.environ["TERMINAL_STATUS"],
    "patch_bytes": patch.stat().st_size,
    "elapsed_seconds": int(os.environ["ELAPSED_SECONDS"]),
    "container_exit_code": int(os.environ["CONTAINER_EXIT_CODE"]),
    "checkpointed": False,
    "partial_patch": False,
    "finalization_reason": "host_start_failure",
}
target = attempt / "result.json"
fd, name = tempfile.mkstemp(prefix=".result.", dir=attempt)
try:
    with os.fdopen(fd, "w") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(name, target)
finally:
    Path(name).unlink(missing_ok=True)
PY
}

attempt_artifacts_valid() {
    local attempt_dir="$1"
    ATTEMPT_DIR="$attempt_dir" python3 - <<'PY'
import json
import os
from pathlib import Path

attempt = Path(os.environ["ATTEMPT_DIR"])
result_path = attempt / "result.json"
patch_path = attempt / "patch.diff"
if result_path.is_symlink() or patch_path.is_symlink() or not patch_path.is_file():
    raise SystemExit(1)
try:
    result = json.load(result_path.open())
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(result, dict) or not isinstance(result.get("status"), str):
    raise SystemExit(1)
allowed = {
    "patch_collected", "no_patch", "agent_error", "invalid_result",
    "container_error", "timed_out", "operator_cancelled", "oom_killed",
}
if result["status"] not in allowed:
    raise SystemExit(1)
patch_bytes = result.get("patch_bytes")
if isinstance(patch_bytes, bool) or not isinstance(patch_bytes, int):
    raise SystemExit(1)
if patch_bytes != patch_path.stat().st_size:
    raise SystemExit(1)
PY
}

do_run() {
    local agent="${1:?Usage: $0 --run <agent> <instance_id> [TIMEOUT] [--run-id ID]}"
    local instance_id="${2:?Usage: $0 --run <agent> <instance_id> [TIMEOUT] [--run-id ID]}"
    shift 2
    local timeout_sec="3600" requested_run_id="" profile="default" run_dir=""
    if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then
        timeout_sec="$1"
        shift
    fi
    while [ $# -gt 0 ]; do
        case "$1" in
            --timeout) timeout_sec="${2:?--timeout requires a number}"; shift 2 ;;
            --run-id) requested_run_id="${2:?--run-id requires a value}"; shift 2 ;;
            --profile) profile="${2:?--profile requires a value}"; shift 2 ;;
            --run-dir) run_dir="${2:?--run-dir requires a value}"; shift 2 ;;
            *) echo "Unknown option: $1"; return 2 ;;
        esac
    done
    if ! [[ "$timeout_sec" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Timeout must be a non-negative integer, got '${timeout_sec}'."
        return 2
    fi
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found."
        return 1
    fi
    local bundle_dir="${AGENTS_DIR}/${agent}/bundle"
    if [ ! -d "$bundle_dir" ]; then
        echo "ERROR: Agent bundle not found at ${bundle_dir}. Run './run.sh --build ${agent}' first."
        return 1
    fi
    ensure_docker || return 1

    if [ -z "$run_dir" ]; then
        local single_instance_file
        single_instance_file=$(mktemp)
        printf '%s\n' "$instance_id" > "$single_instance_file"
        run_dir=$(create_run_manifest "$agent" "$timeout_sec" "$single_instance_file" \
            "$requested_run_id" "$profile") || { rm -f "$single_instance_file"; return 1; }
        rm -f "$single_instance_file"
    else
        local manifest_agent
        manifest_agent=$(RUN_DIR="$run_dir" python3 -c \
            'import json,os; print(json.load(open(os.path.join(os.environ["RUN_DIR"],"manifest.json")))["agent"])')
        [ "$manifest_agent" = "$agent" ] || { echo "ERROR: Run belongs to ${manifest_agent}."; return 2; }
    fi
    echo "  Run: $(basename "$run_dir")"

    local attempt_dir attempt_id
    attempt_dir=$(python3 "$ARTIFACT_TOOL" begin-attempt \
        --run-dir "$run_dir" --instance-id "$instance_id") || return
    attempt_id=$(basename "$attempt_dir")
    chmod 777 "$attempt_dir"

    local inst_data repo_url base_commit problem_statement image_name
    if ! inst_data=$(get_instance "$instance_id"); then
        echo "ERROR: Could not load dataset record for ${instance_id}." >&2
        return 1
    fi
    if ! repo_url=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['repo'])") || \
            ! base_commit=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['base_commit'])") || \
            ! problem_statement=$(echo "$inst_data" | python3 -c "import sys,json; print(json.load(sys.stdin)['problem_statement'])"); then
        echo "ERROR: Dataset record is missing required fields for ${instance_id}." >&2
        return 1
    fi
    image_name=$(instance_to_image "$instance_id")

    if ! check_storage; then
        echo "Run './run.sh --cleanup' to free space, or set MAX_STORAGE_PCT"
        return 1
    fi
    if ! docker image inspect "$image_name" >/dev/null 2>&1; then
        if load_image_from_cache "$image_name"; then
            echo "  Loaded from cache: ${image_name}"
        else
            echo "Pulling swebench image: ${image_name}..."
            if ! docker pull "$image_name" 2>&1 | tail -3; then
                echo "ERROR: Failed to pull ${image_name}." >&2
                return 1
            fi
            save_image_to_cache "$image_name"
        fi
    fi

    echo "=============================================================================="
    echo "  [WORK] Running: ${agent} against ${instance_id}"
    echo "         Image: ${image_name}"
    echo "         Attempt: ${attempt_id}"
    echo "=============================================================================="

    local container_key container_name
    container_key=$(printf '%s' "$(basename "$run_dir")/${instance_id}/${attempt_id}" | \
        sha256sum | cut -c1-12)
    container_name="swe_${agent}_${instance_id}_${container_key}"
    release_container "$container_name"
    local docker_command=(
        docker run -d --init --stop-timeout 30
        --name "$container_name"
        "${DOCKER_RUN_FLAGS[@]}"
        -e "SWE_AGENT_NAME=${agent}"
        -e "SWE_OUTPUT_ROOT=/workspace/outputs/${agent}"
    )
    append_agent_runtime_env "$agent" docker_command
    docker_command+=(
        -v "${bundle_dir}:/agent:ro"
        -v "${attempt_dir}:/workspace/outputs/${agent}/${instance_id}"
        "$image_name"
        /agent/entrypoint.sh
        "$instance_id"
        "https://github.com/${repo_url}"
        "$base_commit"
        "$problem_statement"
    )

    local started_at cid start_rc elapsed
    started_at=$(date +%s)
    if cid=$("${docker_command[@]}"); then
        start_rc=0
    else
        start_rc=$?
    fi
    if [ "$start_rc" -ne 0 ] || [ -z "$cid" ]; then
        [ "$start_rc" -ne 0 ] || start_rc=1
        elapsed=$(( $(date +%s) - started_at ))
        write_host_terminal_result "$attempt_dir" "container_error" "$elapsed" "$start_rc"
        python3 "$ARTIFACT_TOOL" finalize-attempt --run-dir "$run_dir" \
            --instance-id "$instance_id" --attempt-id "$attempt_id" >/dev/null
        echo "ERROR: Docker could not start ${agent}/${instance_id} (exit ${start_rc})."
        return "$start_rc"
    fi

    ACTIVE_CONTAINER="$container_name"
    ACTIVE_ATTEMPT_DIR="$attempt_dir"
    INTERRUPT_REQUESTED=0
    docker logs -f "$cid" &
    local logs_pid=$!
    local wait_file="${attempt_dir}/container-wait.txt"
    if [ "$timeout_sec" -gt 0 ]; then
        timeout --signal=TERM --kill-after=5 "${timeout_sec}s" docker wait "$cid" > "$wait_file" &
    else
        docker wait "$cid" > "$wait_file" &
    fi
    local monitor_pid=$! monitor_rc
    if wait "$monitor_pid"; then
        monitor_rc=0
    else
        monitor_rc=$?
    fi

    local stop_requested=0
    if [ "$monitor_rc" -eq 124 ]; then
        stop_requested=1
        write_termination_request "timed_out" "hard_timeout" "$timeout_sec" || true
        docker stop --signal=TERM --time 30 "$cid" >/dev/null 2>&1 || true
    elif [ "$INTERRUPT_REQUESTED" -eq 1 ]; then
        stop_requested=1
        docker stop --signal=TERM --time 30 "$cid" >/dev/null 2>&1 || true
    elif [ "$monitor_rc" -ne 0 ]; then
        stop_requested=1
        echo "  WARNING: docker wait monitor exited with ${monitor_rc}; stopping the active container."
        write_termination_request "container_error" "wait_monitor_error" "$timeout_sec" || true
        docker stop --signal=TERM --time 30 "$cid" >/dev/null 2>&1 || true
    fi
    if [ "$stop_requested" -eq 1 ] && \
            ! timeout --signal=TERM --kill-after=5 40s docker wait "$cid" >/dev/null 2>&1; then
        echo "  WARNING: Graceful stop did not complete; force-killing ${container_name}." >&2
        docker kill "$cid" >/dev/null 2>&1 || true
        timeout --signal=TERM --kill-after=2 10s docker wait "$cid" >/dev/null 2>&1 || true
    fi
    wait "$logs_pid" 2>/dev/null || true
    elapsed=$(( $(date +%s) - started_at ))

    local state_file="${attempt_dir}/container-state.json" state_tmp inspect_ok=0
    state_tmp=$(mktemp "${attempt_dir}/.container-state.XXXXXX")
    if docker inspect --format '{{json .State}}' "$cid" > "$state_tmp" 2>/dev/null; then
        mv "$state_tmp" "$state_file"
        inspect_ok=1
    else
        rm -f "$state_tmp"
        echo "  WARNING: Container state could not be captured; retaining ${container_name}."
    fi

    local container_exit=-1 oom_killed=0
    if [ "$inspect_ok" -eq 1 ]; then
        local state_values
        if state_values=$(STATE_FILE="$state_file" python3 -c \
            'import json,os; s=json.load(open(os.environ["STATE_FILE"])); print(s.get("ExitCode",-1), int(bool(s.get("OOMKilled",False))))' \
            2>/dev/null); then
            read -r container_exit oom_killed <<< "$state_values"
        else
            inspect_ok=0
            echo "  WARNING: Container State JSON is invalid; retaining ${container_name}."
        fi
    fi

    chown -R "$(id -u):$(id -g)" "$attempt_dir" 2>/dev/null || true
    local artifacts_valid=0
    if attempt_artifacts_valid "$attempt_dir"; then
        artifacts_valid=1
    fi
    if [ "$artifacts_valid" -ne 1 ]; then
        record_host_observation "${attempt_dir}/host-observation.json" "invalid_result" \
            "$elapsed" "$monitor_rc" "$container_name"
        ACTIVE_CONTAINER=""
        ACTIVE_ATTEMPT_DIR=""
        echo "ERROR: Attempt artifacts are incomplete; retained stopped container ${container_name}."
        return 1
    fi

    local status_override="" retained_container=""
    if [ "$oom_killed" -eq 1 ]; then
        status_override="oom_killed"
    elif [ "$monitor_rc" -eq 124 ]; then
        status_override="timed_out"
    elif [ "$INTERRUPT_REQUESTED" -eq 1 ]; then
        status_override="operator_cancelled"
    elif [ "$monitor_rc" -ne 0 ]; then
        status_override="container_error"
    elif [ "$container_exit" -ne 0 ]; then
        local entrypoint_status
        entrypoint_status=$(RESULT_FILE="${attempt_dir}/result.json" python3 -c \
            'import json,os; print(json.load(open(os.environ["RESULT_FILE"])).get("status","invalid_result"))')
        case "$entrypoint_status" in
            agent_error|invalid_result|timed_out|operator_cancelled) ;;
            *) status_override="container_error" ;;
        esac
    fi
    [ "$inspect_ok" -eq 1 ] || retained_container="$container_name"
    if ! record_host_result "${attempt_dir}/result.json" "$status_override" "$container_exit" \
        "$elapsed" "$monitor_rc" "$timeout_sec" "$state_file" 1 "$retained_container"; then
        ACTIVE_CONTAINER=""
        ACTIVE_ATTEMPT_DIR=""
        echo "ERROR: Host result finalization failed; retained ${container_name}." >&2
        return 1
    fi

    if ! python3 "$ARTIFACT_TOOL" finalize-attempt --run-dir "$run_dir" \
        --instance-id "$instance_id" --attempt-id "$attempt_id" >/dev/null; then
        ACTIVE_CONTAINER=""
        ACTIVE_ATTEMPT_DIR=""
        echo "ERROR: Attempt manifest finalization failed; retained ${container_name}." >&2
        return 1
    fi
    if [ "$inspect_ok" -eq 1 ]; then
        docker rm "$cid" >/dev/null 2>&1 || \
            echo "  WARNING: Could not remove stopped container ${container_name}."
    fi
    ACTIVE_CONTAINER=""
    ACTIVE_ATTEMPT_DIR=""

    local final_status
    final_status=$(RESULT_FILE="${attempt_dir}/result.json" python3 -c \
        'import json,os; print(json.load(open(os.environ["RESULT_FILE"])).get("status","unknown"))')
    echo "  Result: ${final_status}; artifacts: ${attempt_dir}"
    case "$final_status" in
        patch_collected|no_patch|resolved) return 0 ;;
        timed_out) return 124 ;;
        operator_cancelled) return 130 ;;
        *) return 1 ;;
    esac
}

# ==============================================================================
# WORK-ALL — run agent against all instances
#
# Usage: ./run.sh --run-all <agent> [--timeout <seconds>] [--resume] [--run-id ID]
#   --timeout N  Request a checkpoint at N seconds (default: 3600 = 1 hour)
#   --resume     Continue the named/latest manifest, running only untouched tasks
# ==============================================================================
do_run_all() {
    local agent="${1:?Usage: $0 --run-all <agent> [--timeout N] [--resume]}"
    shift

    local timeout_sec="3600" timeout_set=0 resume=0 requested_run_id=""
    local profile="default" profile_set=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --timeout) timeout_sec="${2:?--timeout requires a number}"; timeout_set=1; shift 2 ;;
            --resume)  resume=1; shift ;;
            --run-id) requested_run_id="${2:?--run-id requires a value}"; shift 2 ;;
            --profile) profile="${2:?--profile requires a value}"; profile_set=1; shift 2 ;;
            *)         echo "Unknown option: $1"; return 2 ;;
        esac
    done
    if ! [[ "$timeout_sec" =~ ^[0-9]+$ ]]; then
        echo "ERROR: Timeout must be a non-negative integer, got '${timeout_sec}'."
        return 2
    fi
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found."
        return 1
    fi
    ensure_docker || return 1

    local count=0 skipped=0 failed=0
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

    local run_dir
    if [ "$resume" -eq 1 ]; then
        run_dir=$(resolve_run_dir "$agent" "$requested_run_id") || { rm -f "$inst_file"; return 1; }
        local resume_values manifest_timeout manifest_profile manifest_dataset_sha current_dataset_sha
        resume_values=$(RUN_DIR="$run_dir" INSTANCES_FILE="$inst_file" python3 - <<'PY'
import json
import os
from pathlib import Path

manifest = json.load(open(Path(os.environ["RUN_DIR"]) / "manifest.json"))
current = [line.strip() for line in Path(os.environ["INSTANCES_FILE"]).read_text().splitlines() if line.strip()]
if manifest["dataset"]["instance_ids"] != current:
    raise SystemExit("instance ordering mismatch")
print(manifest["config"]["timeout_seconds"], manifest["profile"], manifest["dataset"]["cache_sha256"])
PY
        ) || {
            echo "ERROR: The current dataset ordering does not match the run manifest; refusing resume." >&2
            rm -f "$inst_file"
            return 2
        }
        read -r manifest_timeout manifest_profile manifest_dataset_sha <<< "$resume_values"
        current_dataset_sha=$(dataset_fingerprint "$inst_file")
        if [ "$current_dataset_sha" != "$manifest_dataset_sha" ]; then
            echo "ERROR: The dataset fingerprint changed; refusing resume." >&2
            rm -f "$inst_file"
            return 2
        fi
        if [ "$timeout_set" -eq 1 ] && [ "$timeout_sec" != "$manifest_timeout" ]; then
            echo "ERROR: --timeout differs from the immutable run manifest (${manifest_timeout})." >&2
            rm -f "$inst_file"
            return 2
        fi
        if [ "$profile_set" -eq 1 ] && [ "$profile" != "$manifest_profile" ]; then
            echo "ERROR: --profile differs from the immutable run manifest (${manifest_profile})." >&2
            rm -f "$inst_file"
            return 2
        fi
        timeout_sec="$manifest_timeout"
        profile="$manifest_profile"
    else
        run_dir=$(create_run_manifest "$agent" "$timeout_sec" "$inst_file" \
            "$requested_run_id" "$profile") || { rm -f "$inst_file"; return 1; }
    fi
    echo "=== Run: $(basename "$run_dir") ==="

    while read -r instance_id; do
        echo "=============================================================================="
        echo "  [WORK] Processing: ${instance_id}"
        echo "=============================================================================="
        local task_state
        task_state=$(python3 "$ARTIFACT_TOOL" task-state \
            --run-dir "$run_dir" --instance-id "$instance_id") || { failed=$((failed + 1)); continue; }
        if [ "$resume" -eq 1 ] && [ "$task_state" != "pending" ]; then
            echo "  Skipping manifest task with existing attempt state: ${task_state}"
            skipped=$((skipped + 1))
            continue
        fi
        count=$((count + 1))
        local run_rc=0
        if do_run "$agent" "$instance_id" --timeout "$timeout_sec" --run-dir "$run_dir"; then
            run_rc=0
        else
            run_rc=$?
            failed=$((failed + 1))
        fi
        if [ "$run_rc" -eq 130 ]; then
            echo "  Operator cancellation stops the queue; untouched tasks remain resumable."
            break
        fi
    done < "$inst_file"
    rm -f "$inst_file"
    echo ""
    echo "Done: ${count} run, ${skipped} skipped (resume), ${failed} failed"
    echo "Run artifacts: ${run_dir}"
    [ "$failed" -eq 0 ]
}

# ==============================================================================
# EVAL — use official swebench harness
# ==============================================================================
do_eval() {
    local agent="${1:?Usage: $0 --eval <agent> [--run-id ID]}"
    shift
    local requested_run_id=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --run-id) requested_run_id="${2:?--run-id requires a value}"; shift 2 ;;
            *) echo "Unknown option: $1"; return 2 ;;
        esac
    done
    if [ ! -d "${AGENTS_DIR}/${agent}" ]; then
        echo "ERROR: Agent '${agent}' not found."
        return 1
    fi

    # Check swebench is installed
    if [ ! -f "${SWEBENCH_PY}" ]; then
        echo "ERROR: swebench not installed. Run './run.sh --init' first."
        return 1
    fi

    local run_dir
    run_dir=$(resolve_run_dir "$agent" "$requested_run_id") || return
    local evaluation_id="eval-$(date -u +%Y%m%dT%H%M%S%NZ)-$$"
    local eval_dir="${run_dir}/reports/evaluations/${evaluation_id}"
    mkdir -p "$eval_dir"

    # The manifest selection snapshot is the sole evaluation input. The helper
    # verifies each finalized patch digest before writing predictions.
    local instance_ids=()

    local preds="${eval_dir}/predictions.jsonl"
    local selection_file="${eval_dir}/selected-attempts.json"
    local prediction_count
    prediction_count=$(python3 "$ARTIFACT_TOOL" build-predictions \
        --run-dir "$run_dir" --output "$preds" --selection-output "$selection_file") || return
    mapfile -t instance_ids < <(PREDS="$preds" python3 -c \
        'import json,os; [print(json.loads(line)["instance_id"]) for line in open(os.environ["PREDS"])]')

    echo "=============================================================================="
    echo "  [EVAL] Run $(basename "$run_dir"): ${prediction_count} selected patch(es)"
    echo "=============================================================================="

    local report_dir="${eval_dir}/harness"
    mkdir -p "$report_dir"
    if ! (
        cd "$eval_dir"
        "${SWEBENCH_PY}" -m swebench.harness.run_evaluation \
            --dataset_name "${HF_DATASET}" \
            --split "test" \
            --predictions_path "${preds}" \
            --max_workers 1 \
            --cache_level instance \
            --report_dir "${report_dir}" \
            --run_id "${evaluation_id}" \
            -i "${instance_ids[@]}"
    ); then
        echo "ERROR: The official SWE-bench evaluator failed for ${evaluation_id}." >&2
        return 1
    fi

    # Evaluation is an overlay. Finalized attempts remain immutable.
    local official_report
    official_report=$(EVAL_DIR="$eval_dir" python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["EVAL_DIR"])
matches = []
for path in root.rglob("*.json"):
    if path.name in {"selected-attempts.json", "evaluation.json"}:
        continue
    try:
        data = json.load(path.open())
    except (OSError, json.JSONDecodeError):
        continue
    if isinstance(data, dict) and "resolved_ids" in data and "unresolved_ids" in data:
        matches.append(path)
if not matches:
    raise SystemExit("official swebench aggregate report was not found")
print(max(matches, key=lambda item: item.stat().st_mtime_ns))
PY
    ) || return 1
    python3 "$ARTIFACT_TOOL" record-evaluation \
        --run-dir "$run_dir" \
        --evaluation-id "$evaluation_id" \
        --report-file "$official_report" \
        --selection-file "$selection_file" >/dev/null || return
    echo "Evaluation overlay: ${eval_dir}/evaluation.json"
}

# ==============================================================================
# SUMMARIZE — combine and summarize all collected results
# ==============================================================================
summarize_agent() {
    local agent="$1"
    local requested_run_id="${2:-}"
    local run_dir
    run_dir=$(resolve_run_dir "$agent" "$requested_run_id") || return
    local out_json="${run_dir}/reports/summary.json"
    python3 "$ARTIFACT_TOOL" summary --run-dir "$run_dir" --output "$out_json" >/dev/null || return
    SUMMARY_FILE="$out_json" python3 - <<'PY' || return
import json
import os

summary = json.load(open(os.environ["SUMMARY_FILE"]))
rows = summary["rows"]
print(f"Run: {summary['run_id']} | Agent: {summary['agent']} | Profile: {summary['profile']}")
print(f"{'instance_id':42s} {'status':20s} {'selected':14s} {'local_eval':12s} {'patch_B':>8s}")
for row in rows:
    print(
        f"{row['instance_id']:42s} {str(row['status']):20s} "
        f"{str(row['selected_attempt']):14s} {str(row['local_eval']):12s} "
        f"{str(row['patch_bytes'] or 0):>8s}"
    )
print(
    f"\nPlanned: {summary['planned']} | attempted: {summary['attempted']} | "
    f"selected: {summary['selected']} | resolved: {summary['resolved']} | "
    f"failed: {summary['failed']} | error: {summary['errored']} | "
    f"timed_out: {summary['timed_out']}"
)
print(f"Summary written to {os.environ['SUMMARY_FILE']}")
PY
}

do_summarize() {
    local requested_agent="" requested_run_id=""
    if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
        requested_agent="$1"
        shift
    fi
    while [ $# -gt 0 ]; do
        case "$1" in
            --run-id) requested_run_id="${2:?--run-id requires a value}"; shift 2 ;;
            *) echo "Unknown option: $1"; return 2 ;;
        esac
    done
    if [ -n "$requested_agent" ]; then
        summarize_agent "$requested_agent" "$requested_run_id"
        return
    fi
    if [ -n "$requested_run_id" ]; then
        echo "ERROR: --run-id requires an explicit agent." >&2
        return 2
    fi
    [ -d "${RUNS_DIR}/latest" ] || { echo "No manifest-backed runs found."; return; }
    local found=0 pointer
    for pointer in "${RUNS_DIR}/latest"/*; do
        [ -f "$pointer" ] && [ ! -L "$pointer" ] || continue
        found=1
        summarize_agent "$(basename "$pointer")"
        echo ""
    done
    [ "$found" -eq 1 ] || echo "No manifest-backed runs found."
}

# ==============================================================================
# STATUS — show completion status
# ==============================================================================
show_agent_status() {
    local agent="$1"
    local requested_run_id="${2:-}" run_dir summary_tmp
    run_dir=$(resolve_run_dir "$agent" "$requested_run_id") || return
    summary_tmp=$(mktemp)
    if ! python3 "$ARTIFACT_TOOL" summary --run-dir "$run_dir" > "$summary_tmp"; then
        rm -f "$summary_tmp"
        return 1
    fi
    STATUS_SUMMARY_FILE="$summary_tmp" python3 - <<'PY' || { rm -f "$summary_tmp"; return 1; }
import json
import os

summary = json.load(open(os.environ["STATUS_SUMMARY_FILE"]))
print(f"Run: {summary['run_id']} | Agent: {summary['agent']} | Profile: {summary['profile']}")
for row in summary["rows"]:
    status = row["local_eval"] or row["status"]
    marker = {
        "resolved": "✓",
        "failed": "✗",
        "error": "!",
        "timed_out": "⌛",
        "no_patch": "—",
        "pending": "·",
    }.get(status, "?")
    print(f"{marker} {row['instance_id']} ({status}; attempts={row['attempts']})")
print(
    f"\nPlanned: {summary['planned']} | Attempted: {summary['attempted']} | "
    f"Selected: {summary['selected']} | Resolved: {summary['resolved']} | "
    f"Failed: {summary['failed']} | Errors: {summary['errored']} | "
    f"Timed out: {summary['timed_out']}"
)
PY
    rm -f "$summary_tmp"
}

do_status() {
    local requested_agent="" requested_run_id=""
    if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
        requested_agent="$1"
        shift
    fi
    while [ $# -gt 0 ]; do
        case "$1" in
            --run-id) requested_run_id="${2:?--run-id requires a value}"; shift 2 ;;
            *) echo "Unknown option: $1"; return 2 ;;
        esac
    done
    echo "=== SWE-bench Harness Status ==="
    echo "Runs directory: ${RUNS_DIR}"
    echo ""

    if [ -n "$requested_agent" ]; then
        show_agent_status "$requested_agent" "$requested_run_id"
        return
    fi
    if [ -n "$requested_run_id" ]; then
        echo "ERROR: --run-id requires an explicit agent." >&2
        return 2
    fi
    [ -d "${RUNS_DIR}/latest" ] || { echo "No manifest-backed runs found."; return; }
    local found=0 pointer
    for pointer in "${RUNS_DIR}/latest"/*; do
        [ -f "$pointer" ] && [ ! -L "$pointer" ] || continue
        found=1
        show_agent_status "$(basename "$pointer")"
        echo ""
    done
    [ "$found" -eq 1 ] || echo "No manifest-backed runs found."
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
    )
    append_agent_runtime_env "$agent" docker_command
    docker_command+=(
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
            do_run "${@:2}"
            ;;
        --run-all)
            do_run_all "${@:2}"
            ;;
        --eval)
            do_eval "${@:2}"
            ;;
        --summarize)
            do_summarize "${@:2}"
            ;;
        --status)
            do_status "${@:2}"
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
            do_cleanup_partial "${@:2}"
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
