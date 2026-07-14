#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT

export SWE_WORKSPACE_DIR="${TEST_ROOT}/workspace"
# shellcheck source=../run.sh
source "${REPO_ROOT}/run.sh"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local haystack="$1" needle="$2"
    [[ "$haystack" == *"$needle"* ]] || fail "expected output to contain: $needle"
}

echo "1..7"

bash -n "${REPO_ROOT}/run.sh" \
    "${REPO_ROOT}/agents/pi/entrypoint.sh" \
    "${REPO_ROOT}/agents/codex/entrypoint.sh" \
    "${REPO_ROOT}/agents/codex/build_bundle.sh"
echo "ok 1 - shell scripts parse"

mkdir -p "${OUTPUT_DIR}/pi/example__repo-1" "${OUTPUT_DIR}/codex/example__repo-1"
printf '%s\n' '{"status":"patch_collected","patch_bytes":10,"elapsed_seconds":2,"agent_exit_code":0}' \
    > "${OUTPUT_DIR}/pi/example__repo-1/result.json"
printf '%s\n' '{"status":"timed_out","patch_bytes":0,"elapsed_seconds":17}' \
    > "${OUTPUT_DIR}/codex/example__repo-1/result.json"

summarize_agent pi >/dev/null
summarize_agent codex >/dev/null
python3 - "${OUTPUT_DIR}" <<'PY'
import json
import os
import sys

root = sys.argv[1]
pi = json.load(open(os.path.join(root, "pi", "summary.json")))
codex = json.load(open(os.path.join(root, "codex", "summary.json")))
assert pi["agent"] == "pi" and pi["total"] == 1 and pi["timed_out"] == 0
assert codex["agent"] == "codex" and codex["total"] == 1 and codex["timed_out"] == 1
PY
echo "ok 2 - summaries are isolated by agent"

status_output=$(do_status codex)
assert_contains "$status_output" "Agent: codex"
assert_contains "$status_output" "Timed out: 1"
[[ "$status_output" != *"Agent: pi"* ]] || fail "selected status included another agent"
echo "ok 3 - status is isolated by agent"

result_file="${OUTPUT_DIR}/pi/example__repo-1/result.json"
record_host_result "$result_file" container_error 9 11
python3 - "$result_file" <<'PY'
import json
import sys

result = json.load(open(sys.argv[1]))
assert result["status"] == "container_error"
assert result["container_exit_code"] == 9
assert result["agent_exit_code"] == 0
PY
echo "ok 4 - host errors preserve agent metadata"

fetch_dataset() {
    printf '%s\n' '[{"instance_id":"example__repo-2"}]'
}
CALLED_ARGS=""
do_run() {
    CALLED_ARGS="$*"
}
do_run_all pi --timeout 17 >/dev/null
[ "$CALLED_ARGS" = "pi example__repo-2 17" ] || fail "run-all did not forward timeout: $CALLED_ARGS"
echo "ok 5 - run-all forwards timeout"

printf '%s\n' 'pi-only-patch' > "${OUTPUT_DIR}/pi/example__repo-1/patch.diff"
printf '%s\n' 'codex-only-patch' > "${OUTPUT_DIR}/codex/example__repo-1/patch.diff"
SWEBENCH_PY="$(command -v python3)"
export PYTHONPATH="${REPO_ROOT}/tests/fake_swebench"
require_docker() {
    return 0
}
do_eval pi >/dev/null
python3 - "${OUTPUT_DIR}" <<'PY'
import json
import os
import sys

root = sys.argv[1]
predictions = [
    json.loads(line)
    for line in open(os.path.join(root, "pi", "predictions.jsonl"))
]
assert len(predictions) == 1
assert predictions[0]["model_name_or_path"] == "pi"
assert predictions[0]["model_patch"] == "pi-only-patch\n"
result = json.load(open(os.path.join(root, "pi", "example__repo-1", "result.json")))
assert result["status"] == "resolved" and result["local_eval"] == "resolved"
assert result["agent_exit_code"] == 0 and result["container_exit_code"] == 9
PY
echo "ok 6 - evaluation reads and folds only the selected agent"

docker_log="${TEST_ROOT}/docker.log"
docker() {
    printf '%s\n' "$*" >> "$docker_log"
    if [ "${1:-} ${2:-}" = "ps -aq" ]; then
        printf '%s\n' harness_container
    elif [ "${1:-}" = "images" ]; then
        printf '%s\n' 'swebench/sweb.eval.x86_64.example image_one' 'ubuntu image_two'
    fi
}
do_cleanup >/dev/null
cleanup_calls=$(<"$docker_log")
assert_contains "$cleanup_calls" "ps -aq --filter name=^/swe_"
assert_contains "$cleanup_calls" "rm -f harness_container"
assert_contains "$cleanup_calls" "rmi image_one"
[[ "$cleanup_calls" != *"image_two"* ]] || fail "cleanup selected unrelated image"
echo "ok 7 - cleanup targets only harness resources"
