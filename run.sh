#!/bin/bash
# ==============================================================================
# SWE-bench Orchestrator — unified build, index, list, and run
#
# Architecture: Self-contained agent bundles mounted into swebench harness images.
# We do NOT build Docker images — the swebench harness provides the container runtime.
#
# Usage:
#   ./run.sh --help              Show this help
#   ./run.sh --index             Fetch and cache problem listings from HuggingFace
#   ./run.sh --list [filter]     List problems (optional grep filter)
#   ./run.sh --build [agent]     Build agent bundle(s) only
#   ./run.sh --rebuild [scope] Rebuild from scratch (--no-cache): all|<agent>
#   ./run.sh --eval <agent>      Evaluate collected patches (Docker-free, quickstart-style)
#   ./run.sh --summarize [agent]  Combine and summarize collected results
#   ./run.sh --status            Show completion status
#
# Workflow:
#   1. ./run.sh --index          (one-time: cache the dataset)
#   2. ./run.sh --build          (build agent bundles — no Docker images)
#   3. Harness runs agents using its own images, mounting our bundle at /agent
#   4. ./run.sh --eval <agent>   (evaluate collected patches, Docker-free)
#   5. ./run.sh --status         (inspect results)
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
Self-contained agent bundles mounted into swebench harness images.

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

  --eval <AGENT>
      Evaluate the patches collected for AGENT in a previous run.
      This is a SEPARATE step from the agent "work" step and needs NO Docker
      access. Following the SWE-bench quickstart methodology, for each patch it
      clones the repo at the base commit, applies the model patch + the
      dataset's test_patch, installs the project in a venv, and runs the
      instance's FAIL_TO_PASS and PASS_TO_PASS tests (Django uses
      tests/runtests.py; other repos use pytest). Writes local_eval.json and
      folds the result into result.json. Can be slow (bootstraps each project).

  --summarize [AGENT]
      Combine and summarize all collected results into outputs/summary.json and
      print a table (status / local_eval / patch size). Optional AGENT filter
      matches instance ids prefixed with '<agent>__'.

  --status
      Summarize progress of collected runs: totals plus per-instance status
      (resolved / failed / no patch / unknown) read from result.json files.

ENVIRONMENT
  SWE_WORKSPACE_DIR
      Root of the workspace (default: ./workspace). The outputs directory is
      derived from it: outputs = \$workspace/outputs

PREREQUISITES
  - A HuggingFace dataset fetch happens on first --index / --list.
  - The swebench harness provides the container runtime for running agents.

EXAMPLES
  $(basename "$0") --index
  $(basename "$0") --list "django"
  $(basename "$0") --build
  $(basename "$0") --rebuild           # force fresh build of all bundles (latest pi CLI)
  $(basename "$0") --rebuild pi        # rebuild only the 'pi' agent bundle
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

    # Build agent bundles
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
# REBUILD — always from scratch (--no-cache), scope-controlled by arg:
#   --rebuild           -> all agent bundles (default: 'all')
#   --rebuild all       -> all agent bundles
#   --rebuild <agent>   -> only that agent bundle
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
# EVAL — Docker-free evaluation (SWE-bench quickstart methodology)
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

    local eval_dir="${OUTPUT_DIR}"
    [ -d "$eval_dir" ] || { echo "No outputs found. Run instances first (--run or --run-all)."; return; }

    local instance_ids=()
    for d in "${eval_dir}"/*/; do
        [ -d "$d" ] && [ -f "${d}patch.diff" ] && instance_ids+=("$(basename "$d")")
    done
    [ ${#instance_ids[@]} -eq 0 ] && { echo "No patches found to evaluate. Run instances first."; return; }

    echo "=============================================================================="
    echo "Evaluating ${#instance_ids[@]} patch(es) for '${agent}' (quickstart-style, no Docker)"
    echo "=============================================================================="

    # Also write predictions.json in the standard SWE-bench format
    local preds="${eval_dir}/predictions.json"
    EVAL_DIR="${eval_dir}" AGENT_NAME="${agent}" PREDS="${preds}" python3 -c "
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

    if [ ! -f "${REPO_ROOT}/eval_local_worker.py" ]; then
        echo "ERROR: eval_local_worker.py not found at ${REPO_ROOT}/eval_local_worker.py"
        exit 1
    fi

    for iid in "${instance_ids[@]}"; do
        local out_dir="${eval_dir}/${iid}"
        echo "--- ${iid} ---"
        # Prepare eval input from the dataset (host python)
        INSTANCE_ID="$iid" CACHE_FILE="$CACHE_FILE" OUT_DIR="$out_dir" python3 - <<'PY'
import json, os
iid=os.environ['INSTANCE_ID']; cache=os.environ['CACHE_FILE']; out=os.environ['OUT_DIR']
data=json.load(open(cache))
inst=next((i for i in data if i['instance_id']==iid), None)
assert inst, "instance not found: "+iid
inp={
  "instance_id": iid,
  "repo": inst["repo"],
  "base_commit": inst["base_commit"],
  "test_patch": inst.get("test_patch",""),
  "FAIL_TO_PASS": json.loads(inst.get("FAIL_TO_PASS","[]") or "[]"),
  "PASS_TO_PASS": json.loads(inst.get("PASS_TO_PASS","[]") or "[]"),
}
json.dump(inp, open(os.path.join(out,"eval_local_input.json"),"w"), indent=2)
print("prepared", iid)
PY
        # Run the worker on the host (no Docker — uses host Python + git)
        python3 "${REPO_ROOT}/eval_local_worker.py" "${out_dir}"

        # Fold the result into result.json
        if [ -f "$out_dir/local_eval.json" ]; then
            OUT_DIR="$out_dir" python3 - <<'PY'
import json, os
out=os.environ['OUT_DIR']
le=json.load(open(os.path.join(out,'local_eval.json')))
rp=os.path.join(out,'result.json')
meta=json.load(open(rp)) if os.path.exists(rp) else {}
meta['local_eval']=le['status']
meta['local_eval_detail']={'install_ok':le.get('install_ok'),
                           'model_patch_applied':le.get('model_patch_applied'),
                           'tests':le.get('tests')}
json.dump(meta, open(rp,'w'), indent=2)
print("updated", os.path.basename(out), "->", le['status'])
PY
        fi
    done
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
    meta=json.load(open(rp))
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
print(f"{'instance_id':42s} {'status':12s} {'local_eval':12s} {'patch_B':>8s}")
for r in rows:
    print(f"{r['instance_id']:42s} {str(r['status']):12s} {str(r['local_eval']):12s} {str(r['patch_bytes'] or 0):>8s}")
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
    --eval)
        do_eval "${2:-}"
        ;;
    --summarize)
        do_summarize "${2:-}"
        ;;
    --status)
        do_status
        ;;
    *)
        echo "Unknown option: $1"
        show_help
        exit 1
        ;;
esac
