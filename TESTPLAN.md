# Test Plan: SWE-bench Orchestrator (`run.sh`)

## Overview

This plan covers test coverage for all 30+ functions in `run.sh` (1273 lines). Tests are organized by dependency level so they can be run incrementally — no Docker required for the foundational layer.

---

## Test Categories

| Category | Scope | Docker Required? |
|----------|-------|------------------|
| **T0** | Pure shell logic, argument parsing, config, helpers | ❌ No |
| **T1** | Filesystem operations, dataset cache, bundle building | ❌ No (Docker for `--build` only) |
| **T2** | Docker-dependent functions (run, cleanup, eval) | ✅ Yes |
| **T3** | End-to-end workflow (full run → eval → summarize) | ✅ Yes |

---

## T0 — Pure Shell Logic (No Docker)

### 0.1 Argument Parsing & Help

| # | Test | Verify |
|---|------|--------|
| T0-01 | `./run.sh` with no args | Prints help, exits 0 |
| T0-02 | `./run.sh --help` | Prints help, exits 0 |
| T0-03 | `./run.sh -h` | Prints help, exits 0 |
| T0-04 | `./run.sh --unknown-flag` | Prints error + help, exits 1 |
| T0-05 | `./run.sh --run` with missing args | Prints usage error, exits non-zero |
| T0-06 | `./run.sh --run-all` with missing agent | Prints usage error, exits non-zero |
| T0-07 | `./run.sh --eval` with missing agent | Prints usage error, exits non-zero |
| T0-08 | `./run.sh --interactive` with missing args | Prints usage error, exits non-zero |
| T0-09 | `./run.sh --run-all pi --timeout abc` | Rejects non-numeric timeout, exits non-zero |
| T0-10 | `./run.sh --run-all pi --resume --timeout 3600` | Parses multiple flags correctly |

### 0.2 Configuration & Environment

| # | Test | Verify |
|---|------|--------|
| T0-11 | Default `MAX_STORAGE_PCT=80` | Config variable set to 80 |
| T0-12 | `MAX_STORAGE_PCT=90 ./run.sh --help` | Env var overrides default |
| T0-13 | `SWE_WORKSPACE_DIR=/tmp/test ./run.sh --help` | Workspace dir override works |
| T0-14 | `SWEBENCH_IMAGE_CACHE=/tmp/cache ./run.sh --help` | Cache env var set without error |
| T0-15 | `HF_DATASET` defaults to `princeton-nlp/SWE-bench_Verified` | Correct default value |
| T0-16 | `CACHE_FILE` defaults to `/tmp/swe_verified_cache.json` | Correct default path |

### 0.3 Storage Check (`check_storage`)

| # | Test | Verify |
|---|------|--------|
| T0-17 | Disk at 50% with `MAX_STORAGE_PCT=80` | Returns 0 (OK) |
| T0-18 | Disk at 50% with `MAX_STORAGE_PCT=40` | Returns 1 (warning) |
| T0-19 | Disk at 50% with `MAX_STORAGE_PCT=50` | Returns 1 (at threshold) |
| T0-20 | Disk at 50% with `MAX_STORAGE_PCT=60` | Returns 0 (below threshold) |
| T0-21 | `df` unavailable (mocked) | Defaults to 0, returns 0 |

### 0.4 Docker Readiness (`require_docker`, `ensure_docker`)

| # | Test | Verify |
|---|------|--------|
| T0-22 | `docker info` succeeds | `ensure_docker` returns 0, sets `DOCKER_READY=1` |
| T0-23 | `docker info` fails (mocked) | `require_docker` prints error to stderr, returns 1 |
| T0-24 | Call `ensure_docker` twice | Second call returns immediately (cached) |

### 0.5 Instance-to-Image Mapping (`instance_to_image`, `get_arch`)

