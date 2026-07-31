# TODO: SWE-bench Orchestrator Refactor

## Current State (run.sh — 1273 lines, 30+ functions)

### Architecture
- Self-contained agent bundles mounted into swebench eval images
- Two phases: [WORK] → [EVAL]
- Flat output: `<workspace>/outputs/<agent>/<instance_id>/`
- Docker images pulled per instance, cached via tarballs (broken — overlayfs produces 10KB fake files)

### Functions
| Function | Purpose |
|----------|---------|
| `show_help()` | Print usage |
| `do_index()` | Fetch/cache dataset from HuggingFace |
| `do_list()` | List cached instances with optional filter |
| `do_build()` / `build_agent_bundle()` | Build agent bundles |
| `do_rebuild()` | Force rebuild (--no-cache) |
| `check_storage()` | Check disk usage vs MAX_STORAGE_PCT (default 80%) |
| `require_docker()` / `ensure_docker()` | Docker availability check with caching |
| `record_host_result()` | Write result.json with status, exit code, elapsed |
| `release_container()` | Remove container + release bridge endpoint |
| `do_cleanup()` | Remove all swe_* containers and swebench images |
| `do_cleanup_partial()` | Remove incomplete output dirs (DRY RUN only) |
| `save_image_to_cache()` / `load_image_from_cache()` | Tarball cache (broken) |
| `fetch_dataset()` | Cache validation + HuggingFace fetch |
| `get_instance()` | Find single instance by ID from cache |
| `instance_to_image()` | Convert instance_id to swebench image name |
| `do_run()` | Run agent against single instance |
| `do_run_all()` | Loop over all instances |
| `do_eval()` | Evaluate patches via swebench harness |
| `summarize_agent()` / `do_summarize()` | Combine results into summary.json |
| `show_agent_status()` / `do_status()` | Show completion status with colored output |
| `do_init()` | Install swebench Python package in venv |
| `do_interactive()` | Drop into interactive shell in eval image |

### Bugs Found & Fixed (from test coverage)
1. **EXIT trap grep returning 1** → `--help` exits 1
   - Fix: Use `mapfile` + `|| true` in `stop_running_containers()`
2. **`--run-all --timeout abc` loops through all instances** instead of rejecting
   - Fix: Add numeric validation in `do_run_all()` before entering loop

### Test Coverage (157 tests, all passing)
```
tests/
├── test_runner.sh          # Unified runner: ./tests/test_runner.sh [T0|T1|T1b|T2|T2b|T2c|T2d|T3|T3b|T4|all]
├── test_helper.sh          # Shared helpers (mock cache, mock agent, assertions)
├── t0_pure_shell.sh        # 34 tests — arg parsing, config, storage, image naming, cache helpers
├── t1_filesystem.sh        # 21 tests — dataset cache, index/list, bundle build/rebuild, cleanup-partial
├── t1b_instance_lookup.sh  # 15 tests — instance lookup, dataset validation, --list output
├── t2_docker.sh            # 8 tests — Docker-dependent: cleanup, init, summarize, status, eval
├── t2_docker_mocked.sh     # 10 tests — do_run() logic paths via fake docker
├── t2b_signal_handling.sh  # 11 tests — signal handling, traps, stop_running_containers
├── t2c_run_all_mocked.sh   # 8 tests — do_run_all() argument parsing, agent validation
├── t2d_docker_mock_edge_cases.sh  # 10 tests — Docker mock edge cases, integration
├── t3_e2e.sh               # 2 tests — end-to-end workflows
├── t3b_interactive_and_misc.sh  # 16 tests — interactive mode, misc edge cases
├── t4_eval_and_integration.sh     # 12 tests — eval, predictions.jsonl, report folding
└── fixtures/
    ├── docker              # PATH-swappable fake docker (5 modes: success, timeout, error, cp_fail, oom)
    └── mock-entrypoint.sh  # Minimal test agent that writes expected artifacts
```

---

## Problems to Fix

### From PR #1 Audit (Shared Harness)
1. `--cleanup-partial` iterates one level too high — can delete entire agent output tree
2. Cleanup traps target ALL `swe_*` containers — second process can stop first's container
3. EXIT cleanup returns 1 when no matching container exists — breaks `./run.sh --help` (FIXED)
4. Timeout/error paths remove containers before copy/finalization completes
5. Reruns can reuse stale artifacts
6. `release_container` removes before network disconnect (Docker docs say disconnect is container op)
7. Eval reuses fixed agent name as `run_id` — collision when patches change

### From Operational Experience
1. Docker Hub rate limiting (100 pulls/6hrs free tier)
2. No space management — images accumulate until disk fills
3. Eval runs once at end, after all work phases — no opportunity to reclaim space mid-run
4. Images deleted/loaded in arbitrary order, causing maximum re-downloads
5. Local storage won't fit entire suite (500GB+ needed)

---

## Refactor Plan: Re-arrange How We Run Problems

### Goal
Move from flat output + batch eval to manifest-based runs with attempt isolation, per-instance eval, and active space management.

### Architecture Target

```
<workspace>/runs/<RUN_ID>/
├── manifest.json          # Run metadata, dataset hash, timeout, profile
├── tasks/
│   ├── <instance_id>/
│   │   ├── attempt-001/   # Immutable attempt directory
│   │   │   ├── result.json
│   │   │   ├── patch.diff
│   │   │   ├── agent_output.txt
│   │   │   └── meta.json
│   │   └── attempt-002/   # Rerun creates new attempt
│   │       └── ...
│   └── ...
└── eval/
    └── <RUN_ID>/          # Evaluation reports per run
        ├── report.json
        └── <instance_id>/
            └── result.json
```

