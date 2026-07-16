#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT

export SWE_WORKSPACE_DIR="${TEST_ROOT}/workspace"
# shellcheck source=../run.sh
source "${REPO_ROOT}/run.sh"
# run.sh installs production signal/exit traps. Tests use mocked Docker and need
# their own cleanup trap so a no-container grep result cannot change TAP status.
trap 'rm -rf "$TEST_ROOT"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local haystack="$1" needle="$2"
    [[ "$haystack" == *"$needle"* ]] || fail "expected output to contain: $needle"
}

docker_log="${TEST_ROOT}/docker.log"
docker() {
    printf '%s\n' "$*" >> "$docker_log"
    if [ "${1:-} ${2:-}" = "ps -aq" ]; then
        printf '%s\n' harness_container
    elif [ "${1:-}" = "images" ]; then
        printf '%s\n' 'swebench/sweb.eval.x86_64.example image_one' 'ubuntu image_two'
    fi
}

echo "1..9"

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

do_cleanup >/dev/null
cleanup_calls=$(<"$docker_log")
assert_contains "$cleanup_calls" "ps -aq --filter name=^/swe_"
assert_contains "$cleanup_calls" "rm -f harness_container"
assert_contains "$cleanup_calls" "rmi image_one"
[[ "$cleanup_calls" != *"image_two"* ]] || fail "cleanup selected unrelated image"
echo "ok 7 - cleanup targets only harness resources"

export SWE_CODEX_MODEL="test-model"
export SWE_CODEX_BASE_URL="http://model.invalid/v1"
export SWE_CODEX_API_KEY="test-token"
export SWE_CODEX_CONTEXT_WINDOW="8192"
export SWE_CODEX_AUTO_COMPACT_TOKEN_LIMIT="7000"
runtime_args=(docker run)
append_agent_runtime_env codex runtime_args
runtime_args_text="${runtime_args[*]}"
assert_contains "$runtime_args_text" "SWE_CODEX_MODEL=test-model"
assert_contains "$runtime_args_text" "SWE_CODEX_BASE_URL=http://model.invalid/v1"
assert_contains "$runtime_args_text" "SWE_CODEX_API_KEY"
[[ "$runtime_args_text" != *"test-token"* ]] || fail "Codex API key leaked into argv"
pi_runtime_args=(docker run)
append_agent_runtime_env pi pi_runtime_args
[ "${#pi_runtime_args[@]}" -eq 2 ] || fail "Codex runtime settings leaked into Pi"
unset SWE_CODEX_MODEL SWE_CODEX_BASE_URL SWE_CODEX_API_KEY
unset SWE_CODEX_CONTEXT_WINDOW SWE_CODEX_AUTO_COMPACT_TOKEN_LIMIT
echo "ok 8 - Codex runtime settings are isolated and forwarded"

fake_bundle="${TEST_ROOT}/codex-bundle"
fake_testbed="${TEST_ROOT}/testbed"
fake_outputs="${TEST_ROOT}/codex-outputs"
mkdir -p "${fake_bundle}/bin" "$fake_testbed"
cp "${REPO_ROOT}/agents/codex/config.toml" "${fake_bundle}/config.toml"
git -C "$fake_testbed" init -q
printf '%s\n' original > "${fake_testbed}/original.txt"
git -C "$fake_testbed" add original.txt
git -C "$fake_testbed" -c user.name=Test -c user.email=test@example.invalid \
    commit -qm initial
base_commit=$(git -C "$fake_testbed" rev-parse HEAD)

cat > "${fake_bundle}/bin/codex" <<'SH'
#!/bin/bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then
    echo "codex-cli-test"
    exit 0
fi
[ "${1:-}" = "exec" ] || exit 2
cp "${CODEX_HOME}/config.toml" "$FAKE_CAPTURE_CONFIG"
output_file=""
while [ $# -gt 0 ]; do
    if [ "$1" = "--output-last-message" ]; then
        output_file="$2"
        shift 2
    else
        shift
    fi
done
printf '%s\n' "fake final message" > "$output_file"
printf '%s\n' changed > "${FAKE_TESTBED}/change.txt"
printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3}}'
SH
chmod +x "${fake_bundle}/bin/codex"

FAKE_CAPTURE_CONFIG="${TEST_ROOT}/rendered-config.toml" \
FAKE_TESTBED="$fake_testbed" \
SWE_AGENT_BUNDLE="$fake_bundle" \
SWE_TESTBED_DIR="$fake_testbed" \
SWE_OUTPUT_ROOT="$fake_outputs" \
SWE_AGENT_NAME="codex" \
SWE_CODEX_MODEL="custom-model" \
SWE_CODEX_BASE_URL="http://custom.invalid/v1" \
SWE_CODEX_CONTEXT_WINDOW="8192" \
SWE_CODEX_AUTO_COMPACT_TOKEN_LIMIT="7000" \
    bash "${REPO_ROOT}/agents/codex/entrypoint.sh" \
        example__repo-3 example/repo "$base_commit" "Fix the example" >/dev/null

python3 - "$fake_outputs" "${TEST_ROOT}/rendered-config.toml" <<'PY'
import json
import os
import sys

output_root, config_path = sys.argv[1:]
instance_dir = os.path.join(output_root, "example__repo-3")
meta = json.load(open(os.path.join(instance_dir, "meta.json")))
result = json.load(open(os.path.join(instance_dir, "result.json")))
config = open(config_path).read()
patch = open(os.path.join(instance_dir, "patch.diff")).read()
assert meta["model"] == "custom-model"
assert meta["base_url"] == "http://custom.invalid/v1"
assert result["status"] == "patch_collected"
assert result["usage"] == {"input_tokens": 10, "output_tokens": 3}
assert 'model = "custom-model"' in config
assert 'base_url = "http://custom.invalid/v1"' in config
assert "model_context_window = 8192" in config
assert "model_auto_compact_token_limit = 7000" in config
assert "__SWE_CODEX_" not in config
assert "change.txt" in patch
PY
echo "ok 9 - Codex entrypoint renders config and records a patch"
