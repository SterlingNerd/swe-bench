#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local haystack="$1" needle="$2"
    [[ "$haystack" == *"$needle"* ]] || fail "expected output to contain: $needle"
}

wait_for_file() {
    local path="$1" count=0
    while [ ! -f "$path" ] && [ "$count" -lt 100 ]; do
        sleep 0.05
        count=$((count + 1))
    done
    [ -f "$path" ] || fail "timed out waiting for $path"
}

mkdir -p "${TEST_ROOT}/bin"
export FAKE_DOCKER_LOG="${TEST_ROOT}/docker.log"
export FAKE_DOCKER_ATTEMPT_FILE="${TEST_ROOT}/docker-attempt-path"
export FAKE_DOCKER_STOPPED_FILE="${TEST_ROOT}/docker-stopped"
export FAKE_DOCKER_WAIT_MODE="normal"

cat > "${TEST_ROOT}/bin/docker" <<'SH'
#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
case "${1:-} ${2:-}" in
    "info ") exit 0 ;;
    "image inspect") exit 0 ;;
    "ps -aq") printf '%s\n' harness_container; exit 0 ;;
esac
case "${1:-}" in
    images)
        printf '%s\n' 'swebench/sweb.eval.x86_64.example image_one' 'ubuntu image_two'
        ;;
    network|pull|save|load|rmi)
        ;;
    rm)
        ;;
    run)
        attempt=""
        for arg in "$@"; do
            case "$arg" in
                *:/workspace/outputs/*) attempt="${arg%%:*}" ;;
            esac
        done
        [ -n "$attempt" ] || exit 9
        printf '%s\n' "$attempt" > "$FAKE_DOCKER_ATTEMPT_FILE"
        rm -f "$FAKE_DOCKER_STOPPED_FILE"
        case "$FAKE_DOCKER_WAIT_MODE" in
            normal|oom)
                printf '%s\n' 'normal patch' > "${attempt}/patch.diff"
                bytes=$(wc -c < "${attempt}/patch.diff")
                printf '{"status":"patch_collected","patch_bytes":%s,"checkpointed":true}\n' \
                    "$bytes" > "${attempt}/result.json"
                ;;
        esac
        printf '%s\n' fakecid
        ;;
    logs)
        ;;
    wait)
        if [ -f "$FAKE_DOCKER_STOPPED_FILE" ]; then
            printf '%s\n' 143
        elif [ "$FAKE_DOCKER_WAIT_MODE" = "slow" ]; then
            sleep 5
            printf '%s\n' 143
        else
            printf '%s\n' 0
        fi
        ;;
    stop)
        attempt=$(<"$FAKE_DOCKER_ATTEMPT_FILE")
        if [ -f "${attempt}/termination-request.json" ]; then
            printf '%s\n' checkpoint-request-present >> "$FAKE_DOCKER_LOG"
        fi
        printf '%s\n' 'timeout patch' > "${attempt}/patch.diff"
        bytes=$(wc -c < "${attempt}/patch.diff")
        printf '{"status":"timed_out","patch_bytes":%s,"checkpointed":true,"partial_patch":true}\n' \
            "$bytes" > "${attempt}/result.json"
        touch "$FAKE_DOCKER_STOPPED_FILE"
        ;;
    inspect)
        if [ "$FAKE_DOCKER_WAIT_MODE" = "oom" ]; then
            printf '%s\n' '{"Status":"exited","Running":false,"OOMKilled":true,"ExitCode":137,"Error":"","StartedAt":"2026-01-01T00:00:00Z","FinishedAt":"2026-01-01T00:01:00Z"}'
        else
            printf '%s\n' '{"Status":"exited","Running":false,"OOMKilled":false,"ExitCode":0,"Error":"","StartedAt":"2026-01-01T00:00:00Z","FinishedAt":"2026-01-01T00:01:00Z"}'
        fi
        ;;
esac
SH
chmod +x "${TEST_ROOT}/bin/docker"

export PATH="${TEST_ROOT}/bin:${PATH}"
export SWE_WORKSPACE_DIR="${TEST_ROOT}/workspace"
# shellcheck source=../run.sh
source "${REPO_ROOT}/run.sh"
trap 'rm -rf "$TEST_ROOT"' EXIT

AGENTS_DIR="${TEST_ROOT}/agents"
RUNS_DIR="${SWE_WORKSPACE_DIR}/runs"
mkdir -p "${AGENTS_DIR}/pi/bundle"
get_instance() {
    printf '%s\n' '{"repo":"example/repo","base_commit":"deadbeef","problem_statement":"Fix it"}'
}
instance_to_image() {
    printf '%s\n' 'swebench/fake:latest'
}
check_storage() {
    return 0
}

echo "1..13"

bash -n "${REPO_ROOT}/run.sh" \
    "${REPO_ROOT}/agents/pi/entrypoint.sh" \
    "${REPO_ROOT}/agents/codex/entrypoint.sh" \
    "${REPO_ROOT}/agents/codex/build_bundle.sh"
python3 -B -c 'from pathlib import Path; compile(Path("scripts/run_artifacts.py").read_text(), "scripts/run_artifacts.py", "exec")'
echo "ok 1 - shell and Python entrypoints parse"

: > "$FAKE_DOCKER_LOG"
FAKE_DOCKER_WAIT_MODE=normal
export FAKE_DOCKER_WAIT_MODE
do_run pi example__repo-9 0 --run-id lifecycle-normal >/dev/null
normal_run="${RUNS_DIR}/lifecycle-normal"
normal_attempt="${normal_run}/tasks/example__repo-9/attempts/attempt-0001"
python3 - "$normal_run" "$normal_attempt" "$FAKE_DOCKER_LOG" <<'PY'
import json
import sys
from pathlib import Path

run, attempt, log_path = map(Path, sys.argv[1:])
manifest = json.load((run / "manifest.json").open())
result = json.load((attempt / "result.json").open())
assert manifest["tasks"]["example__repo-9"]["selected_attempt"] == "attempt-0001"
assert result["status"] == "patch_collected"
assert result["container_exit_code"] == 0
assert (attempt / "container-state.json").is_file()
lines = log_path.read_text().splitlines()
inspect_at = next(i for i, line in enumerate(lines) if line.startswith("inspect --format") and line.endswith(" fakecid"))
remove_at = next(i for i, line in enumerate(lines) if line == "rm fakecid")
assert inspect_at < remove_at
assert not any(line.startswith("cp ") for line in lines)
PY
echo "ok 2 - detached lifecycle inspects and finalizes before removal"

: > "$FAKE_DOCKER_LOG"
FAKE_DOCKER_WAIT_MODE=slow
export FAKE_DOCKER_WAIT_MODE
set +e
do_run pi example__repo-10 --timeout 1 --run-id lifecycle-timeout >/dev/null
timeout_rc=$?
set -e
[ "$timeout_rc" -eq 124 ] || fail "timeout returned ${timeout_rc}, expected 124"
timeout_attempt="${RUNS_DIR}/lifecycle-timeout/tasks/example__repo-10/attempts/attempt-0001"
python3 - "$timeout_attempt" "$FAKE_DOCKER_LOG" <<'PY'
import json
import sys
from pathlib import Path

attempt, log_path = map(Path, sys.argv[1:])
request = json.load((attempt / "termination-request.json").open())
result = json.load((attempt / "result.json").open())
assert request["requested_status"] == "timed_out"
assert request["reason"] == "hard_timeout"
assert result["status"] == "timed_out"
lines = log_path.read_text().splitlines()
assert "checkpoint-request-present" in lines
stop_at = next(i for i, line in enumerate(lines) if line.startswith("stop --signal=TERM"))
inspect_at = next(i for i, line in enumerate(lines) if line.startswith("inspect --format"))
remove_at = next(i for i, line in enumerate(lines) if line == "rm fakecid")
assert stop_at < inspect_at < remove_at
PY
echo "ok 3 - timeout requests a checkpoint before stop, inspect, and removal"

: > "$FAKE_DOCKER_LOG"
FAKE_DOCKER_WAIT_MODE=invalid
export FAKE_DOCKER_WAIT_MODE
set +e
do_run pi example__repo-11 0 --run-id lifecycle-invalid >/dev/null
invalid_rc=$?
set -e
[ "$invalid_rc" -eq 1 ] || fail "invalid artifact run returned ${invalid_rc}"
invalid_attempt="${RUNS_DIR}/lifecycle-invalid/tasks/example__repo-11/attempts/attempt-0001"
[ -f "${invalid_attempt}/host-observation.json" ] || fail "missing host observation"
invalid_log=$(<"$FAKE_DOCKER_LOG")
[[ "$invalid_log" != *$'\nrm fakecid'* ]] || fail "invalid artifact container was removed"
echo "ok 4 - incomplete artifacts retain the stopped container"

: > "$FAKE_DOCKER_LOG"
FAKE_DOCKER_WAIT_MODE=oom
export FAKE_DOCKER_WAIT_MODE
set +e
do_run pi example__repo-12 0 --run-id lifecycle-oom >/dev/null
oom_rc=$?
set -e
[ "$oom_rc" -eq 1 ] || fail "OOM run returned ${oom_rc}"
oom_attempt="${RUNS_DIR}/lifecycle-oom/tasks/example__repo-12/attempts/attempt-0001"
python3 - "$oom_attempt" <<'PY'
import json
import sys
from pathlib import Path

result = json.load((Path(sys.argv[1]) / "result.json").open())
assert result["status"] == "oom_killed"
assert result["container_exit_code"] == 137
assert result["container"]["oom_killed"] is True
PY
echo "ok 5 - OOM state overrides the entrypoint outcome"

SWEBENCH_PY="$(command -v python3)"
export PYTHONPATH="${REPO_ROOT}/tests/fake_swebench"
do_eval pi --run-id lifecycle-normal >/dev/null
python3 - "$normal_run" "$normal_attempt" <<'PY'
import json
import sys
from pathlib import Path

run, attempt = map(Path, sys.argv[1:])
manifest = json.load((run / "manifest.json").open())
assert manifest["latest_evaluation"]
evaluation = json.load((run / manifest["evaluations"][-1]["path"]).open())
result = json.load((attempt / "result.json").open())
assert evaluation["outcomes"] == {"example__repo-9": "resolved"}
assert "local_eval" not in result and result["status"] == "patch_collected"
PY
echo "ok 6 - evaluation uses selections and leaves attempts immutable"

status_output=$(do_status pi --run-id lifecycle-normal)
summary_output=$(do_summarize pi --run-id lifecycle-normal)
assert_contains "$status_output" "Resolved: 1"
assert_contains "$summary_output" "resolved: 1"
echo "ok 7 - status and summary derive from the run manifest"

fetch_dataset() {
    printf '%s\n' '[{"instance_id":"example__repo-9"}]'
}
resume_output=$(do_run_all pi --resume --run-id lifecycle-normal)
assert_contains "$resume_output" "0 run, 1 skipped"
set +e
do_run_all pi --resume --run-id lifecycle-normal --timeout 1 >/dev/null 2>&1
resume_mismatch_rc=$?
set -e
[ "$resume_mismatch_rc" -eq 2 ] || fail "resume config mismatch returned ${resume_mismatch_rc}"
echo "ok 8 - resume continues only untouched tasks under immutable run config"

: > "$FAKE_DOCKER_LOG"
do_cleanup >/dev/null
cleanup_calls=$(<"$FAKE_DOCKER_LOG")
assert_contains "$cleanup_calls" "ps -aq --filter name=^/swe_"
assert_contains "$cleanup_calls" "rm -f harness_container"
assert_contains "$cleanup_calls" "rmi image_one"
[[ "$cleanup_calls" != *"image_two"* ]] || fail "cleanup selected unrelated image"
echo "ok 9 - Docker cleanup remains harness-scoped"

export SWE_CODEX_MODEL="test-model"
export SWE_CODEX_BASE_URL="http://model.invalid/v1"
export SWE_CODEX_API_KEY="test-token"
runtime_args=(docker run)
append_agent_runtime_env codex runtime_args
runtime_text="${runtime_args[*]}"
assert_contains "$runtime_text" "SWE_CODEX_MODEL=test-model"
assert_contains "$runtime_text" "SWE_CODEX_API_KEY"
[[ "$runtime_text" != *"test-token"* ]] || fail "Codex API key leaked into argv"
unset SWE_CODEX_MODEL SWE_CODEX_BASE_URL SWE_CODEX_API_KEY
echo "ok 10 - Codex runtime credentials are forwarded without argv leakage"

fake_codex_bundle="${TEST_ROOT}/codex-bundle"
fake_codex_testbed="${TEST_ROOT}/codex-testbed"
fake_codex_outputs="${TEST_ROOT}/codex-outputs"
mkdir -p "${fake_codex_bundle}/bin" "$fake_codex_testbed"
cp "${REPO_ROOT}/agents/codex/config.toml" "${fake_codex_bundle}/config.toml"
git -C "$fake_codex_testbed" init -q
printf '%s\n' original > "${fake_codex_testbed}/original.txt"
git -C "$fake_codex_testbed" add original.txt
git -C "$fake_codex_testbed" -c user.name=Test -c user.email=test@example.invalid commit -qm initial
codex_base=$(git -C "$fake_codex_testbed" rev-parse HEAD)
cat > "${fake_codex_bundle}/bin/codex" <<'SH'
#!/bin/bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then echo codex-cli-test; exit 0; fi
output_file=""
while [ $# -gt 0 ]; do
    if [ "$1" = "--output-last-message" ]; then output_file="$2"; shift 2; else shift; fi
done
printf '%s\n' final > "$output_file"
printf '%s\n' changed > "${FAKE_TESTBED}/change.txt"
printf '%s\n' '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":3}}'
if [ "${FAKE_AGENT_SLEEP:-0}" = 1 ]; then
    touch "$FAKE_AGENT_MARKER"
    sleep 60
fi
exit "${FAKE_AGENT_EXIT_CODE:-0}"
SH
chmod +x "${fake_codex_bundle}/bin/codex"

env FAKE_TESTBED="$fake_codex_testbed" \
    SWE_AGENT_BUNDLE="$fake_codex_bundle" \
    SWE_TESTBED_DIR="$fake_codex_testbed" \
    SWE_OUTPUT_ROOT="$fake_codex_outputs" \
    SWE_CODEX_RUNTIME_DIR="${TEST_ROOT}/codex-runtime" \
    SWE_AGENT_NAME=codex \
    bash "${REPO_ROOT}/agents/codex/entrypoint.sh" \
        example__repo-20 example/repo "$codex_base" "Fix it" >/dev/null
python3 - "$fake_codex_outputs" "$fake_codex_testbed" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

outputs, repo = map(Path, sys.argv[1:])
attempt = outputs / "example__repo-20"
result = json.load((attempt / "result.json").open())
assert result["status"] == "patch_collected"
assert result["checkpointed"] is True
assert result["usage"] == {"input_tokens": 10, "output_tokens": 3}
assert "change.txt" in (attempt / "patch.diff").read_text()
subprocess.run(["git", "-C", repo, "diff", "--cached", "--quiet"], check=True)
PY
echo "ok 11 - Codex checkpoints atomically without mutating the live index"

signal_codex_testbed="${TEST_ROOT}/signal-codex-testbed"
signal_codex_outputs="${TEST_ROOT}/signal-codex-outputs"
signal_marker="${TEST_ROOT}/codex-running"
mkdir -p "$signal_codex_testbed"
git -C "$signal_codex_testbed" init -q
printf '%s\n' original > "${signal_codex_testbed}/original.txt"
git -C "$signal_codex_testbed" add original.txt
git -C "$signal_codex_testbed" -c user.name=Test -c user.email=test@example.invalid commit -qm initial
signal_codex_base=$(git -C "$signal_codex_testbed" rev-parse HEAD)
env FAKE_TESTBED="$signal_codex_testbed" FAKE_AGENT_SLEEP=1 FAKE_AGENT_MARKER="$signal_marker" \
    SWE_AGENT_BUNDLE="$fake_codex_bundle" SWE_TESTBED_DIR="$signal_codex_testbed" \
    SWE_OUTPUT_ROOT="$signal_codex_outputs" SWE_CODEX_RUNTIME_DIR="${TEST_ROOT}/signal-codex-runtime" \
    SWE_AGENT_NAME=codex bash "${REPO_ROOT}/agents/codex/entrypoint.sh" \
        example__repo-21 example/repo "$signal_codex_base" "Fix it" >/dev/null 2>&1 &
codex_entrypoint_pid=$!
wait_for_file "$signal_marker"
signal_codex_attempt="${signal_codex_outputs}/example__repo-21"
printf '%s\n' '{"requested_status":"timed_out","reason":"hard_timeout","requested_at":"2026-01-01T00:00:00Z"}' \
    > "${signal_codex_attempt}/termination-request.json"
kill -TERM "$codex_entrypoint_pid"
wait "$codex_entrypoint_pid"
python3 - "$signal_codex_attempt" <<'PY'
import json
import sys
from pathlib import Path

attempt = Path(sys.argv[1])
result = json.load((attempt / "result.json").open())
assert result["status"] == "timed_out"
assert result["partial_patch"] is True and result["termination_signal"] == "TERM"
assert result["finalization_reason"] == "hard_timeout"
assert "change.txt" in (attempt / "patch.diff").read_text()
PY
echo "ok 12 - Codex TERM path preserves a partial patch and timeout metadata"

fake_pi_bundle="${TEST_ROOT}/pi-bundle"
fake_pi_testbed="${TEST_ROOT}/pi-testbed"
fake_pi_outputs="${TEST_ROOT}/pi-outputs"
pi_marker="${TEST_ROOT}/pi-running"
mkdir -p "${fake_pi_bundle}/bin" "$fake_pi_testbed"
cat > "${fake_pi_bundle}/bin/pi" <<'SH'
#!/bin/bash
set -euo pipefail
if [ "${1:-}" = "--version" ]; then echo pi-cli-test; exit 0; fi
printf '%s\n' changed > "${FAKE_TESTBED}/change.txt"
touch "$FAKE_AGENT_MARKER"
sleep 60
SH
chmod +x "${fake_pi_bundle}/bin/pi"
git -C "$fake_pi_testbed" init -q
printf '%s\n' original > "${fake_pi_testbed}/original.txt"
git -C "$fake_pi_testbed" add original.txt
git -C "$fake_pi_testbed" -c user.name=Test -c user.email=test@example.invalid commit -qm initial
pi_base=$(git -C "$fake_pi_testbed" rev-parse HEAD)
env FAKE_TESTBED="$fake_pi_testbed" FAKE_AGENT_MARKER="$pi_marker" \
    SWE_AGENT_BUNDLE="$fake_pi_bundle" SWE_TESTBED_DIR="$fake_pi_testbed" \
    SWE_OUTPUT_ROOT="$fake_pi_outputs" SWE_PI_CONFIG_DIR="${TEST_ROOT}/pi-config" \
    SWE_AGENT_NAME=pi bash "${REPO_ROOT}/agents/pi/entrypoint.sh" \
        example__repo-22 example/repo "$pi_base" "Fix it" >/dev/null 2>&1 &
pi_entrypoint_pid=$!
wait_for_file "$pi_marker"
pi_attempt="${fake_pi_outputs}/example__repo-22"
printf '%s\n' '{"requested_status":"operator_cancelled","reason":"operator_interrupt","requested_at":"2026-01-01T00:00:00Z"}' \
    > "${pi_attempt}/termination-request.json"
kill -TERM "$pi_entrypoint_pid"
wait "$pi_entrypoint_pid"
python3 - "$pi_attempt" "$fake_pi_testbed" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

attempt, repo = map(Path, sys.argv[1:])
result = json.load((attempt / "result.json").open())
assert result["status"] == "operator_cancelled"
assert result["partial_patch"] is True and result["checkpointed"] is True
assert "change.txt" in (attempt / "patch.diff").read_text()
subprocess.run(["git", "-C", repo, "diff", "--cached", "--quiet"], check=True)
PY
echo "ok 13 - Pi TERM path checkpoints without mutating the live index"