| # | Test | Verify |
|---|------|--------|
| T0-25 | `get_arch` on x86_64 | Returns `x86_64` |
| T0-26 | `instance_to_image django__django-11039` | Returns `swebench/sweb.eval.x86_64.django_1776_django-11039:latest` |
| T0-27 | `instance_to_image princeton-nlp/SWE-bench_Verified__issue-42` | Converts slashes to underscores: `sweb.eval.x86_64.princeton-nlp_SWE-bench_Verified_1776_issue-42:latest` |
| T0-28 | `instance_to_image` with `SWEBENCH_REGISTRY=custom` | Uses custom registry prefix |

### 0.6 Record Host Result (`record_host_result`)

| # | Test | Verify |
|---|------|--------|
| T0-29 | Write result to new file | Creates file with status, container_exit_code, elapsed_seconds, patch_bytes=0 |
| T0-30 | Write result to existing file | Merges into existing JSON without losing other fields |
| T0-31 | Write result with invalid existing JSON | Overwrites corrupted file cleanly |

### 0.7 Release Container (`release_container`)

| # | Test | Verify |
|---|------|--------|
| T0-32 | `release_container` on non-existent container | Returns 0 (no error, `|| true`) |
| T0-33 | `release_container` on running container | Stops and removes it |

### 0.8 Image Cache Helpers (`save_image_to_cache`, `load_image_from_cache`)

| # | Test | Verify |
|---|------|--------|
| T0-34 | `save_image_to_cache` with no `SWEBENCH_IMAGE_CACHE` set | Returns 0 immediately (no-op) |
| T0-35 | `load_image_from_cache` with no `SWEBENCH_IMAGE_CACHE` set | Returns 1 immediately |
| T0-36 | `save_image_to_cache` with cache set, tar already exists | Skips save (no duplicate) |
| T0-37 | `load_image_from_cache` with cache set, tar missing | Returns 1 |
| T0-38 | `save_image_to_cache` with slashes/colons in image name | Sanitizes to safe filename |

---

## T1 — Filesystem & Dataset Operations (No Docker for core logic)

### 1.1 Dataset Cache (`fetch_dataset`)

| # | Test | Verify |
|---|------|--------|
| T1-01 | `CACHE_FILE` doesn't exist | Triggers fetch |
| T1-02 | `CACHE_FILE` is empty | Triggers re-fetch |
| T1-03 | `CACHE_FILE` contains invalid JSON | Triggers re-fetch |
| T1-04 | `CACHE_FILE` contains valid non-list JSON | Triggers re-fetch |
| T1-05 | `CACHE_FILE` contains valid list with 0 elements | Triggers re-fetch |
| T1-06 | `CACHE_FILE` contains valid list with data | Returns cached content, no fetch |
| T1-07 | Fetch fails (Docker/network error) | Prints error, removes cache file, returns 1 |

### 1.2 Get Instance (`get_instance`)

| # | Test | Verify |
|---|------|--------|
| T1-08 | Valid instance_id exists in cache | Returns JSON with repo, base_commit, problem_statement |
| T1-09 | Invalid instance_id not in cache | Prints error to stderr, exits 1 |

### 1.3 Index & List (`do_index`, `do_list`)

| # | Test | Verify |
|---|------|--------|
| T1-10 | `--index` with no cache | Creates cache file, prints count |
| T1-11 | `--index` with existing cache | Reuses cache, prints count |
| T1-12 | `--list` with no filter | Prints all instances sorted by repo/version |
| T1-13 | `--list django` | Filters to only django instances |
| T1-14 | `--list NONEXISTENT` | Prints 0 matching instances |

### 1.4 Build Agent Bundle (`do_build`, `build_agent_bundle`)

| # | Test | Verify |
|---|------|--------|
| T1-15 | `--build pi` with existing bundle | Skips (bundle already exists) |
| T1-16 | `--build nonexistent` | Prints error listing available agents, exits 1 |
| T1-17 | `--build` with no args (all agents) | Builds all non-base agents |
| T1-18 | Agent without `build_bundle.sh` | Prints warning, skips |
| T1-19 | `--rebuild pi` | Forces rebuild even if bundle exists |
| T1-20 | `--rebuild nonexistent` | Prints error listing available agents, exits 1 |

### 1.5 Cleanup Partial (`do_cleanup_partial`)

