# TODO: SWE-bench Orchestrator — Python Rewrite

## Status: ✅ Foundation Complete — 140 Unit Tests Passing

The entire bash orchestrator (`run.sh`, 1306 lines) has been rewritten in Python with proper DI and testing. All core modules are implemented and tested.

### Completed (P0 — Foundation)

| Module | Lines | Tests | Status |
|--------|-------|-------|--------|
| `models.py` | Data models (pydantic) | 23 | ✅ |
| `config.py` | Configuration management | 10 | ✅ |
| `dataset.py` | Dataset cache & HuggingFace fetch | 19 | ✅ |
| `bundles.py` | Agent bundle discovery & building | 16 | ✅ |
| `storage.py` | Disk usage & Docker cleanup | 13 | ✅ |
| `docker_ops.py` | Container lifecycle management | 15 | ✅ |
| `manifest.py` | Run tracking & attempt isolation (P1) | 19 | ✅ |
| `runner.py` | Instance execution & summarization | 8 | ✅ |
| `cli.py` | Click-based CLI interface | 17 | ✅ |
| **Total** | **~5,500 lines** | **140 unit** | **All passing** |

### Completed (P1b — Integration Tests)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_runner_mocked.py` | 13 | Docker ops: success, timeout, error, OOM, cp_fail |
| `test_dataset_integration.py` | 7 | Cache lifecycle, corruption recovery, persistence |
| `test_manifest_integration.py` | 12 | Run lifecycle, attempt sequencing, cleanup |
| `test_bundles_integration.py` | 13 | Real subprocess builds, discovery, sorting |
| `test_cli_integration.py` | 27 | Arg parsing, command dispatch for all commands |
| `test_e2e.py` | 7 | Full workflow: run → manifest → attempt → summarize |
| **Total** | **80 integration** | **All passing** |

**Grand Total: 220 tests (140 unit + 80 integration)**

### Architecture

```
src/swebench_orchestrator/
├── __init__.py          # Package init
├── cli.py               # Click CLI (mirrors run.sh commands)
├── config.py            # Immutable Config dataclass
├── models.py            # Pydantic data models
├── dataset.py           # DatasetCache + fetch_and_cache_dataset()
├── bundles.py           # AgentBundle + BundleBuilder
├── storage.py           # Disk usage, Docker cleanup
├── docker_ops.py        # DockerOps class for container lifecycle
├── manifest.py          # RunManager + attempt isolation (P1)
└── runner.py            # Runner class + run_instance() + summarize_results()

tests/
├── unit/
│   ├── test_models.py       # 23 tests — data model specifications
│   ├── test_config.py       # 10 tests — configuration specs
│   ├── test_dataset.py      # 19 tests — dataset cache/query
│   ├── test_bundles.py      # 16 tests — bundle building
│   ├── test_storage.py      # 13 tests — storage management
│   ├── test_docker_ops.py   # 15 tests — Docker operations
│   ├── test_manifest.py     # 19 tests — manifest infrastructure (P1)
│   ├── test_runner.py       # 8 tests — runner logic
│   └── test_cli.py          # 17 tests — CLI interface
```

### Key Design Decisions

1. **Pydantic models** — Type-safe data validation, mirrors bash JSON handling
2. **Click CLI** — Drop-in replacement for `run.sh`, same commands
3. **DockerOps class** — Clean abstraction over subprocess/docker calls
4. **RunManager** — Manifest-based run tracking with immutable attempt directories
5. **Config dataclass (frozen)** — Immutable, testable configuration
6. **Dependency injection** — All modules accept dependencies via constructor/params

### Commands Mapped

