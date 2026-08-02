# PR #7 Python Rewrite Audit

## Scope and verdict

This is a read-only audit of GitHub PR #7 at the immutable head commit
`3853fe0e344c5c3955e4a29d43ab5aeed2805a14` on 2026-08-01. Findings below
refer to that tree even if `refactor-python` advances later.

**Verdict:** do not merge this commit into `main`. The package has useful
scaffolding and its 140 unit tests pass in an isolated environment, but the
production work command is currently nonfunctional and multiple cleanup,
concurrency, artifact, manifest, and evaluation contracts can lose or
misattribute results.

Classifications used here:

- **Confirmed production defect** — the production code path itself establishes
  the failure without relying on a speculative environment.
- **Missing verification** — a claimed guarantee is not exercised by a test
  that invokes the responsible production path.
- **Documentation mismatch** — prose or PR claims disagree with the audited
  tree.
- **Design recommendation** — a proposed direction, not a claim that the current
  design is necessarily defective by itself.

## Audit execution

The source audit used `git show`, `git grep`, and `git ls-tree` against the
commit directly. Tests were collected and run from a temporary `git archive`,
not from this worktree, using Python 3.12.3, Click 8.4.2, Pydantic 2.13.4,
pytest 9.1.1, and pytest-mock 3.15.1.

| Gate | Result |
|---|---|
| Python collection | 297 collected: 140 unit, 157 integration |
| Unit suite | 140 passed |
| Integration suite excluding 10 real-Docker tests | 137 passed, 10 failed |
| Real-Docker suite | Not run; intentionally excluded from the read-only gate |
| GitHub CI at PR head | No workflow or reported status checks |

The ten non-Docker failures are all in
`tests/integration/test_runner_mocked.py`. Its `MockDockerOps` overrides
container execution and copying but inherits the real `image_exists` and
`pull_image` methods, so the supposedly mocked tests reach the local Docker
boundary before exercising their intended assertions.

## Confirmed production defects

### F01 — Docker receives the image twice

- **Severity:** Critical
- **Classification:** Confirmed production defect
- **Evidence:** `src/swebench_orchestrator/runner.py:141-160` starts `command`
  with `image_name` and also passes `image_name=`. `DockerOps.run_container`
  independently inserts `[image_name]` at
  `src/swebench_orchestrator/docker_ops.py:165-189`.
- **Observed flow:** the argv is effectively
  `docker run ... IMAGE IMAGE /agent/entrypoint.sh ...`. Docker treats the
  second image token as the executable inside the container.
- **Impact:** the normal Python `run` path exits before the agent entrypoint;
  claims about successful end-to-end Python work are not established.
- **Regression test:** invoke the production runner with only subprocess
  recording at the Docker boundary and assert the complete argv contains the
  image exactly once followed immediately by `/agent/entrypoint.sh`. Add one
  opt-in lightweight real-Docker smoke test through `Runner`, not only
  `DockerOps`.
- **Minimal suggested fix:** remove `image_name` from the beginning of the
  runner's `command` list and keep image insertion solely in `run_container`.

### F02 — The output contract creates a doubled agent directory

- **Severity:** Critical
- **Classification:** Confirmed production defect
- **Evidence:** the canonical host path is selected at
  `runner.py:115-118`; `runner.py:135-139` mounts host `<outputs>/<agent>` at
  `/workspace/outputs` while setting
  `SWE_OUTPUT_ROOT=/workspace/outputs/<agent>`. The Pi entrypoint appends the
  instance at `agents/pi/entrypoint.sh:47-48,67-80`. Legacy `run.sh` has the
  same mount/environment pairing at `run.sh:674-700`.
- **Observed flow:** the entrypoint writes
  `/workspace/outputs/<agent>/<instance>`, mapping to host
  `<outputs>/<agent>/<agent>/<instance>`. The success path then copies those
  files again into `<outputs>/<agent>/<instance>` at `runner.py:206-235`.
- **Impact:** success duplicates storage; timeout/error artifacts are stranded
  outside the canonical path; summary, resume, and eval can miss them.
- **Regression test:** assert the exact mount, environment variable, entrypoint
  destination, and copy source in one production-runner contract. Prove no
  `<agent>/<agent>` directory is created.
- **Minimal suggested fix:** keep the agent-specific host mount but set
  `SWE_OUTPUT_ROOT=/workspace/outputs`, and copy
  `/workspace/outputs/<instance>`.

### F03 — `cleanup-partial` can erase an entire agent history