| # | Test | Verify |
|---|------|--------|
| T1-21 | No outputs directory exists | Prints "No outputs directory found", returns 0 |
| T1-22 | Instance with both `result.json` and `patch.diff` | Kept (not removed) |
| T1-23 | Instance missing `result.json` | Removed |
| T1-24 | Instance missing `patch.diff` | Removed |
| T1-25 | Instance with empty `patch.diff` (0 bytes) | **BUG**: Currently kept — should be removed |
| T1-26 | Instance with non-empty `patch.diff` but no `result.json` | Removed |

---

## T2 — Docker-Dependent Functions

### 2.1 Cleanup (`do_cleanup`)

| # | Test | Verify |
|---|------|--------|
| T2-01 | No swe_* containers or images | Prints "No SWE-bench Docker resources found" |
| T2-02 | Running swe_* container exists | Removes it, prints count |
| T2-03 | Orphaned bridge endpoints exist | Releases them, prints count |
| T2-04 | swebench/sweb.* images exist | Removes them, prints count |
| T2-05 | Docker unavailable | Prints error, returns 1 |

### 2.2 Run Single Instance (`do_run`)

| # | Test | Verify |
|---|------|--------|
| T2-06 | Invalid agent name | Prints error with available agents, exits 1 |
| T2-07 | Valid agent but no bundle | Prints error suggesting `--build`, exits 1 |
| T2-08 | Invalid instance_id | Fails at `get_instance`, exits non-zero |
| T2-09 | Storage check fails (disk full) | Prints warning, returns 1 without running |
| T2-10 | Image not in cache, pull succeeds | Pulls image, runs container |
| T2-11 | Image in cache, load succeeds | Loads from cache, skips pull |
| T2-12 | Container exits 0 | Copies outputs, returns 0 |
| T2-13 | Container exits non-zero (agent error) | Records `container_error`, removes container, returns non-zero |
| T2-14 | Timeout (exit 124) | Records `timed_out`, removes container, returns 124 |
| T2-15 | `docker cp` succeeds but no output files | Prints warning, returns 1 |
| T2-16 | `docker cp` fails, container dead | Leaves container for inspection, returns 1 |
| T2-17 | `docker cp` fails, container running | Leaves container for inspection, returns 1 |
| T2-18 | Output copy succeeds, result.json shows `agent_error` | Returns 1 (failure status) |
| T2-19 | Output copy succeeds, result.json shows `no_patch` | Returns 0 (not a failure status) |
| T2-20 | Output copy succeeds, result.json shows `resolved` | Returns 0 |
| T2-21 | Ownership fix after copy | Files owned by current user |

### 2.3 Run All Instances (`do_run_all`)

| # | Test | Verify |
|---|------|--------|
| T2-22 | Invalid agent name | Prints error, exits 1 |
| T2-23 | Dataset fetch fails | Prints error, cleans up temp file, exits 1 |
| T2-24 | `--resume` with all instances complete | Skips all, prints "0 run, N skipped, 0 failed" |
| T2-25 | `--resume` with some incomplete | Runs only incomplete ones |
| T2-26 | One instance fails in the loop | Increments failed count, continues to next |
| T2-27 | Storage check fails mid-run | Breaks out of loop, prints summary |
| T2-28 | Waits for running container (up to 3600s) | Polls and waits |
| T2-29 | Timeout waiting for container (3600s exceeded) | Kills all swe containers, breaks |

### 2.4 Evaluation (`do_eval`)

| # | Test | Verify |
|---|------|--------|
| T2-30 | Invalid agent name | Prints error with available agents, exits 1 |
| T2-31 | swebench not installed (no venv) | Prints error suggesting `--init`, exits 1 |
| T2-32 | No outputs directory for agent | Prints "No outputs found", returns |
| T2-33 | Outputs exist but no non-empty patches | Prints "No patches found", returns |
| T2-34 | Patches exist, predictions.jsonl created | Writes correct JSONL format |
| T2-35 | Harness report folding — resolved instance | Sets `local_eval=resolved`, `status=resolved` |
| T2-36 | Harness report folding — failed instance | Sets `local_eval=failed`, `status=failed` |
| T2-37 | Harness report folding — errored instance | Sets `local_eval=error`, `status=error` |
| T2-38 | Harness report folding — instance not in report | Skipped (no fold) |
| T2-39 | Multiple report file naming conventions | Tries `{agent}.{agent}.json`, `{agent}__{agent}.json`, newest *.json |

