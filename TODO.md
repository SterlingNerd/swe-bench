# SWE-bench Multi-Agent Harness — Implementation Plan

> Historical implementation record. The current architecture and remaining
> work are tracked in `BENCHMARK_REVAMP_PLAN.md`. In particular, the legacy
> flat-output and `docker cp` design below was superseded by manifest-owned,
> attempt-scoped bind mounts in the 2026-07-19 P0 implementation.

## Active roadmap checklist — reconciled 2026-07-19

This is the durable execution checklist for the current branch. The complete
rationale, operational evidence, acceptance details, and research references
are in `BENCHMARK_REVAMP_PLAN.md`. The earlier implementation round was
mistakenly called “Phase 1,” but its delivered scope was roadmap P0. P1 is not
complete: queue continuation exists, while the SQLite supervisor, scoped
leases, status-selective retry, structured heartbeats, budgets, and circuit
breakers do not.

### P0 — Safety and artifact correctness ✅

- [x] Preserve manifest-owned immutable attempts and stable explicit selection.
- [x] Verify byte length and SHA-256 before evaluation.
- [x] Store evaluation as an immutable overlay without mutating attempts.
- [x] Checkpoint on timeout, TERM, and INT from both agent entrypoints.
- [x] Capture Docker state and retain stopped containers when artifacts are
  incomplete.
- [x] Limit cleanup to exact manifest-owned paths and default partial cleanup to
  dry-run.
- [x] Preserve the five Python artifact tests and thirteen shell lifecycle
  tests as the regression floor.

### P1 — Durable orchestration and bounded image lifecycle

P1 is the next implementation phase and remains incomplete until every P1F
gate passes.

Current code facts this phase must remove:

- the global `/tmp/swe-bench-run.lock` `flock`;
- hard-coded `SWEBENCH_REGISTRY="swebench"`;
- a storage preflight that checks only repository-filesystem `df` usage;
- per-image `docker save`/`docker load` tar archives and the false help claim
  that loading them keeps images off local disk;
- model-attempt allocation before dataset/image/storage preparation;
- evaluator `--cache_level instance` without `--clean True`; and
- manifests without exact image ref/digest/platform/registry provenance.

#### P1A — SQLite supervisor and state reconciliation

- [ ] Add a migrated `state.sqlite` as the transactional scheduling authority;
  retain manifests as durable audit exports.
- [ ] Implement `planned -> preparing -> running -> checkpointing -> collected
  -> terminal/retryable`.
- [ ] Reconcile existing manifest-backed runs without changing finalized
  attempts.
- [ ] Keep the default worker count at one.

#### P1B — Leases, recovery, and retry semantics

- [ ] Replace global `flock` with owner/expiry leases scoped to runs and
  attempts.
- [ ] Reclaim expired `preparing`/`running` work after Docker, WSL, or
  supervisor failure.
- [ ] Keep queue continuation, infrastructure retry, agent retry, and Codex
  session continuation separate and auditable.
- [ ] Finish storage/image preparation before allocating a model attempt, so
  an image pull, rate limit, registry error, or ENOSPC does not consume pass@1
  or become a model DNF.
- [ ] Add status-selective infrastructure retry; never automatically retry a
  baseline agent failure.

#### P1C — Structured observability

- [ ] Emit typed events and periodic heartbeats.
- [ ] Record last model event, tool event, diff change, provider usage, Docker
  resources, and elapsed budgets.
- [ ] Record exact official image ref/digest/platform,
  `TestSpec.env_image_key`, registry source, and cache policy.
- [ ] Snapshot Docker usage before pull, after pull, after evaluation, and
  after eviction.
- [ ] Derive status from SQLite rather than artifact scanning.

#### P1D — Deterministic image and NAS-registry controller

Operational requirements from the latest run and screenshots:

- a roughly 250-task run filled local disk;
- about 150 GB of Docker images accumulated on a 250 GB drive;
- Django images were reported near 500 MB and some Matplotlib images near 3 GB
  compressed, with larger local unpacked usage;
- Docker Hub rate limiting blocked more pulls; and
- an agent changed the correctly identified `swebench/` prefix to the invalid
  `swebbench/` spelling while grepping.

The Matplotlib 25311 canary measured about 3.31 GB of registry content, 11.41 GB
of local Docker disk usage, and roughly 7 GB of visible filesystem content; its
targeted `test_complete` evaluation passed. Adjacent images varied materially,
so scheduling cannot use one repository-family size assumption.

Therefore image control is deterministic host-side infrastructure, never an
LLM shell task and never direct manipulation of Docker/containerd files.

- [ ] Require Docker 29.6+ before using automated image-size decisions.
- [ ] Reconcile active Docker context, image-store backend,
  `/var/lib/containerd`, all images, and daemon/filesystem storage.
- [ ] Use Docker APIs/structured JSON and exact filters, including
  `docker image ls --all --filter "reference=swebench/sweb.*" --format json`;
  reject `swebbench/...`.
- [ ] Resolve the official SWE-bench image reference and immutable digest.
- [ ] Retire per-image `docker save`/`docker load` archives because they
  duplicate shared parents and still need local unpacked storage.
- [ ] Keep registry credentials host-side and out of task containers.
- [ ] Add a writable NAS OCI registry for digest-pinned images. Keep any
  pull-through proxy separate because proxy mode cannot accept pushes and does
  not eliminate Docker Hub fair-use limits.
- [ ] Enforce the initial 250 GB drive policy: use SWE-bench's documented “at
  least 120 GB free” prerequisite as the scheduling floor, retain at most about
  100 GB of Docker images, reserve next-image expansion plus evaluation
  scratch, allow at most three concurrent/cached Matplotlib instance images,
  and maintain a separate small quarantine budget.