- **Severity:** Critical
- **Classification:** Confirmed production defect
- **Evidence:** `Config.output_dir` is the global output root at
  `src/swebench_orchestrator/config.py:35-38`. The Click command iterates its
  immediate children, looks for result and patch files at that level, and runs
  `shutil.rmtree` at `src/swebench_orchestrator/cli.py:571-586`.
- **Observed flow:** for `outputs/pi/<instance>/...`, the command examines
  `outputs/pi`, finds no `outputs/pi/result.json`, and recursively deletes the
  complete `pi` tree.
- **Impact:** one cleanup invocation can destroy every stored result for every
  agent, including the doubled-path artifacts from F02.
- **Regression test:** invoke the real Click command against multiple agent
  roots containing complete/incomplete instances and sentinels. Assert agent
  roots, complete instances, the output root, and unrelated siblings survive.
- **Minimal suggested fix:** require explicit agent/run scope; traverse
  `<output>/<agent>/<instance>`; refuse agent-root deletion; add containment
  checks; make dry-run the default and require a separate apply flag.

### F04 — Timeout/error artifacts are not normalized into the run record

- **Severity:** High
- **Classification:** Confirmed production defect
- **Evidence:** timeout and container-error branches remove the container and
  return at `runner.py:166-196`. Collection occurs only after a successful exit
  at `runner.py:198-247`. The entrypoint writes metadata, logs, and sessions
  incrementally at `agents/pi/entrypoint.sh:67-95`.
- **Observed flow:** failure removes the container and writes a canonical host
  status without best-effort collection. With F02, bind-mounted partial output
  may survive only in the unexpected doubled path; container-only data is no
  longer recoverable.
- **Impact:** the canonical attempt lacks the transcript, session, partial
  patch, and diagnostics most needed after a failure.
- **Regression test:** drive timeout, exit 137, and ordinary nonzero through the
  production runner; assert collection/normalization precedes release and
  captured result metadata is merged rather than discarded.
- **Minimal suggested fix:** perform best-effort artifact finalization for every
  terminal status inside `try/finally`, then release only the owned container.

Qualification: correctly placed bind-mounted data survives container removal.
The confirmed defect is canonical collection and attribution, not a claim that
`docker rm -f` necessarily destroys every bind-mounted byte.

### F05 — Empty copied output can be reported as `patch_collected`

- **Severity:** High
- **Classification:** Confirmed production defect
- **Evidence:** `runner.py:211-235` sets `cp_ok=True` when the copied directory
  exists, without requiring any file. `runner.py:264-267` defaults the final
  status to `patch_collected`, and `runner.py:283-287` returns success.
- **Impact:** a container that exits zero but produces no result or patch can be
  recorded as successful work.
- **Regression test:** make the production copy boundary return an empty
  directory and assert a non-success status with diagnostic preservation.
- **Minimal suggested fix:** require a valid typed `result.json` and apply an
  explicit policy for `patch.diff`; never default missing output to
  `patch_collected`.

### F06 — Same-instance concurrency can kill and overwrite an active run

- **Severity:** Critical
- **Classification:** Confirmed production defect
- **Evidence:** `config.py:30` defines `lock_file`, but production never
  acquires it. `runner.py:120-123` uses a deterministic container name and
  unconditionally releases any existing container. `runner.py:115-118` reuses
  the same flat output path.
- **Observed flow:** a second process for the same agent/instance force-removes
  the first process's live container, then both processes target the same
  files.
- **Impact:** active work can be terminated and its results overwritten,
  combined, or misclassified.
- **Regression test:** block one production-path fake run, start a second, and
  assert the second fails with a lease/busy result without releasing the first
  process's container.
- **Minimal suggested fix:** acquire a nonblocking per-run or
  per-agent/instance lease before any mutation and label containers with their
  lease/run identity.

### F07 — Signals and unexpected exceptions have no production finalizer

- **Severity:** Critical
- **Classification:** Confirmed production defect
- **Evidence:** execution blocks at `runner.py:155-162`; cleanup occurs only in
  explicit return branches and at `runner.py:246-247`. No outer lifecycle
  `finally` exists. The only production `signal` import is unused at
  `docker_ops.py:384`.
- **Impact:** SIGINT, SIGTERM, `KeyboardInterrupt`, or a copy/rename exception
  can leave a running container, a pending attempt, and unattributed partial
  output.
- **Regression test:** launch the actual CLI around a blocking fake child,
  deliver SIGINT and SIGTERM, and assert scoped cancellation, artifact
  finalization, an `interrupted` attempt status, and the expected exit code.
- **Minimal suggested fix:** install application handlers that request
  cancellation and wrap owned-container/attempt state in a lifecycle context
  that always finalizes exactly once.

### F08 — Attempt result updates can target the wrong instance