### 2.5 Summarize (`summarize_agent`, `do_summarize`)

| # | Test | Verify |
|---|------|--------|
| T2-40 | No outputs for agent | Prints "No outputs found", returns |
| T2-41 | Agent with instances, all have result.json | Writes summary.json, prints table |
| T2-42 | Instance missing result.json | Skipped in summary |
| T2-43 | Corrupted result.json | Skipped (JSON parse error caught) |
| T2-44 | `--summarize` with no agent (all agents) | Summarizes each agent directory |
| T2-45 | Summary counts: resolved, failed, errored, no_patch, timed_out, agent_errors | All counts correct |

### 2.6 Status (`show_agent_status`, `do_status`)

| # | Test | Verify |
|---|------|--------|
| T2-46 | No outputs directory | Prints "No outputs found" |
| T2-47 | Agent with no instances | Prints "No outputs found" |
| T2-48 | Instance with result.json showing `resolved` | Shows green ✓ |
| T2-49 | Instance with result.json showing `failed` | Shows red ✗ |
| T2-50 | Instance with result.json showing `no_patch` | Shows yellow — |
| T2-51 | Instance with result.json showing `timed_out` | Shows ⌛ |
| T2-52 | Instance with result.json showing `agent_error` | Shows red ! |
| T2-53 | Instance with no result.json | Shows ? (no result) |
| T2-54 | `eval/` and `logs/` directories skipped | Not counted in totals |
| T2-55 | `--status` with no agent (all agents) | Shows status for each agent |

### 2.7 Init (`do_init`)

| # | Test | Verify |
|---|------|--------|
| T2-56 | swebench already installed | Prints version, returns 0 |
| T2-57 | swebench not installed | Creates venv, installs package, prints version |

### 2.8 Interactive (`do_interactive`)

| # | Test | Verify |
|---|------|--------|
| T2-58 | Invalid agent name | Prints error, exits 1 |
| T2-59 | No bundle for agent | Prints error suggesting `--build`, exits 1 |
| T2-60 | Docker unavailable | Prints error, returns 1 |
| T2-61 | Image not present | Pulls image before starting shell |
| T2-62 | Interactive flag passed to entrypoint | Container runs with `--interactive` |

---

## T3 — End-to-End Workflows

### 3.1 Full Workflow: Build → Run → Eval → Summarize

| # | Test | Verify |
|---|------|--------|
| T3-01 | `--build pi` → `--run pi django__django-11039` → `--eval pi` → `--summarize pi` | Complete pipeline succeeds, summary.json has correct counts |
| T3-02 | Same pipeline with `--run-all pi --timeout 300` (small subset) | All instances processed, summary reflects results |

### 3.2 Resume Workflow

| # | Test | Verify |
|---|------|--------|
| T3-03 | `--run-all pi --resume` after partial run | Skips completed instances, runs remaining |
| T3-04 | `--run-all pi --resume` after full completion | Skips all, prints "0 run, N skipped" |

### 3.3 Cleanup Workflow

| # | Test | Verify |
|---|------|--------|
| T3-05 | `--cleanup-partial` after failed runs | Removes incomplete directories only |
| T3-06 | `--cleanup` after runs | Removes all swe_* containers and images |

### 3.4 Error Recovery

| # | Test | Verify |
|---|------|--------|
| T3-07 | Interrupt (^C) during `do_run` | Container stopped, outputs copied if possible |
| T3-08 | Interrupt during `do_run_all` | Stops current container, summary printed |
| T3-09 | Disk full during run | Aborts with warning, no partial state corruption |

---

## Test Infrastructure

### File: `tests/test_runner.sh`

