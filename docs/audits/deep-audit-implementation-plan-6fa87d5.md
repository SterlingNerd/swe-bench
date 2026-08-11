# Deep Audit and Implementation Plan at `6fa87d5`

## Scope, branch state, and recommendation

This document records a deeper audit performed on 2026-08-10 against the
documentation side branch `gage/refactor-python-doc-audit` at `6fa87d5`. Its
runtime tree is based on upstream `main` at `215f6d7`. The remote was fetched
before the audit; the side branch matched its remote and contained two
documentation commits above `origin/main`. The worktree was clean. Local
`main` and Josh's working files were not checked out, rewritten, or edited.

This commit adds planning documentation only. It does not implement the fixes
below.

**Recommendation:** do not treat the Python orchestrator as merge-ready yet.
The two original command-composition blockers are repaired, but the current
tree still has destructive cleanup scope, cross-agent writable output access,
ambiguous attempt ownership, incomplete failure finalization, an evaluator
contract that is incompatible with current unpinned SWE-bench, and a failing
deterministic integration gate.

## What changed since the synchronization review

The preceding [main synchronization review](main-sync-aa0644f-215f6d7.md)
documented the imported `aa0644f..215f6d7` range and the disposition of the
original fifteen findings. This deeper pass adds focused static analysis,
coverage measurement, fault probes, supply-chain and installed-package review,
and a current-upstream contract comparison.

Newly confirmed findings are:

1. Every agent container receives the entire global output root as a writable
   bind mount. A task for one agent can modify another agent's results and prior
   attempts.
2. `init` raises `UnboundLocalError` when the SWE-bench environment already
   exists because a function-local `import subprocess` shadows the module
   import.
3. The documented `timeout=0` meaning "no timeout" instead causes the
   `run-all` stale-container wait to skip immediately and force-release every
   matching agent container.
4. A program that legitimately exits with status 124 is classified as an
   orchestrator timeout even when `subprocess.run` did not time out.
5. A Click command that exits early can retain the global lock in the same
   process, causing the next invocation to report that another instance is
   running.
6. `summarize_results()` infers `outputs` instead of the agent name when its
   optional `agent` argument is omitted.
7. Current SWE-bench accepts space-separated `--instance_ids` and does not
   define `--cache_level`; both local eval implementations pass a comma-joined
   ID token and `--cache_level instance`. The harness is installed without a
   version or commit pin.
8. Current SWE-bench writes the aggregate report from the model name and
   `run_id` in the harness working directory. The local code searches a shared
   `eval/` directory and may select the newest unrelated JSON file.
9. Ruff finds three direct correctness defects: the `init` shadowing bug, the
   undefined `list_attempts` call in event export, and a duplicated test method
   that silently replaces an intended test.
10. The unit suite covers 60% of the package. The least-covered operational
    modules are the CLI (26%), shutdown handling (30%), and storage cleanup
    (47%), which are also where several high-risk behaviors live.

## Evidence and validation

The existing isolated Python 3.12.3 environment was reused. Its relevant
versions were Click 8.4.2, Pydantic 2.13.4, pytest 9.1.1, pytest-mock 3.15.1,
Docker 7.2.0, GitPython 3.1.59, python-dotenv 1.2.2, and Ruff 0.16.2. No
production Docker container or external repository was mutated.

| Check | Result |
|---|---|
| Python bytecode compilation | Passed |
| Unit suite | 234 passed |
| Unit coverage | 60% overall; CLI 26%, shutdown 30%, storage 47% |
| Non-Docker integration gate from the synchronization review | 146 passed, 23 failed |
| Deterministic non-Docker total | 380 passed, 23 failed |
| Real-Docker integration | 10 tests not run |
| Full Ruff scan | 227 findings, including F821, F823, and F811 correctness defects |
| GitHub Actions workflows | None present |

Focused probes produced these results:

```text
existing SWE-bench environment -> init: UnboundLocalError
timeout_seconds=0 with a matching container -> container force-released
summarize_results(agent_dir) -> agent: "outputs"
ordinary subprocess return code 124 -> status: "timed_out"
early Click exit followed by a second invocation -> stale lock blocks second call
```

The previous 23 deterministic integration failures remain actionable:

- Twenty fail in the production runner copy/flatten path because the
  destination instance directory is not guaranteed to exist.
- Two eval tests exit on the SWE-bench executable precondition before reaching
  their mocked harness assertions.
- One order-dependent eval/summarize test is a symptom of the retained Click
  lock.

Ruff's correctness-only result is:

```text
src/swebench_orchestrator/cli.py:493       F823 local subprocess before assignment
src/swebench_orchestrator/manifest.py:447  F821 undefined list_attempts
tests/unit/test_runner.py:544              F811 duplicate test method name
```

## Current finding inventory

The IDs F01-F15 retain their meaning from the historical audit. F16-F23 are
introduced here so implementation work and regression tests can refer to stable
labels.

| ID | Severity | State | Required outcome |
|---|---|---|---|
| F01 duplicate Docker image token | Critical | Resolved | Retain the exact-argv regression and a production-path Docker canary. |
| F02 doubled agent output path | Critical | Resolved for path composition | Preserve one agent segment, but replace the global writable mount per F16. |
| F03 destructive `cleanup-partial` scope | Critical | Open | Never recurse from the global output root into an agent root; require exact run/attempt scope and dry-run by default. |
| F04 incomplete failure artifact finalization | High | Open | Finalize logs, partial patch, result, and diagnostics before releasing the owned container on every terminal path. |
| F05 empty/invalid output accepted as success | High | Open | Require a typed `result.json`; missing, malformed, or empty artifacts must be explicit failure states. |
| F06 concurrent ownership and overwrite | Critical | Open | Replace deterministic ownership with a lease/run identity; never remove a resource merely because its name collides. |
| F07 signal and exception finalization | Critical | Open | Request cancellation in the handler and finalize exactly once in normal control flow. |
| F08 ambiguous attempt result update | Critical | Open | Address attempts by `(run_id, instance_id, attempt_id)` and reject unknown paths. |
| F09 non-immutable attempt allocation/artifacts | Critical | Open | Atomically claim unique attempt IDs and make the attempt directory authoritative. |
| F10 missing batch identity/provenance/resume contract | High | Open | Create one manifest per batch, snapshot provenance, and resume only validated compatible terminal states. |
| F11 duplicated and incompatible eval path | Critical | Open | Use one pinned adapter, exact upstream argv, unique eval identity, and exact report selection. |
| F12 non-atomic state writes | High | Open | Write a same-directory temporary file, flush/fsync it, replace atomically, and synchronize concurrent writers. |
| F13 broken event export | High | Open | Call a real manager/query API and test empty, multi-run, multi-instance, and invalid-state exports. |
| F14 global Docker cleanup ownership | Critical | Open | Label every resource and filter by orchestrator/run/attempt ownership before stop, remove, wait, or prune. |
| F15 fail-open/ineffective storage guard | High | Open | Treat measurement failure as unknown, prevent new work at the critical threshold, and never perform global image deletion implicitly. |
| F16 cross-agent writable output mount | Critical | Open | Mount only the active attempt output directory read-write; no container may see sibling agents or prior attempts. |
| F17 installed-environment `init` crash | High | Open | Make installation idempotent, remove import shadowing, check subprocess failures, and report the installed pinned version. |
| F18 zero-timeout force-release | Critical | Open | Keep zero as unlimited instance runtime and use a separate, explicit takeover grace policy that never kills unowned work. |
| F19 exit-code 124 misclassification | High | Open | Mark timeout only from a real timeout/cancellation event; preserve the agent/container exit code independently. |
| F20 lock lifecycle and scope | High | Open | Register release with Click context teardown, namespace the lock by checkout/workspace, and keep read-only commands nonexclusive where safe. |
| F21 summary/status identity integrity | Medium | Open | Derive the agent from the correct directory, bind reports to eval IDs, and validate count/set consistency. |
| F22 reproducibility and supply chain | High | Open | Lock Python/harness dependencies, dataset revision, image digests, and downloaded bundle assets with verified checksums. |
| F23 test/packaging gate integrity | High | Open | Restore the overwritten test, make deterministic CI mandatory, mark Docker tests explicitly, and remove tracked generated metadata. |

## Detailed risk notes

### Container and artifact isolation

`runner.py` mounts the global host output directory at
`/workspace/outputs` read-write, then relies on `SWE_OUTPUT_ROOT` to guide the
entrypoint into one agent subtree. That environment convention is not an
access boundary. The container can create, replace, or delete any sibling in
the mount. Bind mounts are writable by default, so the isolation must come from
the host path selected for the mount, not from an environment variable.

The successful copy path also uses a predictable `.tmp_<instance>` directory,
deletes colliding destination entries, and stores final artifacts in a flat
agent/instance directory. Direct Runner callers can therefore collide even
though the Click entrypoint has a global lock. If copying returns false, the
container is removed before the copy failure is returned. If copying or
flattening raises, the temporary directory is cleaned but the container and
pending attempt may remain.

Docker documents that `docker cp` can copy from a stopped container and that it
does not create missing parent directories. Preserve a failed container until
best-effort collection has completed, and create a unique staging directory
inside the authoritative attempt directory.

