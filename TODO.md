# SWE-bench Multi-Agent Harness — Implementation Plan

## Overview

Transform the harness from a single-agent tool into a multi-agent platform with proper isolation, structured error handling, and narrowed cleanup scope.

---

## Phase 1 — Core Infrastructure (low risk) ✅

- [x] Add `require_docker` / `ensure_docker` helpers
- [x] Narrow `--cleanup` to harness-owned resources only (`swe_*` containers + `swebench/sweb.*` images)
- [x] Wrap `main` in a function with `if [[ "${BASH_SOURCE[0]}" == "$0" ]]` guard
- [x] Change `exit N` → `return N` throughout for composability
- [x] Update README: document narrowed cleanup, new docker preflight checks

## Phase 2 — Output Isolation (moderate risk, breaking change) ✅

Existing `outputs/` data will be incompatible — agents previously wrote to a flat `outputs/<instance_id>/` layout. Manual one-time move: `mv outputs/* outputs/<agent>/`.

### Container output strategy: `docker cp` instead of bind mount

Instead of `-v "${WORKSPACE_DIR}:/workspace:rw"`, the container writes to an internal path and we `docker cp` files out after exit. This eliminates all uid/gid permission issues — no `chmod` workarounds needed.

**Note:** The host-side `workspace/repos/` directory is an orphan. The entrypoint already clones repos into `/tmp/repos` (tmpfs, writable) inside the container. Since images are cached locally, the only per-run cost is cloning from git into tmpfs — fast and storage-neutral.

- [x] Remove `-v "${WORKSPACE_DIR}:/workspace:rw"` from docker run commands
- [x] In `do_run`: remove `--rm`, add post-run `docker cp` + manual cleanup (`docker stop` + `docker rm -f`)
- [x] Handle timeout path: after `timeout` kills the container, `docker stop`, then `docker cp`, then `docker rm`
- [x] Add logging to distinguish violent deaths (OOM kill, etc.) from legitimate early exits — check `docker inspect` for `State.FinishedAt` and `State.Error` to decide whether `docker cp` is worth attempting
- [x] Accept that if a container dies too violently for `docker cp`, we lose that output — but it's re-runnable
- [x] Agent bundle can still be a read-only bind mount (`/agent:ro`) — only output files need the copy-out treatment
- [x] Change output layout from `outputs/<instance_id>/` → `outputs/<agent>/<instance_id>/`
- [x] Update `do_run_all` — resolve agent-scoped paths for resume and result checks
- [x] Update `do_eval` — read/write only the selected agent's output directory
- [x] Update `summarize_agent` / `do_summarize` — per-agent summaries, auto-discover all agents when no filter given
- [x] Update `show_agent_status` / `do_status` — per-agent status display
- [x] Update `do_interactive` — take `<agent> <instance_id>` instead of just `<instance_id>`
- [x] Update `agents/pi/entrypoint.sh` — use `SWE_OUTPUT_ROOT` and `SWE_AGENT_NAME` env vars
- [x] Remove all `chmod -R a+rwX` from both entrypoints (no longer needed)
- [x] Update README: new architecture diagram, output contract, docker cp strategy, per-agent usage examples

## Phase 3 — Error Handling & Timeouts (moderate risk) ✅

- [x] Add timeout enforcement in `do_run` using `timeout --foreground --signal=TERM --kill-after=30s`
- [x] Add structured failure statuses: `timed_out`, `container_error`, `agent_error`
- [x] Add `record_host_result` function — merges host-side errors into existing `result.json`, preserving agent metadata (`agent_exit_code`, `elapsed_seconds`, etc.)
- [x] Improve result folding in `--eval` — merge into existing `result.json` instead of deleting and rewriting; preserve `patch_bytes`, `elapsed_seconds`, `agent_exit_code`
- [x] Update `agents/pi/entrypoint.sh` — structured failure reporting, binary-safe patch extraction (`git diff --binary`)
- [x] Update README: document timeout/resume workflow, new status values, error handling behavior

## Phase 4 — Error Handling Cleanup (low risk, high value) ✅

**Audit result:** All 21 `|| true` / `2>/dev/null` patterns are intentional. No critical errors are silently swallowed.

- [x] Audit all `|| true` and `2>/dev/null` in `run.sh` — keep only where truly intentional (e.g., cleanup of already-dead containers)
- [x] Replace swallowed errors with explicit logging + appropriate return codes — none needed, all have proper fallbacks
- [x] In entrypoints: remove `|| true` on critical operations (git diff, file writes), report failures clearly — all intentional best-effort
- [x] Consider a centralized error handler function (e.g., `die "message"` that logs and returns 1) — not needed, existing patterns are clear
- [x] Update README: document error statuses and what they mean

## Phase 5 — Polish ✅

- [x] Update `.gitignore` (`__pycache__/`, `*.py[cod]`)
- [x] Final review pass: verify all changes work together, no regressions

---

## Notes

- **Timeout kill-after:** 30s is generous — the container gets a TERM signal and has 30s to exit before SIGKILL. Adjust if needed.
- **`docker cp` on violent death:** If a container is OOM-killed or otherwise dies too hard, `docker cp` may fail. Acceptable trade-off — the work is re-runnable.
- **Codex adapter** (`agents/codex/`) is handled on the dedicated Codex runner branch.