```bash
#!/bin/bash
# Lightweight test runner for run.sh tests
# Usage: ./tests/test_runner.sh [category]
#   category: T0, T1, T2, T3, or all (default)

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0 FAIL=0 TOTAL=0

run_test() {
    local name="$1"
    local cmd="$2"
    local expected_exit="${3:-0}"
    TOTAL=$((TOTAL + 1))
    
    set +e
    eval "$cmd" > /tmp/test_${TOTAL}_out 2>&1
    local actual_exit=$?
    set -e
    
    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  ✓ T$(printf '%02d' $TOTAL): $name"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T$(printf '%02d' $TOTAL): $name (expected exit=$expected_exit, got $actual_exit)"
        cat /tmp/test_${TOTAL}_out
        FAIL=$((FAIL + 1))
    fi
}

run_test_output() {
    local name="$1"
    local cmd="$2"
    local expected_pattern="$3"
    TOTAL=$((TOTAL + 1))
    
    set +e
    eval "$cmd" > /tmp/test_${TOTAL}_out 2>&1
    local actual_exit=$?
    set -e
    
    if grep -q "$expected_pattern" /tmp/test_${TOTAL}_out; then
        echo "  ✓ T$(printf '%02d' $TOTAL): $name"
        PASS=$((PASS + 1))
    else
        echo "  ✗ T$(printf '%02d' $TOTAL): $name (pattern '$expected_pattern' not found)"
        cat /tmp/test_${TOTAL}_out
        FAIL=$((FAIL + 1))
    fi
}

echo "=== SWE-bench run.sh Test Suite ==="
echo ""

CATEGORY="${1:-all}"

case "$CATEGORY" in
    T0) echo "--- T0: Pure Shell Logic ---" ;;
    T1) echo "--- T1: Filesystem & Dataset ---" ;;
    T2) echo "--- T2: Docker-Dependent ---" ;;
    T3) echo "--- T3: End-to-End ---" ;;
    all) echo "Running all test categories..." ;;
esac

# Tests go here...

echo ""
echo "=== Results: ${PASS}/${TOTAL} passed, ${FAIL} failed ==="
[ "$FAIL" -eq 0 ]
```

### File: `tests/t0_pure_shell.sh`

Tests for T0 category (no Docker needed).

### File: `tests/t1_filesystem.sh`

Tests for T1 category (Docker only for bundle building).

### File: `tests/t2_docker.sh`

Tests for T2 category (requires Docker daemon).

### File: `tests/t3_e2e.sh`

Tests for T3 category (full workflow, requires Docker + dataset).

---

## Implementation Priority

| Phase | Tests | Effort | Value |
|-------|-------|--------|-------|
| **Phase 1** | T0-01 through T0-38 | Small | High — catches bugs before Docker dependency |
| **Phase 2** | T1-01 through T1-26 | Small | High — validates data integrity |
| **Phase 3** | T2-01 through T2-29 | Medium | Critical — core runtime behavior |
| **Phase 4** | T2-30 through T2-62 | Medium | Critical — eval, summarize, status |
| **Phase 5** | T3-01 through T3-09 | Large | High — validates full workflows |

---

## Notes & Known Issues to Test

1. **T1-25**: `do_cleanup_partial` keeps instances with empty (0-byte) `patch.diff` — this is likely a bug
2. **T2-16/17**: Container state detection (`dead|error` vs other states) needs verification
3. **T2-39**: Report file naming is fragile — tries multiple conventions with fallback
4. **T2-54**: `eval/` and `logs/` directories are skipped by name — could break if renamed
5. **Signal handling**: The EXIT trap calls `stop_running_containers` which kills ALL `swe_*` containers — this is the shared-harness bug from PR #1 audit

---

## Mocking Strategy

For tests that need Docker but shouldn't actually run containers:

| Function | Mock Approach |
|----------|--------------|
| `docker run` | Replace with script that creates output files and exits 0 |
| `docker pull` | No-op (image already exists or skip) |
| `docker rm` / `docker rmi` | No-op |
| `docker cp` | Copy from test fixture directory |
| `docker inspect` | Return mock JSON |
| `fetch_dataset` | Use pre-seeded cache file |

This allows T2 tests to verify logic paths without actual container execution.