### Lifecycle and state

The runner creates an attempt before validating and running the task, but only
updates it after the low-level call returns normally. Exceptions can leave it
pending forever. Attempt allocation uses `len(existing)+1` with
`exist_ok=True`, result updates search all task directories for the first
matching attempt number, and a missing match fabricates a task directory.
Those behaviors contradict the documented immutability model.

State files, dataset caches, predictions, status, and entrypoint results are
written by truncating the final path. A crash or concurrent writer can leave
valid-looking paths with partial contents. Python's `os.replace` is atomic when
the temporary and destination paths are on the same filesystem; durable state
also needs file flush/fsync and, where required, directory fsync.

The signal handler performs blocking Docker operations and raises `SystemExit`
from the handler. Python delivers Python-level handlers in the main thread and
warns against synchronization primitives in handlers. The handler should set a
minimal cancellation flag/event; the coordinator should own bounded TERM,
KILL, artifact finalization, and final state transitions.

### Evaluation and reporting

There are separate evaluator implementations in `cli.py` and `runner.py`.
Both reuse agent-scoped `predictions.jsonl`, `eval/`, and `run_id`, so retries
and concurrent evaluations overwrite one another. Current upstream
`run_evaluation.py` defines `--instance_ids` with `nargs="+"` and no
`--cache_level`. Current `reporting.py` names the aggregate report
`<model_name>.<run_id>.json` in the process working directory. The local
fallback that selects the newest JSON can fold another run's result.

The adapter must be pinned and contract-tested. It should generate a unique
eval ID, place predictions and reports under that eval directory, pass each
instance ID as its own argv token, omit unsupported flags, and accept only the
exact expected report whose submitted IDs and identity match the manifest.

### Reproducibility, installation, and cleanup

`init` executes unpinned `pip install swebench`; `pyproject.toml` uses lower
bounds rather than a lock; dataset fetching uses mutable `python:3.10-slim`
plus an unpinned in-container `pip install datasets`; and the global cache
contains no dataset name, split, revision, schema, or digest. A nonempty JSON
list for a different dataset is accepted as valid.

The Pi bundle script deletes the existing bundle before completing a rebuild,
downloads Node.js, fd, and ripgrep without checksum verification, and installs
the Pi npm package without a committed lock-based installation. Build into a
unique sibling directory, verify all required files and digests, and atomically
promote only a successful bundle. Authentication material should be injected at
build/run time under an explicit placeholder/secret policy rather than copied
indiscriminately from a tracked file.

`Config.from_env()` derives the repository root from the installed package
path. In a wheel/site-packages installation this can point outside the checkout.
Accept an explicit workspace/repository root, validate its expected structure,
and keep runtime state outside package installation directories.

Cleanup counts removals without checking all Docker return codes, signal
cleanup targets every `swe_*` container, stopped-container cleanup targets the
entire Docker daemon, and image pruning force-removes every matching global
repository. None of those operations is safe until ownership labels and exact
filters are mandatory.

## Ordered implementation plan

### Phase 0 — Freeze contracts and add failing regressions

1. Introduce typed IDs for orchestrator/workspace, run, eval, instance, and
   attempt. Document the artifact layout and allowed state transitions.
2. Restore the overwritten unit test under a unique name.
3. Add failing tests for F03-F23 before changing the implementation. Prefer
   production entrypoints with fakes only at subprocess, filesystem-fault, or
   Docker-daemon boundaries.
4. Create checked-in evaluator argv/report fixtures for the exact pinned
   SWE-bench version.

Exit gate: every open finding has a focused regression that fails for the
intended reason; test collection increases by at least one after restoring the
overwritten test.

### Phase 1 — Establish ownership and filesystem containment

1. Create one run manifest before a single run or batch begins. Generate a
   collision-resistant run ID and record orchestrator commit, dirty-state
   indicator, effective config, agent bundle digest, dataset name/split/revision
   and digest, image digest, host architecture, timeout, and start time.
2. Atomically create
   `runs/<run>/tasks/<instance>/<attempt>/`; make that directory the sole
   read-write output mount. Expose a derived flat `latest` view only after a
   successful finalization.
3. Label containers with namespaced orchestrator, workspace, run, instance, and
   attempt IDs. Select resources through exact label filters, never name-prefix
   scans.
4. Replace deterministic stale-container deletion with an ownership/lease
   check. A live foreign lease returns `busy`; only an expired, provably owned
   lease can be reclaimed.