- **Severity:** Critical
- **Classification:** Confirmed production defect
- **Evidence:** attempts are numbered per instance at
  `src/swebench_orchestrator/manifest.py:218-230`, so many tasks have
  `attempt-001`. `update_attempt_result` accepts only run and attempt IDs and
  selects the first matching task at `manifest.py:246-280`. If no match exists,
  it silently fabricates a path at `manifest.py:282-285`.
- **Impact:** a multi-task run can attach status and timing to the wrong
  SWE-bench instance or create malformed task structure.
- **Regression test:** create two tasks with `attempt-001`, update the second,
  and prove only its exact result changes. An unknown attempt must raise.
- **Minimal suggested fix:** require `(run_id, instance_id, attempt_id)` and
  address the direct validated path; remove the silent fallback.

### F09 — Attempt allocation and artifacts are not immutable

- **Severity:** Critical
- **Classification:** Confirmed production defect
- **Evidence:** `manifest.py:221-230` chooses
  `len(existing_attempts)+1` and uses `mkdir(exist_ok=True)`, allowing suffix
  reuse after a gap or a concurrent race. Attempt directories receive only
  metadata/result at `manifest.py:229-238,287-295`. Actual artifacts use the
  flat path at `runner.py:115-118`, where old destinations are deleted at
  `runner.py:214-234`.
- **Impact:** attempt metadata can be overwritten and historical patches,
  sessions, or logs are lost on rerun despite immutable-attempt claims.
- **Regression test:** preserve two attempts with distinct content, remove a
  lower suffix, and create concurrently. Require unique new IDs and unchanged
  prior artifacts/digests.
- **Minimal suggested fix:** atomically claim globally unique attempt IDs and
  make the attempt directory the authoritative mount/artifact destination. A
  flat `latest` view may be derived afterward.

### F10 — `run-all`, provenance, and resume do not describe one batch

- **Severity:** High
- **Classification:** Confirmed production defect
- **Evidence:** `runner.py:683-697` calls `self.run_instance` without a run ID;
  `runner.py:626-633` therefore creates a new manifest for each instance.
  Dataset and commit hashes default empty at
  `src/swebench_orchestrator/models.py:63-69`. Resume skips on mere file
  existence at `runner.py:684-688`.
- **Impact:** a batch has no single identity or immutable configuration;
  malformed, failed, stale, or mismatched `result.json` files can be treated as
  complete.
- **Regression test:** run two cached instances and require one manifest with a
  task inventory and nonempty provenance. Parameterize malformed JSON, timeout,
  error, missing patch, and mismatched provenance for resume.
- **Minimal suggested fix:** create one batch manifest before the loop, pass its
  ID through all attempts, snapshot effective provenance, and define typed
  terminal/retryable resume states.

### F11 — Evaluation output is malformed, colliding, and incompletely folded

- **Severity:** Critical
- **Classification:** Confirmed production defect
- **Evidence:** the public Click path hand-builds JSON with `repr(patch)` at
  `src/swebench_orchestrator/cli.py:271-277`, which is not JSON encoding. It
  bypasses `Runner.eval` and folding at `cli.py:282-303`. Both implementations
  reuse `predictions.jsonl`, `eval/`, and agent-only `--run_id` at
  `runner.py:415-425,556-568` and `cli.py:271-297`. Both construct one
  comma-joined `-i...` token at `runner.py:559-570` and `cli.py:288-299`, while
  the official harness defines `--instance_ids` with `nargs='+'` and documents
  space-separated IDs.
- **Impact:** ordinary patches can produce invalid predictions; multi-instance
  filtering can fail; repeated/concurrent evaluations overwrite inputs and
  reports; successful CLI evaluation leaves summary state stale.
- **Regression test:** invoke the real Click command with patches containing
  newlines, quotes, and backslashes; parse each prediction using `json.loads`;
  assert separate argv elements for multiple instance IDs; run two eval IDs and
  fold each exact report only into its originating attempts.
- **Minimal suggested fix:** delete the duplicate CLI eval implementation and
  delegate to one runner service using `json.dumps`, unique run/eval namespaces,
  separate instance argv elements, and an exact expected report path.

### F12 — JSON state updates are neither atomic nor serialized

- **Severity:** High
- **Classification:** Confirmed production defect
- **Evidence:** `src/swebench_orchestrator/models.py:143-147` truncates final
  JSON paths directly. Read-modify-write updates occur without a lease at
  `runner.py:274-280,306-322,462-477`.
- **Impact:** interruption can leave invalid JSON, while concurrent runner/eval
  updates can lose each other's fields. Resume can then skip the corrupted file
  under F10.
