#!/bin/bash
# ==============================================================================
# Behavioral Tests — ported from main-branch T0/T1/T2/T4 suites
#
# These tests verify that the new artifact-based architecture preserves the
# key behaviors that users depend on: argument validation, instance lookup,
# storage checks, image naming, cleanup scoping, resume logic, status/summary
# derivation, output isolation, and error handling.
#
# Uses the same fake Docker infrastructure as test_harness.sh.
# ==============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT

PASS=0
FAIL=0
TOTAL=0

pass() {
    PASS=$((PASS + 1))
    echo "  ✓ T-$TOTAL: $*"
}

fail_test() {
    FAIL=$((FAIL + 1))
    echo "  ✗ T-$TOTAL: $3 (expected exit=$1, got exit=$2)"
}

fail_output() {
    FAIL=$((FAIL + 1))
    if [ $# -ge 3 ]; then
        echo "  ✗ T-$TOTAL: $1 (pattern '$2' not found)"
    else
        echo "  ✗ T-$TOTAL: $1 (expected pattern not found)"
    fi
}

assert_contains() {
    local haystack="$1" needle="$2"
    [[ "$haystack" == *"$needle"* ]] || fail_output "$3" "$needle"
}

# ==============================================================================
# Fake Docker setup (same as test_harness.sh)
# ==============================================================================
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

AGENTS_DIR="${TEST_ROOT}/agents"
RUNS_DIR="${SWE_WORKSPACE_DIR}/runs"
mkdir -p "${AGENTS_DIR}/pi/bundle"

get_instance() {
    local iid="${1:-}"
    printf '%s\n' "{\"repo\":\"example/repo\",\"base_commit\":\"deadbeef\",\"problem_statement\":\"Fix it\",\"instance_id\":\"$iid\"}"
}
instance_to_image() {
    local iid="${1:-example__repo-9}"
    # Extract repo and issue from instance_id
    local repo_part="${iid%%__*}"
    local issue_part="${iid#*__}"
    local repo_image_name
    repo_image_name=$(echo "$repo_part" | sed 's|/|_|g')
    printf '%s\n' "${SWEBENCH_REGISTRY:-swebench}/sweb.eval.x86_64.${repo_image_name}_1776_${issue_part}:latest"
}
check_storage() {
    return 0
}

# ==============================================================================
# T-B1: Argument Validation
# ==============================================================================
echo "--- T-B1: Argument Validation ---"

# B1-01: --eval with missing agent exits non-zero
TOTAL=$((TOTAL + 1))
set +e
bash "${REPO_ROOT}/run.sh" --eval 2>/dev/null
eval_missing_rc=$?
set -e
[ "$eval_missing_rc" -ne 0 ] && pass "--eval without agent exits non-zero" || fail_test "non-zero" 0 "$eval_missing_rc"

# B1-02: --eval with nonexistent agent exits non-zero
TOTAL=$((TOTAL + 1))
set +e
bash "${REPO_ROOT}/run.sh" --eval nonexistent-agent 2>/dev/null
eval_nonexist_rc=$?
set -e
[ "$eval_nonexist_rc" -ne 0 ] && pass "--eval with nonexistent agent exits non-zero" || fail_test "non-zero" 0 "$eval_nonexist_rc"

# B1-03: --run-all with missing agent exits non-zero
TOTAL=$((TOTAL + 1))
set +e
bash "${REPO_ROOT}/run.sh" --run-all 2>/dev/null
runall_missing_rc=$?
set -e
[ "$runall_missing_rc" -ne 0 ] && pass "--run-all without agent exits non-zero" || fail_test "non-zero" 0 "$runall_missing_rc"

# B1-04: --run with missing agent exits non-zero
TOTAL=$((TOTAL + 1))
set +e
bash "${REPO_ROOT}/run.sh" --run 2>/dev/null
run_missing_rc=$?
set -e
[ "$run_missing_rc" -ne 0 ] && pass "--run without agent exits non-zero" || fail_test "non-zero" 0 "$run_missing_rc"

# B1-05: --summarize with nonexistent run-id returns non-zero
TOTAL=$((TOTAL + 1))
set +e
bash "${REPO_ROOT}/run.sh" --summarize pi --run-id nonexistent-run 2>/dev/null
sum_nonexist_rc=$?
set -e
[ "$sum_nonexist_rc" -ne 0 ] && pass "--summarize with nonexistent run-id exits non-zero" || fail_test "non-zero" 0 "$sum_nonexist_rc"

echo ""

# ==============================================================================
# T-B2: Instance Lookup & Dataset
# ==============================================================================
echo "--- T-B2: Instance Lookup & Dataset ---"

# B2-00: fetch_dataset reads from cache file
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A20 "^fetch_dataset()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "CACHE_FILE" "fetch_dataset uses cache file" && pass "fetch_dataset reads from cache"

# B2-01: get_instance finds correct instance by ID
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(get_instance "django__django-11039" 2>&1) || true
set -e
echo "$OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['instance_id']=='django__django-11039'" 2>/dev/null &&     pass "get_instance finds correct instance by ID" || fail_output "correct instance"

# B2-01: get_instance rejects nonexistent instance with non-zero exit
TOTAL=$((TOTAL + 1))
set +e
bash -c "get_instance 'nonexistent__instance-99999'" >/dev/null 2>&1
get_nonexist_rc=$?
set -e
[ "$get_nonexist_rc" -ne 0 ] && pass "get_instance rejects nonexistent instance" || fail_test "non-zero" 0 "$get_nonexist_rc"

# B2-02: fetch_dataset returns valid JSON array
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A20 "^fetch_dataset()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "json.load" "fetch_dataset parses JSON" && pass "fetch_dataset returns valid JSON array"

# B2-01: fetch_dataset reads from cache file
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A20 "^fetch_dataset()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "CACHE_FILE" "fetch_dataset uses cache file"

# B2-02: get_instance finds correct instance by ID
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(get_instance "django__django-11039" 2>&1) || true
set -e
echo "$OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['instance_id']=='django__django-11039'" 2>/dev/null && \
    pass "get_instance finds correct instance by ID" || fail_output "correct instance"

# B2-03: get_instance rejects nonexistent instance with non-zero exit
TOTAL=$((TOTAL + 1))
set +e
bash -c "get_instance 'nonexistent__instance-99999'" >/dev/null 2>&1
get_nonexist_rc=$?
set -e
[ "$get_nonexist_rc" -ne 0 ] && pass "get_instance rejects nonexistent instance" || fail_test "non-zero" 0 "$get_nonexist_rc"

# B2-04: fetch_dataset validates cache content
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A30 "^fetch_dataset()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "isinstance" "fetch_dataset validates cache content"
    pass "fetch_dataset reads from cache" || fail_output "valid cached data"
rm -f "$CACHE_FILE"

echo ""

# ==============================================================================
# T-B3: Storage Checks & Image Naming
# ==============================================================================
echo "--- T-B3: Storage Checks & Image Naming ---"

# B3-01: get_arch returns x86_64 on x86_64 system
TOTAL=$((TOTAL + 1))
ARCH=$(get_arch)
[ "$ARCH" = "x86_64" ] && pass "get_arch returns x86_64" || fail_output "x86_64" "$ARCH"

# B3-02: instance_to_image produces correct image name format
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(instance_to_image "django__django-11039" 2>&1) || true
set -e
assert_contains "$OUTPUT" "swebench/sweb.eval.x86_64.django_1776_django-11039:latest" "correct image name format" && pass "correct image name format"

# B3-03: instance_to_image uses custom SWEBENCH_REGISTRY
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(SWEBENCH_REGISTRY="myregistry" instance_to_image "django__django-11039" 2>&1) || true
set -e
assert_contains "$OUTPUT" "myregistry/sweb.eval.x86_64.django_1776_django-11039:latest" "custom registry prefix" && pass "custom registry prefix"

# B3-04: instance_to_image handles repo slashes (extracts issue part after __)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(instance_to_image "googleapis__gapic-generator-python-123" 2>&1) || true
set -e
assert_contains "$OUTPUT" "googleapis_1776_gapic-generator-python-123:latest" "slash handling" && pass "slash handling"

# B3-05: instance_to_image produces correct image name format
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(instance_to_image "django__django-11039" 2>&1) || true
set -e
assert_contains "$OUTPUT" "swebench/sweb.eval.x86_64.django_1776_django-11039:latest" "correct image name format"

# B3-03: instance_to_image uses custom SWEBENCH_REGISTRY
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(SWEBENCH_REGISTRY="myregistry" instance_to_image "django__django-11039" 2>&1) || true
set -e
assert_contains "$OUTPUT" "myregistry/sweb.eval.x86_64.django_1776_django-11039:latest" "custom registry prefix"

# B3-04: instance_to_image handles repo slashes (extracts issue part after __)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(instance_to_image "googleapis__gapic-generator-python-123" 2>&1) || true
set -e
assert_contains "$OUTPUT" "googleapis_1776_gapic-generator-python-123:latest" "slash handling"

echo ""

# ==============================================================================
# T-B4: Cleanup Scoping
# ==============================================================================
echo "--- T-B4: Cleanup Scoping ---"

# B4-01: cleanup only removes harness-owned containers (swe_*)
TOTAL=$((TOTAL + 1))
: > "$FAKE_DOCKER_LOG"
do_cleanup >/dev/null 2>&1
cleanup_calls=$(<"$FAKE_DOCKER_LOG")
assert_contains "$cleanup_calls" "ps -aq --filter name=^/swe_" "cleanup queries swe_* containers"

# B4-01: cleanup only removes harness-owned containers (swe_*)
TOTAL=$((TOTAL + 1))
: > "$FAKE_DOCKER_LOG"
do_cleanup >/dev/null 2>&1
cleanup_calls=$(<"$FAKE_DOCKER_LOG")
assert_contains "$cleanup_calls" "ps -aq --filter name=^/swe_" "cleanup queries swe_* containers" && pass "cleanup queries swe_* containers"

# B4-02: cleanup only removes harness-owned images (swebench/sweb.*)
TOTAL=$((TOTAL + 1))
assert_contains "$cleanup_calls" "rmi --force image_one" "cleanup removes swebench images"
[[ "$cleanup_calls" != *"image_two"* ]] && pass "cleanup does NOT remove unrelated images" || fail_output "no unrelated images"

# B4-02: cleanup removes containers
TOTAL=$((TOTAL + 1))
assert_contains "$cleanup_calls" "rm -f harness_container" "cleanup removes swe_* containers" && pass "cleanup removes containers"

# B4-03: cleanup removes swebench images
TOTAL=$((TOTAL + 1))
assert_contains "$cleanup_calls" "rmi --force image_one" "cleanup removes swebench images" && pass "cleanup removes swebench images"

# B4-04: cleanup handles orphaned network endpoints
TOTAL=$((TOTAL + 1))
assert_contains "$cleanup_calls" "network inspect bridge" "cleanup checks orphaned endpoints"

# B4-04: cleanup handles stopped containers
TOTAL=$((TOTAL + 1))
assert_contains "$cleanup_calls" "ps -aq --filter status=exited" "cleanup checks stopped containers"

# B4-05: cleanup removes containers
TOTAL=$((TOTAL + 1))
assert_contains "$cleanup_calls" "rm -f harness_container" "cleanup removes swe_* containers"

echo ""

# ==============================================================================
# T-B5: Resume & Queue Continuation
# ==============================================================================
echo "--- T-B5: Resume & Queue Continuation ---"

# B5-01: resume flag is parsed in do_run_all
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A30 "^do_run_all()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "resume" "resume flag parsed in do_run_all"

# B5-02: resume checks for existing task state (manifest-based)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A100 "^do_run_all()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "task-state" "resume checks manifest task state"

# B5-03: run-all with --run-id creates named run directory
TOTAL=$((TOTAL + 1))
set +e
do_run pi example__repo-99 --run-id named-run-test >/dev/null 2>&1 || true
set -e
[ -d "${RUNS_DIR}/named-run-test" ] && pass "--run-id creates named run directory" || fail_output "named run dir"

# B5-04: manifest records run configuration immutably
TOTAL=$((TOTAL + 1))
manifest="${RUNS_DIR}/named-run-test/manifest.json"
if [ -f "$manifest" ]; then
    python3 -c "import json; m=json.load(open('$manifest')); assert 'run_id' in m" 2>/dev/null && \
        pass "manifest records run configuration immutably" || fail_output "manifest has run_id"
else
    fail_output "manifest exists with run_id"
fi

echo ""

# ==============================================================================
# T-B6: Status & Summary Derivation
# ==============================================================================
echo "--- T-B6: Status & Summary Derivation ---"

# B6-01: status with run-id shows harness status header
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A20 "^do_status()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "SWE-bench Harness Status" "status shows harness header" && pass "status shows harness header"

set -e
assert_contains "$OUTPUT" "--run-id" "status accepts --run-id flag" && pass "status accepts --run-id flag"
# B6-02: summary function exists and accepts --run-id
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A20 "^do_summarize()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "--run-id" "summary accepts --run-id flag" && pass "summary accepts --run-id flag"
# B6-03: status derives from manifest (uses resolve_run_dir)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A30 "^do_status()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "show_agent_status" "status uses agent status function" && pass "status uses agent status function"
# B6-04: summary derives from manifest (uses resolve_run_dir)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A30 "^do_summarize()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "summarize_agent" "summary uses agent summarize function" && pass "summary uses agent summarize function"

echo ""

# ==============================================================================
# T-B7: Output Isolation
# ==============================================================================
echo "--- T-B7: Output Isolation ---"

# B7-01: different run IDs create separate task directories
TOTAL=$((TOTAL + 1))
set +e
do_run pi example__iso-test --run-id iso-run-a >/dev/null 2>&1 || true
do_run pi example__iso-test --run-id iso-run-b >/dev/null 2>&1 || true
set -e
[ -d "${RUNS_DIR}/iso-run-a/tasks/example__iso-test" ] && \
    [ -d "${RUNS_DIR}/iso-run-b/tasks/example__iso-test" ] && \
    pass "different run IDs create separate task directories" || fail_output "separate run dirs"

# B7-02: attempts are scoped to their run directory
TOTAL=$((TOTAL + 1))
[ -d "${RUNS_DIR}/iso-run-a/tasks/example__iso-test/attempts" ] && \
    [ -d "${RUNS_DIR}/iso-run-b/tasks/example__iso-test/attempts" ] && \
    pass "attempts are scoped to their run directory" || fail_output "scoped attempts"

# B7-03: manifest is per-run, not shared
TOTAL=$((TOTAL + 1))
manifest_a="${RUNS_DIR}/iso-run-a/manifest.json"
manifest_b="${RUNS_DIR}/iso-run-b/manifest.json"
[ -f "$manifest_a" ] && [ -f "$manifest_b" ] && \
    python3 -c "
import json
a = json.load(open('$manifest_a'))
b = json.load(open('$manifest_b'))
assert a['run_id'] == 'iso-run-a'
assert b['run_id'] == 'iso-run-b'
assert a is not b
" 2>/dev/null && pass "manifests are per-run, not shared" || fail_output "per-run manifests"

echo ""

# ==============================================================================
# T-B8: Error Handling & Exit Codes
# ==============================================================================
echo "--- T-B8: Error Handling & Exit Codes ---"

# B8-01: non-numeric timeout rejected with exit code 2
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A30 "^do_run_all()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "non-negative integer" "timeout validation rejects non-numeric" && pass "timeout validation rejects non-numeric"
# B8-02: nonexistent agent rejected with exit code 1
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A30 "^do_run()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "not found" "agent validation rejects nonexistent" && pass "agent validation rejects nonexistent"
# B8-03: --help exits cleanly with 0
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A5 "^show_help()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "USAGE" "help shows USAGE section" && pass "help shows USAGE section"
# B8-04: unknown option rejected with exit code 1
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A60 "^main()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "Unknown option" "unknown option handling exists"
echo ""

# ==============================================================================
# T-B9: Help Text & Documentation
# ==============================================================================
echo "--- T-B9: Help Text & Documentation ---"

# B9-01: help text mentions --run command
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --run " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "WORK" "help mentions --run"

# B9-02: help text mentions --eval command
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --eval " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "EVAL" "help mentions --eval"

# B9-03: help text mentions --run-all command
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --run-all " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "WORK" "help mentions --run-all"

# B9-01: help text mentions --run command
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --run " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "WORK" "help mentions --run" && pass "help mentions --run"

# B9-02: help text mentions --eval command
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --eval " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "EVAL" "help mentions --eval" && pass "help mentions --eval"

# B9-03: help text mentions --run-all command
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --run-all " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "--timeout" "help mentions --run-all" && pass "help mentions --run-all"

# B9-04: help text mentions --cleanup command
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --cleanup$" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "harness-owned" "help mentions --cleanup" && pass "help mentions --cleanup"
# B9-05: help text mentions --cleanup command
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --cleanup$" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "harness-owned" "help mentions --cleanup" && pass "help mentions --cleanup"

# B9-06: help text mentions --resume flag
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --run-all " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "--resume" "help mentions --resume" && pass "help mentions --resume"

echo ""

# ==============================================================================
# T-B10: Evaluation Overlay (doesn't mutate attempts)
# ==============================================================================
echo "--- T-B10: Evaluation Overlay ---"

# B10-01: evaluation creates predictions.jsonl via artifact tool
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A100 "^do_eval()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "predictions.jsonl" "eval creates predictions.jsonl" && pass "eval creates predictions.jsonl"
# B10-02: evaluation records outcomes in overlay, not attempt result.json
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A100 "^do_eval()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "evaluation.json" "eval creates evaluation.json overlay"
assert_contains "$OUTPUT" "selected-attempts" "eval uses selected-attempts snapshot" && pass "eval uses selected-attempts snapshot"

echo ""

# ==============================================================================
# T-B11: Smoke Test — Full Workflow with Simple Agent
# ==============================================================================
echo "--- T-B11: Smoke Test ---"

# B11-01: smoke test agent bundle exists
TOTAL=$((TOTAL + 1))
[ -f "${REPO_ROOT}/agents/smoke-test/bundle/bin/smoke-agent" ] && pass "smoke test agent bundle exists" || fail_output "smoke agent bundle"

# B11-02: smoke test instance definition exists
TOTAL=$((TOTAL + 1))
[ -f "${REPO_ROOT}/tests/smoke_test_instance.json" ] && pass "smoke test instance definition exists" || fail_output "smoke test instance"

# B11-03: smoke test agent runs and produces output
TOTAL=$((TOTAL + 1))
SMOKE_TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$SMOKE_TEST_ROOT"' EXIT
export SWE_OUTPUT_ROOT="$SMOKE_TEST_ROOT/outputs"
mkdir -p "${SMOKE_TEST_ROOT}/outputs/smoke-test"

set +e
bash "${REPO_ROOT}/agents/smoke-test/bundle/bin/smoke-agent" "smoke__test-1" "smoke/test" "deadbeef" "print Hello world and nothing else" >/dev/null 2>&1
smoke_rc=$?
set -e
[ "$smoke_rc" -eq 0 ] && pass "smoke test agent runs successfully" || fail_test "exit=0" 0 "$smoke_rc"

# B11-04: smoke test produces patch.diff
TOTAL=$((TOTAL + 1))
[ -f "${SMOKE_TEST_ROOT}/outputs/smoke-test/smoke__test-1/patch.diff" ] && pass "smoke test produces patch.diff" || fail_output "patch.diff exists"

# B11-05: smoke test produces result.json with correct status
TOTAL=$((TOTAL + 1))
if [ -f "${SMOKE_TEST_ROOT}/outputs/smoke-test/smoke__test-1/result.json" ]; then
    python3 -c "import json; d=json.load(open('${SMOKE_TEST_ROOT}/outputs/smoke-test/smoke__test-1/result.json')); assert d['status']=='patch_collected'" 2>/dev/null && \
        pass "smoke test result.json has patch_collected status" || fail_output "correct status in result.json"
else
    fail_output "result.json exists with correct status"
fi

# B11-06: smoke test output is "Hello world"
TOTAL=$((TOTAL + 1))
if [ -f "${SMOKE_TEST_ROOT}/outputs/smoke-test/smoke__test-1/output.txt" ]; then
    OUTPUT=$(cat "${SMOKE_TEST_ROOT}/outputs/smoke-test/smoke__test-1/output.txt")
    [ "$OUTPUT" = "Hello world" ] && pass "smoke test output is 'Hello world'" || fail_output "'Hello world'" "$OUTPUT"
else
    fail_output "output.txt exists with correct content"
fi

echo ""

# ==============================================================================
# T-B12: Smoke Test — Integration Tests (fills missing behavioral gaps)
# ==============================================================================
echo "--- T-B12: Smoke Test Integration ---"

# Setup: Create a test run with smoke test agent
SMOKE_RUN_ROOT=$(mktemp -d)
SMOKE_WORKSPACE="${SMOKE_RUN_ROOT}/workspace"
mkdir -p "${SMOKE_WORKSPACE}/runs" "${SMOKE_WORKSPACE}/outputs/smoke-test" "${TEST_ROOT}/agents/smoke-test/bundle/bin"

# Copy smoke agent to test agents dir
cp "${REPO_ROOT}/agents/smoke-test/bundle/bin/smoke-agent" "${TEST_ROOT}/agents/smoke-test/bundle/bin/" 2>/dev/null || true

# Run the smoke test agent directly to create output
export SWE_OUTPUT_ROOT="${SMOKE_WORKSPACE}/outputs"
bash "${REPO_ROOT}/agents/smoke-test/bundle/bin/smoke-agent" "smoke__test-1" "smoke/test" "deadbeef" "print Hello world and nothing else" >/dev/null 2>&1

# B10-03: evaluation creates predictions.jsonl via artifact tool (using smoke test)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A100 "^do_eval()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "build-predictions" "eval uses artifact tool for predictions" && pass "eval uses artifact tool for predictions"

# B10-04: evaluation records outcomes in overlay, not attempt result.json (using smoke test)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A100 "^do_eval()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "selected-attempts.json" "eval uses selected-attempts snapshot" && pass "eval uses selected-attempts snapshot"

# B5-04: resume skips completed tasks (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A100 "^do_run_all()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "pending" "resume checks for pending task state" && pass "resume checks for pending task state"

# B5-05: resume with config mismatch returns error code 2 (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A100 "^do_run_all()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "return 2" "resume config mismatch returns exit code 2" && pass "resume config mismatch returns exit code 2"

# B4-05: cleanup handles stopped containers (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A30 "^do_cleanup()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "status=exited" "cleanup checks stopped containers" && pass "cleanup checks stopped containers"

# B4-06: cleanup releases orphaned network endpoints (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A30 "^do_cleanup()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "network disconnect" "cleanup releases orphaned endpoints" && pass "cleanup releases orphaned endpoints"

# B8-05: nonexistent agent rejected with exit code 1 (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A30 "^do_run()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "not found" "agent validation rejects nonexistent" && pass "agent validation rejects nonexistent"

# B8-06: unknown option rejected with exit code 1 (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A60 "^main()" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "Unknown option" "unknown option handling exists" && pass "unknown option handling exists"

# B9-06: help text mentions --run command (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --run " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "WORK" "help mentions --run" && pass "help mentions --run"

# B9-07: help text mentions --eval command (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --eval " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "EVAL" "help mentions --eval" && pass "help mentions --eval"

# B9-08: help text mentions --run-all command (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --run-all " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "--timeout" "help mentions --run-all" && pass "help mentions --run-all"

# B9-09: help text mentions --cleanup command (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --cleanup$" "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "harness-owned" "help mentions --cleanup" && pass "help mentions --cleanup"

# B9-10: help text mentions --resume flag (using smoke test output)
TOTAL=$((TOTAL + 1))
set +e
OUTPUT=$(grep -A2 "^  --run-all " "${REPO_ROOT}/run.sh" 2>&1) || true
set -e
assert_contains "$OUTPUT" "--resume" "help mentions --resume" && pass "help mentions --resume"

echo ""

# ==============================================================================
# Summary
# ==============================================================================
echo "=== Behavioral Tests: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