| run.sh | Python CLI |
|--------|-----------|
| `./run.sh --help` | `swebench-orchestrator --help` |
| `./run.sh --index` | `swebench-orchestrator --index` |
| `./run.sh --list [F]` | `swebench-orchestrator --list [F]` |
| `./run.sh --build [A]` | `swebench-orchestrator --build [A]` |
| `./run.sh --rebuild [S]` | `swebench-orchestrator --rebuild [S]` |
| `./run.sh --run A I [T]` | `swebench-orchestrator --run A I [--timeout T]` |
| `./run.sh --run-all A` | `swebench-orchestrator --run-all A [--timeout T] [--resume]` |
| `./run.sh --eval A` | `swebench-orchestrator --eval A` |
| `./run.sh --summarize [A]` | `swebench-orchestrator --summarize [A]` |
| `./run.sh --status [A]` | `swebench-orchestrator --status [A]` |
| `./run.sh --interactive A I` | `swebench-orchestrator --interactive A I` |
| `./run.sh --init` | `swebench-orchestrator --init` |
| `./run.sh --cleanup` | `swebench-orchestrator --cleanup` |
| `./run.sh --cleanup-partial` | `swebench-orchestrator --cleanup-partial` |

---

## Remaining Work

### Phase 1: Integration Tests (P1b) ✅ DONE

All major integration test categories ported from bash to Python.

**Completed:**
- ✅ T2_docker_mocked — do_run() logic paths (success, timeout, error, cp_fail, oom)
- ✅ T1_filesystem — dataset cache, index/list, bundle build/rebuild
- ✅ T0_pure_shell — arg parsing, config defaults
- ✅ T3_e2e — end-to-end workflows
- ⏳ T4_eval_and_integration — eval, predictions.jsonl (next)

**Next:**
1. T4_eval_and_integration — harness result folding, predictions.jsonl generation

### Phase 2: Eval Integration (P2) ✅ DONE

- ✅ Harness result folding into result.json (`fold_harness_results()`)
- ✅ Predictions.jsonl generation (`generate_predictions()`)
- ✅ `run_eval()` function in runner.py
- ✅ Runner.eval() method
- ✅ tests/integration/test_eval_integration.py — 7 tests
- ✅ tests/integration/test_eval_cli.py — 5 tests

### Phase 3: Smart Ordering & Space Management (P3) ✅ DONE

- ✅ DatasetCache.list_instances() sorts by repo → version → instance_id
- ✅ Disk usage monitoring with warning/critical thresholds
- ✅ Image pruning after eval (`prune_docker_images()`)
- ✅ tests/integration/test_ordering_and_gc.py — 11 tests

### Phase 4: Registry Integration (P4)

- Pull-through registry at `docker-registry.sterling.digital`
- NAS storage for cached layers
- Replace tarball cache with registry mirror

### Phase 5: Cleanup & Hardening (P5)

1. Fix `--cleanup-partial` scope — add `--agent` requirement
2. Scope cleanup traps to active container only
3. Preserve containers during timeout/error paths
4. Add `--run-id` to eval — prevent collision when same agent produces different patches

---

## Quick Reference: Bash → Python

| Aspect | Bash (run.sh) | Python (new) |
|--------|--------------|--------------|
| Entry point | `./run.sh` | `swebench-orchestrator` / `python -m swebench_orchestrator.cli` |
| Config | Shell variables | `Config` dataclass (frozen, immutable) |
| Data models | JSON strings | Pydantic models (`Instance`, `RunManifest`, `Attempt`, etc.) |
| Dataset | Inline Python in bash | `DatasetCache` class + `fetch_and_cache_dataset()` |
| Bundles | `bash build_bundle.sh` | `BundleBuilder.build_agent()` |
| Docker | subprocess calls | `DockerOps` class with clean API |
| Runs | Flat output dirs | Manifest-based: `runs/<run_id>/tasks/<iid>/attempt-NNN/` |
| Tests | Bash scripts (157 tests) | pytest (140 unit tests, porting integration) |

---

## Running Tests

```bash
# All unit tests
.venv/swebench/bin/python -m pytest tests/unit/ -v

# Specific module
.venv/swebench/bin/python -m pytest tests/unit/test_manifest.py -v

# With coverage
.venv/swebench/bin/python -m pytest tests/unit/ --cov=swebench_orchestrator --cov-report=term-missing
```

## Next Immediate Steps

1. **P5: Cleanup & Hardening** — Fix cleanup-partial scope, trap safety, artifact preservation
2. **run_all with resume** — Add --resume flag support to Runner.run_all()
3. **Per-instance eval** — Evaluate immediately after agent run (not batched at end)
4. **P4: Registry Integration** — Pull-through registry, NAS caching
