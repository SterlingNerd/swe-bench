#!/bin/bash
# ==============================================================================
# Fake Artifact Tool — mocks scripts/run_artifacts.py for testing
# ==============================================================================
set -euo pipefail

ACTION="${1:-}"
shift || true

case "$ACTION" in
    resolve-run)
        RUNS_DIR="" AGENT="" RUN_ID=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --runs-dir) RUNS_DIR="$2"; shift 2 ;;
                --agent) AGENT="$2"; shift 2 ;;
                --run-id) RUN_ID="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        if [ -n "$RUN_ID" ] && [ -d "${RUNS_DIR}/${RUN_ID}" ]; then
            echo "${RUNS_DIR}/${RUN_ID}"
        elif [ -d "${RUNS_DIR}/latest/${AGENT}" ]; then
            cat "${RUNS_DIR}/latest/${AGENT}"
        else
            echo "ERROR: run directory not found" >&2
            exit 1
        fi
        ;;

    task-state)
        RUN_DIR="" INSTANCE_ID=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --run-dir) RUN_DIR="$2"; shift 2 ;;
                --instance-id) INSTANCE_ID="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        # Check if task has been finalized (has container-state.json)
        if [ -f "${RUN_DIR}/tasks/${INSTANCE_ID}/attempts/attempt-0001/container-state.json" ]; then
            echo "collected"
        else
            echo "pending"
        fi
        ;;

    build-predictions)
        RUN_DIR="" OUTPUT="" SELECTION_OUTPUT=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --run-dir) RUN_DIR="$2"; shift 2 ;;
                --output) OUTPUT="$2"; shift 2 ;;
                --selection-output) SELECTION_OUTPUT="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        mkdir -p "$(dirname "$OUTPUT")" "$(dirname "$SELECTION_OUTPUT")"
        
        INSTANCE_COUNT=0
        > "$OUTPUT"
        > "$SELECTION_OUTPUT"
        
        if [ -d "${RUN_DIR}/tasks" ]; then
            for attempt_dir in "${RUN_DIR}"/tasks/*/attempts/attempt-*/; do
                [ -d "$attempt_dir" ] || continue
                [ -f "${attempt_dir}container-state.json" ] || continue
                
                instance_id=$(basename "$(dirname "$(dirname "$attempt_dir")")")
                patch_file="${attempt_dir}patch.diff"
                
                if [ -s "$patch_file" ]; then
                    attempt_id=$(basename "$(dirname "$attempt_dir")")
                    echo "{\"instance_id\": \"${instance_id}\", \"model_name_or_path\": \"smoke-test\", \"model_patch\": \"$(cat "$patch_file")\"}" >> "$OUTPUT"
                    echo "{\"instance_id\": \"${instance_id}\", \"attempt_id\": \"${attempt_id}\"}" >> "$SELECTION_OUTPUT"
                    INSTANCE_COUNT=$((INSTANCE_COUNT + 1))
                fi
            done
        fi
        
        echo "$INSTANCE_COUNT"
        ;;

    finalize-attempt)
        RUN_DIR="" INSTANCE_ID="" ATTEMPT_ID=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --run-dir) RUN_DIR="$2"; shift 2 ;;
                --instance-id) INSTANCE_ID="$2"; shift 2 ;;
                --attempt-id) ATTEMPT_ID="$2"; shift 2 ;;
                *) shift ;;
            esac
        done
        # Create manifest if it doesn't exist
        if [ ! -f "${RUN_DIR}/manifest.json" ]; then
            cat > "${RUN_DIR}/manifest.json" <<MANIFEST
{
  "schema_version": 1,
  "run_id": "$(basename "$RUN_DIR")",
  "agent": "smoke-test",
  "tasks": {
    "${INSTANCE_ID}": {
      "selected_attempt": "${ATTEMPT_ID}"
    }
  },
  "run_config": {"timeout": 3600},
  "evaluations": []
}
MANIFEST
        fi
        
        # Update manifest with selected attempt
        python3 -c "
import json, sys
path = '${RUN_DIR}/manifest.json'
with open(path) as f:
    m = json.load(f)
m['tasks']['${INSTANCE_ID}']['selected_attempt'] = '${ATTEMPT_ID}'
with open(path, 'w') as f:
    json.dump(m, f, indent=2)
" 2>/dev/null || true
        ;;

    *)
        echo "Unknown artifact tool action: $ACTION" >&2
        exit 1
        ;;
esac