- **Regression test:** fault immediately before atomic publication and require
  the previous file remain valid; synchronize two writers of disjoint fields
  and require both fields survive.
- **Minimal suggested fix:** serialize updates per run/attempt and write a
  same-directory temporary file followed by flush, fsync, and atomic replace.

### F13 — Audit-event export raises at runtime

- **Severity:** Medium
- **Classification:** Confirmed production defect
- **Evidence:** `src/swebench_orchestrator/manifest.py:447` calls an undefined
  module-level `list_attempts`; only `RunManager.list_attempts` exists at
  `manifest.py:327-355`.
- **Impact:** a promised recovery/debugging export fails as soon as it processes
  a run.
- **Regression test:** create a run plus attempt and require ordered exported
  events without exceptions.
- **Minimal suggested fix:** call the manager method with the run ID and record
  real attempt timestamps rather than using run creation time as a proxy.

### F14 — Cleanup ownership is global, not scoped

- **Severity:** High
- **Classification:** Confirmed production defect
- **Evidence:** `src/swebench_orchestrator/storage.py:113-160` force-removes and
  disconnects every container matching `^/swe_`; runner containers have no
  ownership labels. `storage.py:61-107` force-removes every image repository
  beginning `swebench/`, broader than the legacy `swebench/sweb.` filter.
- **Impact:** cleanup can terminate Josh's or another checkout's concurrent
  run and evict unrelated/shared image caches on the same Docker daemon.
- **Regression test:** model two owners with labels plus both harness and
  unrelated `swebench/` images. Cleanup for one owner must leave every other
  resource untouched.
- **Minimal suggested fix:** label all resources with repository/run ownership,
  filter by those labels, narrow the image namespace, and make force removal an
  explicit operation.

### F15 — Critical disk pressure does not stop new work

- **Severity:** Medium
- **Classification:** Confirmed production defect
- **Evidence:** `config.py:25` defines `max_storage_pct`, but runner calls the
  default check and only logs a warning at `runner.py:104-107`, including when
  `is_critical` is true.
- **Impact:** predictable ENOSPC conditions can corrupt or truncate run state.
- **Regression test:** return a critical storage result and assert no image pull
  or container start; verify the configured threshold is used.
- **Minimal suggested fix:** fail closed at the critical threshold unless the
  operator supplies an explicit override. Do not couple this to global cleanup.

## Missing verification

### V01 — Safety-named tests frequently simulate their conclusions

- **Severity:** High
- **Classification:** Missing verification
- **Evidence:** signal handlers are installed inside tests at
  `tests/integration/test_signal_handling.py:21-74`; cleanup methods are called
  directly at `:80-145`; lock files are manually created/removed at `:151-173`.
  Timeout/error artifacts are manually created at
  `tests/integration/test_cleanup_hardening.py:118-153`; eval isolation manually
  invents a second predictions file at `:181-212`; resume reimplements the loop
  at `:218-245`. The runner fake ignores flags/command at
  `tests/integration/test_runner_mocked.py:30-33,78-95`. CLI tests exercise
  stale Bash-style `--command` forms and often accept any of exit codes 0, 1,
  or 2 at `tests/unit/test_cli.py:30-87` and
  `tests/integration/test_cli_integration.py:70-170`, so an unknown-option
  error can satisfy them without dispatching production behavior.
- **Impact:** a green suite can coexist with F01-F15.
- **Regression test:** replace these simulations with contract tests invoking
  Click, `Runner`, and `RunManager`, mocking only Docker/process boundaries.
- **Minimal suggested fix:** require every regression test to fail against the
  buggy production implementation it claims to guard.

### V02 — The integration suite is not hermetic

- **Severity:** High
- **Classification:** Missing verification
- **Evidence:** isolated execution collected 297 tests and passed all 140 unit
  tests. Of 147 integration tests selected with real-Docker E2E excluded, 137
  passed and 10 failed because `MockDockerOps` inherited real image inspection
  and pull behavior.
- **Impact:** the branch's reported 297-pass result is environment-dependent and
  was not reproduced by this audit; a nominally non-Docker gate may touch
  Docker or attempt a large image pull.
- **Regression test:** make all runner-boundary methods explicit on the fake and
  fail if any unapproved subprocess/Docker call occurs.
- **Minimal suggested fix:** define deterministic unit/integration and opt-in
  Docker markers, then report pass/fail/skip counts separately in CI.

### V03 — No automated merge gate exists

- **Severity:** High
- **Classification:** Missing verification
- **Evidence:** commit `3853fe0` contains no `.github/workflows` entry and the PR
  head reports no status checks.
- **Impact:** collection failures, environment-dependent tests, packaging drift,
  and regressions are not blocked before merge.