### Key Design Principles
1. **Manifest-owned attempts** — each run creates immutable attempt directories
2. **Per-instance eval** — evaluate immediately after agent run, not batched at end
3. **Active space management** — prune images after eval, never let disk exceed 90%
4. **Smart ordering** — process instances grouped by shared base layers
5. **Attempt isolation** — reruns create new attempts, never overwrite previous ones

### What We Replaced SQLite With
Original plan had P1A: SQLite supervisor for state machines, leases, crash recovery.
Replaced with: **Python CLI tool `scripts/run_artifacts.py`** that handles:
- Manifest creation/resolution (JSON files on disk)
- Attempt isolation via immutable directories
- Scoped cleanup via `--agent` and `--run-id` flags
- Event export for audit logging

Tradeoff: Less robust crash recovery, but simpler to implement and test.

---

## Implementation Phases

### Phase 1: Manifest Infrastructure (P1) — START HERE

**What to build:**
- `scripts/run_artifacts.py` — Python tool for manifest management
  - `create-run` — create run manifest with provenance
  - `resolve-run` — resolve latest or named run for an agent
  - `cleanup-partial` — list/remove incomplete attempts (dry-run or --apply)
  - `export-events` — export event audit log

**Code changes in run.sh:**
- Add `RUNS_DIR` variable (`<workspace>/runs`)
- Replace flat `OUTPUT_DIR` with manifest-based structure
- Add `--run-id ID` flag to `--run`, `--run-all`, `--eval`, `--summarize`, `--status`
- Add `--profile NAME` flag for agent-specific configurations

**Testing needed:**
- Manifest creation, resolution, cleanup-partial dry-run/apply

### Phase 2: Per-Instance Eval (P2)

**What to build:**
- `do_eval_instance(agent, instance_id)` — evaluate single instance immediately after agent run
- Fold harness results into attempt's result.json
- Prune swebench image after eval completes

**Code changes in run.sh:**
- Modify `do_run()` to call `do_eval_instance()` after work phase
- Add `prune_image()` function for exact image removal
- Add `ensure_space_for()` function with 90% hard limit

**Testing needed:**
- Per-instance eval, image pruning, space management

### Phase 3: Smart Ordering & Space Management (P3)

**What to build:**
- `get_ordered_instances()` — sort by repo → version → instance_id
- Periodic GC every N instances in `do_run_all()`
- Emergency GC when disk approaches 90%

**Code changes in run.sh:**
- Modify `do_run_all()` to use ordered instance list
- Add periodic GC trigger (every 20 instances)
- Add emergency GC when `check_storage()` returns warning

**Testing needed:**
- Smart ordering, periodic GC, emergency GC

### Phase 4: Registry Integration (P4)

**What to build:**
- Pull-through registry at `docker-registry.sterling.digital`
- NAS storage for cached layers
- Registry mirror configuration in `/etc/docker/daemon.json`

**Code changes in run.sh:**
- Add `--init-registry` command with registry detection and storage estimation
- Replace tarball cache with registry mirror (remove `save_image_to_cache`, `load_image_from_cache`)
- Update `do_run()` to use registry mirror for pulls

**Testing needed:**
- Registry detection, storage estimation, pull through registry, NAS caching

### Phase 5: Cleanup & Hardening (P5)

**What to fix:**
1. Fix `--cleanup-partial` scope — add `--agent` requirement, never traverse above agent directory
2. Scope cleanup traps to active container only — use `ACTIVE_CONTAINER` variable
3. Fix EXIT trap return code — don't let cleanup failures propagate (FIXED)
4. Preserve containers during timeout/error paths — copy artifacts before removing
5. Add `--run-id` to eval — prevent collision when same agent produces different patches

**Testing needed:**
- Cleanup scope, trap safety, artifact preservation

---

## Priority Order

| Phase | Name | Effort | Value | Blocks |
|-------|------|--------|-------|--------|
| **P1** | Manifest infrastructure | Medium | High | P2, P3, P4, P5 |
| **P2** | Per-instance eval | Medium | High | P3, P5 |
| **P3** | Smart ordering & space mgmt | Low | High | P4 |
| **P4** | Registry integration | High | High | None |
| **P5** | Cleanup & hardening | Low | High | None |

**Start with P1 — everything else depends on manifest infrastructure.**

---

## Quick Reference: Current vs Target

| Aspect | Current | Target |
|--------|---------|--------|
| Output structure | Flat: `outputs/<agent>/<iid>/` | Manifest: `runs/<run_id>/tasks/<iid>/attempt-NNN/` |
| Eval timing | Batch at end | Per-instance after agent run |
| Image lifecycle | Accumulate until disk full | Prune after eval, 90% hard limit |
| Reruns | Overwrite previous results | New attempt directory |
| Run provenance | None | Manifest with commit hash, dataset SHA, timeout, profile |
| Cleanup scope | All `swe_*` containers | Active container only, scoped to run |

---

## Next Immediate Steps

1. **Create `scripts/run_artifacts.py`** — manifest CRUD operations
2. **Add `RUNS_DIR` and `--run-id` flag** to run.sh
3. **Update `do_run()`** to create attempt directories under manifest
4. **Write tests** for manifest creation, resolution, cleanup-partial

Run tests before/after each change:
```bash
./tests/test_runner.sh          # All tests
./tests/test_runner.sh T0       # Pure shell (no Docker)
./tests/test_runner.sh --verbose # Verbose on failures
```