5. Rewrite `cleanup-partial` around manifests and exact attempt paths. Default
   to dry-run, require an explicit apply option, validate path containment, and
   refuse output-root, agent-root, run-root, symlink, and unknown-schema
   targets.

Exit gate: two concurrent fake runs cannot observe or modify each other's
artifacts or resources; cleanup adversarial tests preserve every sentinel and
refuse all parent/symlink escapes.

### Phase 2 — Make execution a single finalizable lifecycle

1. Add a lifecycle context that owns the attempt transition, container ID,
   captured stdout/stderr, artifact staging, final result, and cleanup result.
2. Persist `pending -> running -> terminal` atomically. Define terminal states
   including `patch_collected`, `no_patch`, `agent_error`, `container_error`,
   `timed_out`, `interrupted`, `copy_failed`, and `invalid_output`.
3. On success, timeout, nonzero exit, cancellation, copy failure, and unexpected
   exception: capture logs; copy or inspect artifacts best-effort; validate the
   typed result; merge host-observed fields; publish atomically; then release
   the exact labeled container.
4. Treat only `TimeoutExpired` or explicit cancellation as timeout. Keep an
   ordinary exit code 124 as a container/agent error. Separate per-instance
   runtime from stale-resource takeover grace; zero means unlimited runtime.
5. Have signal handlers request cancellation only. Perform bounded TERM, grace,
   KILL, finalization, and lock release from coordinator control flow. Preserve
   distinct conventional SIGINT/SIGTERM exit semantics.
6. Register lock release with `ctx.call_on_close()` or a Click-managed resource;
   make setup idempotent and prevent duplicate logging handlers.

Exit gate: fault injection at every lifecycle boundary produces one terminal
attempt, retained diagnostics, no unowned Docker action, and no stale lock.

### Phase 3 — Repair manifests, atomic state, and resume

1. Require `(run_id, instance_id, attempt_id)` for every attempt read/update.
   Unknown or ambiguous identities must raise; never fabricate fallback paths.
2. Use exclusive atomic creation or UUID-backed attempt IDs. Never reuse a
   suffix or overwrite a prior attempt.
3. Replace free-form status strings with enums and validate state transitions,
   nonnegative counts/times, exit-code provenance, and schema versions.
4. Implement one atomic JSON/JSONL writer using a same-directory temporary,
   flush/fsync, `os.replace`, and directory fsync where durability is required.
   Serialize updates with the exact attempt lease.
5. Make `run-all` one batch manifest with a declared task inventory. Resume
   only a schema-valid compatible terminal attempt; malformed, pending,
   mismatched-provenance, and explicitly retryable failures must not be skipped.
6. Repair event export through `RunManager.list_attempts()` and use real attempt
   timestamps rather than the run creation time as a proxy.

Exit gate: concurrent allocation stress has no duplicate attempts; crash tests
leave either the old complete state or new complete state; batch/resume and
event-export integration tests pass.

### Phase 4 — Consolidate and pin evaluation

1. Delete the duplicated CLI evaluator logic; make Click call one evaluator
   service used by `Runner.eval`.
2. Pin the SWE-bench package to a reviewed version/commit and record it in the
   run/eval manifest. Maintain an explicit compatibility adapter if more than
   one version must be supported.
3. Create `runs/<run>/evals/<eval-id>/` containing immutable predictions,
   stdout, stderr, argv, environment/version metadata, and the aggregate report.
4. Generate predictions atomically. Pass `--instance_ids` followed by separate
   ID tokens, omit unsupported options, use a unique `run_id`, and use the
   working directory/report convention of the pinned harness.
5. Compute the exact expected report path; do not scan for "latest" JSON.
   Validate schema, eval identity, submitted-ID equality, disjoint result sets,
   and set/count consistency before folding.
6. Fold once into the exact attempts referenced by the eval manifest. Preserve
   the original execution status and store evaluation outcome separately.

Exit gate: adapter contract tests pass against the pinned installed harness;
two concurrent/repeated evals remain isolated; malformed or unrelated reports
are rejected; a small opt-in real-Docker evaluation canary succeeds.

### Phase 5 — Make install, data, bundles, and storage reproducible

1. Move imports to module scope, fix the existing-environment `init` path, use
   checked subprocesses, and verify the exact pinned SWE-bench version after an
   idempotent install.
2. Add a lock/constraints workflow for runtime and development dependencies.
   Stop tracking generated `*.egg-info`; ignore local coverage and pytest cache
   output.
3. Namespace the dataset cache by dataset, split, and revision. Validate required
   fields and unique IDs, record a content digest, write atomically, and surface
   Docker/direct-fetch failures without hiding the original cause.