- **Regression test:** validate workflow syntax and require protected checks for
  collection, unit, deterministic integration, and packaging; report optional
  Docker separately.
- **Minimal suggested fix:** add a CI workflow only after the deterministic gate
  is made hermetic.

## Documentation mismatches

### D01 — “Complete replacement” and safety claims exceed the implementation

- **Severity:** High
- **Classification:** Documentation mismatch
- **Evidence:** PR #7 describes a complete rewrite replacing `run.sh`, while
  the full Bash orchestrator remains and does not delegate to Python. The
  original branch TODO marks immutable attempts, resume, artifact preservation,
  signal handling, and eval isolation complete despite F04-F12. The Python CLI
  docstring at `src/swebench_orchestrator/cli.py:1-8,52-65` advertises
  Bash-style `--run` forms although Click registers subcommands at
  `cli.py:40,177-245`. Original documentation also advertises
  `python -m swebench_orchestrator.cli`, but the package has neither
  `swebench_orchestrator/__main__.py` nor a module guard that calls `main()`.
- **Impact:** reviewers can mistake parallel scaffolding and simulated tests for
  operational parity.
- **Regression test:** compare README examples against generated Click help and
  keep audited capability status tied to production-path contract tests.
- **Minimal suggested fix:** describe Python as experimental and parallel until
  a deliberate migration decision and parity gate are complete.

### D02 — Packaging hygiene is unfinished

- **Severity:** Medium
- **Classification:** Documentation mismatch
- **Evidence:** `pyproject.toml:26-30` correctly declares the console entry point,
  but generated `src/swebench_orchestrator.egg-info/` is tracked. `.gitignore`
  omits `runs/`, `*.egg-info/`, `.pytest_cache/`, and coverage outputs. The
  harness installation path uses an unpinned `pip install swebench` at
  `src/swebench_orchestrator/cli.py:457-463`.
- **Impact:** generated state can enter commits and the evaluator contract can
  change independently of the orchestrator.
- **Regression test:** build/install from a clean archive, assert the console
  command works, and verify the tree remains clean after deterministic checks.
- **Minimal suggested fix:** separate generated metadata from source, define an
  ignore policy, and record a supported SWE-bench dependency/version strategy.

## Design recommendations

These are recommendations rather than additional defect claims:

1. Make `Runner` the sole application service and keep Click as a thin adapter;
   remove duplicated eval and lifecycle logic.
2. Treat one immutable run as the provenance root, immutable attempts as the
   artifact roots, and any flat output as a derived `latest` view.
3. Use per-run leases and Docker ownership labels so unrelated agents/runs may
   proceed concurrently without global cleanup.
4. Decide explicitly whether `run.sh` is supported legacy behavior, a thin
   Python launcher, or scheduled for removal after parity validation.
5. Split deterministic CI from opt-in real-Docker and model-backed canaries.

## Suspicions narrowed or disproved

- Manifest-side `cleanup_partial_attempts` is scoped to recognized
  `runs/<run>/tasks/<instance>/attempt-*` directories at
  `manifest.py:378-408`. The agent-root deletion is the separate Click loop.
- Bind-mounted data is not inherently deleted with the container; F04 concerns
  placement, normalization, and attribution.
- `generate_predictions` in `runner.py` uses valid `json.dumps`; invalid JSON is
  specific to the duplicate public Click eval path.
- UUID-derived run IDs are ordinarily distinct. Deterministic container names,
  attempt IDs, and eval namespaces are the confirmed collision surfaces.
- `cleanup_stopped_containers` is broad but has no production caller at this
  commit, so it is not listed as an active CLI defect.

## External contracts checked

- [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/)
  define host source/container destination semantics and warn that bind mounts
  can modify host data.
- [Docker `cp`](https://docs.docker.com/reference/cli/docker/container/cp/)
  supports running or stopped containers, so collection can occur before
  removal.
- [Click commands and groups](https://click.palletsprojects.com/en/stable/commands-and-groups/)
  confirm that registered commands are invoked as subcommands and that their
  options follow the subcommand.
- [Python signal handling](https://docs.python.org/3/library/signal.html)
  documents main-thread handler execution and installation constraints.
- [SWE-bench's official evaluator](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/run_evaluation.py)
  defines `--instance_ids` as one or more space-separated arguments and uses
  `run_id` in evaluator log namespaces.
- [GitHub Actions workflows](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows)
  are discovered from `.github/workflows` in the associated commit.
- [PyPA console-script entry points](https://packaging.python.org/en/latest/specifications/entry-points/)
  support the `pyproject.toml` command declaration used by this package.