Subphases:

- [ ] **P1D-1:** add the configurable digest-pinned NAS registry, credentials,
  reference resolution, digest-pinned pull, and structured inventory.
- [ ] **P1D-2:** add single-flight seeding on a NAS miss with a seeding lease,
  bounded wait, and no retry stampede.
- [ ] **P1D-3:** verify the destination digest and persist the official-to-NAS
  source mapping before scheduling.

NAS miss flow:

1. Check the NAS for the expected digest.
2. Acquire one seeding lease.
3. Resolve the official Docker Hub digest.
4. Copy registry-to-registry with a tool such as Skopeo rather than unpacking
   into local Docker.
5. Verify the destination digest.
6. Pull the NAS reference locally by digest.

#### P1E — Cohort execution, eviction, and circuit breakers

- [ ] Group by official `TestSpec.env_image_key`, not repository string.
- [ ] Implement the exact lifecycle:

  `resolve digest -> reserve local space -> check/seed NAS -> pull from NAS by
  digest -> solve -> checkpoint/finalize -> evaluate -> persist immutable
  overlay -> remove container -> release image lease -> remove exact local
  image -> remeasure storage`

- [ ] Evaluate before eviction. Do not delete the image merely because the
  solve container stopped.
- [ ] Use evaluator `--cache_level env --clean True` where applicable or
  explicit exact-digest cleanup after the immutable evaluation overlay.
  `cache_level=env` alone is insufficient because solve-pulled images predate
  evaluator startup and `clean=False` preserves them.
- [ ] Never invoke `docker system prune -a` from the runner.
- [ ] Add global circuit breakers and bounded backoff for storage
  reservation/ENOSPC, Hub 429, auth failure, registry outage, digest mismatch,
  and Docker daemon loss.
- [ ] Inspect evaluator `error_ids` and per-instance logs even when the
  evaluator exits zero.
- [ ] Record infrastructure statuses as
  `image_pull_rate_limited`, `image_pull_authentication_error`,
  `image_not_found`, `registry_unavailable`, `image_digest_mismatch`,
  `image_storage_exhausted`, `docker_daemon_unavailable`, or
  `evaluation_harness_error`; none counts as model DNF.
- [ ] Remove only the exact ref/digest, without force, after no live or retained
  container and no worker/evaluator lease needs it. Preserve unrelated images
  and referenced shared layers.
- [ ] Defer eviction to the bounded quarantine budget while an incomplete
  stopped container or another worker/evaluator still holds a lease.

Subphases:

- [ ] **P1E-1:** make one image lease span solve and evaluation until durable
  evaluation artifacts exist.
- [ ] **P1E-2:** add exact post-evaluation eviction, storage remeasurement, and
  tests proving shared layers and unrelated images survive.
- [ ] **P1E-3:** add bounded quarantine and explicit storage-pressure
  exceptions.

#### P1F — Completion gate

- [ ] Recover from a crash at every state transition.
- [ ] Pass two-supervisor contention and lease-expiry recovery tests.
- [ ] Continue a queue without rerunning completed work.
- [ ] Retry only eligible infrastructure failures.
- [ ] Pass simulated Hub 429, ENOSPC, registry outage, and digest mismatch.
- [ ] Accept `swebench/sweb...` and reject `swebbench/...`.
- [ ] Prove exact eviction, shared-layer preservation, no unrelated removal,
  and quarantine-cap enforcement.
- [ ] Run the real
  `swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-25311` canary pinned to
  digest
  `sha256:767d72ed9ee6c6c85fc54ba39457207c64da5ba6fc56d74580ed419fab0e1d2a`.
- [ ] Stay within the storage budget across multiple image cohorts.
- [ ] Prove SQLite, scoped leases, heartbeats, budgets, and circuit breakers
  exist and the global lock is gone.

### P2 — Provider profiles and credential isolation

- [ ] **P2A:** add named local, baseline Codex, and guarded profiles plus
  provider/model capability preflight; keep the pinned stable CLI until an
  intentional benchmark-version change.
- [ ] **P2B:** add a host/sidecar Responses proxy and isolated Docker network.
- [ ] **P2C:** issue run-scoped tokens and enforce request, token, time, and
  spend quotas.
- [ ] **P2D:** prove task processes cannot access upstream credentials or
  bypass the metered proxy.

### P3 — Loop analysis and guarded experiments

- [ ] **P3A:** normalize trajectories into typed events and label all twelve
  partial-run DNFs.
- [ ] **P3B:** run the loop/no-progress detector in shadow mode only.
- [ ] **P3C:** use the correct-`swebench/` then wrong-`swebbench/` grep as a
  contradiction fixture; keep infrastructure deterministic outside the agent.
- [ ] **P3D:** audit false positives and thresholds before allowing one bounded,
  explicitly named recovery.
- [ ] **P3E:** run a paired frozen baseline-versus-guarded pilot.

### P4 — Bounded throughput

- [ ] **P4A:** add separate bounded solve/evaluation pools, initially one
  worker each.
- [ ] **P4B:** schedule with CPU, memory, provider, and projected image-expansion
  resource tokens pinned in the manifest.
- [ ] **P4C:** scale through attended 5-task batches, an 8–12 smoke set, a fixed
  50-task pilot, then a 100-task confirmation.
- [ ] **P4D:** attempt all 500 only after storage, retry, cost, isolation,
  reproducibility, and full-reporting gates pass.

---

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