4. Pin fetch images by digest and remove runtime `pip install` where practical.
   Derive SWE-bench image architecture/tag from the pinned harness/dataset
   contract rather than hard-coded `x86_64` and `_1776_` assumptions.
5. Build bundles transactionally. Commit or generate dependency locks, verify
   upstream checksums/signatures for downloads, validate required executables
   and config, and retain the prior good bundle on failure.
6. Make storage measurement return an explicit unknown/error state. Block new
   work at critical usage, offer ownership-scoped cleanup separately, and
   report only Docker actions whose return codes succeeded.
7. Stop suppressing `git add`/`git diff` failures in the entrypoint; record them
   as artifact-generation errors and write entrypoint metadata atomically.

Exit gate: clean/offline reinstall tests are reproducible from locked inputs;
failed bundle rebuilds preserve the prior bundle; wrong/stale dataset caches are
rejected; critical or unknown storage cannot silently start work.

### Phase 6 — Establish merge gates and reconcile documentation

1. Add CI for supported Python versions with compilation, correctness-focused
   Ruff (`F821`, `F823`, `F811` initially), unit tests, and all deterministic
   non-Docker integration tests. Broaden lint in reviewed batches rather than
   applying 146 automatic changes blindly.
2. Mark real-Docker tests explicitly and exclude them from the deterministic
   default. Run them in a separate opt-in/environment-capable job with cleanup
   verification.
3. Raise coverage on CLI, shutdown, storage, runner lifecycle, manifest, and
   evaluator paths. Use branch coverage and fault/concurrency tests; do not use
   a global percentage alone as the safety claim.
4. Fix the current 23 non-Docker failures and require at least 404 deterministic
   tests after restoring the overwritten test. Keep the 10 Docker tests as a
   separately reported canary suite.
5. Update CLI help, wrapper comments, agent documentation, artifact schema,
   cleanup warnings, timeout semantics, recovery procedures, and evaluator pin
   only after their implementation tests pass.

Exit gate: deterministic CI is required and green from a clean environment,
correctness Ruff is clean, the Docker canary is separately reported, docs match
observed behavior, and no P0/P1 finding above remains open.

## Suggested commit sequence

Keep changes reviewable and preserve a green gate after each commit:

1. `test: restore overwritten test and codify safety regressions`
2. `feat: add run identity, attempt paths, and Docker ownership labels`
3. `fix: scope output mounts and make cleanup dry-run and contained`
4. `fix: centralize runner lifecycle and terminal artifact finalization`
5. `fix: make manifest addressing and state writes atomic`
6. `fix: make batch resume provenance-aware`
7. `fix: consolidate evaluation behind pinned SWE-bench adapter`
8. `build: lock installs, dataset inputs, images, and bundle downloads`
9. `ci: add deterministic gates and isolated Docker canaries`
10. `docs: publish verified operations and recovery contracts`

Avoid combining the ownership/layout migration with evaluator or supply-chain
work in one commit. Existing flat outputs should be treated as read-only legacy
data and migrated by a separate dry-run-first tool after the new schema is
stable.

## Definition of done

The rewrite is ready for merge only when all of the following are true:

- No task container can read or write another agent, run, instance, or attempt.
- Cleanup, signal handling, stale-resource recovery, and image removal act only
  on exact labeled resources owned by the active workspace/run.
- Every started attempt reaches exactly one durable terminal state with its
  diagnostics, including timeout, interrupt, exception, and copy failure.
- Attempts and evals are immutable, uniquely addressed, and provenance-complete.
- Batch resume is schema- and provenance-aware rather than file-existence-based.
- The pinned SWE-bench adapter passes an installed-harness contract test and
  selects only its exact report.
- Critical/unknown storage blocks new work without global destructive cleanup.
- The clean deterministic suite and correctness lint are required and green;
  the real-Docker canary is separately visible.
- Operational documentation is generated from or checked against the tested
  CLI and artifact contracts.

## External contract references

- [Current SWE-bench evaluator source](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/run_evaluation.py)
- [Current SWE-bench report generation](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/reporting.py)
- [Docker bind-mount behavior](https://docs.docker.com/engine/storage/bind-mounts/)
- [Docker object labels and filters](https://docs.docker.com/engine/manage-resources/labels/)
- [Docker container copy behavior](https://docs.docker.com/reference/cli/docker/container/cp/)
- [Click context cleanup callbacks](https://click.palletsprojects.com/en/stable/api/#click.Context.call_on_close)
- [Python atomic replacement and filesystem synchronization](https://docs.python.org/3/library/os.html#os.replace)
- [Python signal-handler execution model](https://docs.python.org/3/library/signal.html#execution-of-python-signal-handlers)
