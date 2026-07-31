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
| **Total** | **~5,500 lines** | **140** | **All passing** |

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

### Phase 1: Integration Tests (P1b) — NEXT

Port the bash integration tests to Python. The unit tests encode the specification; integration tests verify end-to-end behavior.

**What to build:**
- `tests/integration/` — Integration test suite
- Mirror existing test categories: T0 (arg parsing), T1 (filesystem), T2 (Docker mocked), T3 (e2e), T4 (eval)
- Use fixtures from `tests/fixtures/` (mock-entrypoint.sh, fake docker)

**Priority:**
1. T2_docker_mocked — do_run() logic paths (success, timeout, error, cp_fail, oom)
2. T1_filesystem — dataset cache, index/list, bundle build/rebuild
3. T0_pure_shell — arg parsing, config defaults
4. T3_e2e — end-to-end workflows
5. T4_eval_and_integration — eval, predictions.jsonl

### Phase 2: Eval Integration (P2)

The `--eval` command currently runs swebench harness via subprocess. Need to:
- Fold harness results back into result.json (currently done in bash inline Python)
- Add per-instance eval after agent run (not batched at end)
- Prune swebench image after eval completes

**Code changes:**
- `runner.py`: Add `evaluate_instance()` function
- `cli.py`: Update `--eval` to use new harness integration
- Tests: Per-instance eval, image pruning, space management

### Phase 3: Smart Ordering & Space Management (P3)

- `get_ordered_instances()` — sort by repo → version → instance_id
- Periodic GC every N instances in `do_run_all()`
- Emergency GC when disk approaches 90%

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

1. **Create `tests/integration/`** — Port T2_docker_mocked first (highest value)
2. **Add integration test fixtures** — Python equivalents of mock-entrypoint.sh
3. **Implement per-instance eval** — Fold harness results into attempt results
4. **Update TODO.md** as work progresses
