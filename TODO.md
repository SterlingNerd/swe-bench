# TODO: SWE-bench Orchestrator Python Rewrite

## Audited status

**State:** experimental implementation present; integration, safety, and
provenance contracts are not yet merge-ready.

This status reflects a source and isolated-test audit of `refactor-python` at
`3853fe0`. Exactly 297 tests collected: all 140 unit tests passed. With the ten
real-Docker tests excluded, integration produced 137 passes and 10 failures
because the mocked runner tests still reached real Docker image operations.
The branch has no CI workflow or reported status checks, so the 297-pass claim
was not reproduced. See the [full audit](docs/audits/pr7-refactor-python-3853fe0.md).

The Python package currently exists alongside the full legacy `run.sh`. It is a
parallel implementation, not yet the script's backing implementation. Only the
`pi` agent adapter exists on this branch.

## Implemented surface

| Area | Present | Audit status |
|---|---:|---|
| Pydantic models and immutable configuration | Yes | Unit-covered; environment mapping is incomplete |
| Dataset cache and Hugging Face loading | Yes | Unit/integration coverage present |
| Agent discovery and bundle building | Yes | Pi-only on this branch |
| Docker lifecycle abstraction | Yes | Mock and optional Docker coverage present |
| Run and attempt manifests | Yes | Metadata exists; artifacts remain in the flat layout |
| Single and batch execution | Yes | Runtime contracts below need correction |
| Prediction generation and local evaluation | Yes | Eval identifiers and outputs are not isolated by run |
| Summary and status reporting | Yes | Reads the flat output layout |
| Click CLI | Yes | Uses subcommands; not wired through `run.sh` |
| Python tests | 297 collected | Unit: 140 passed; non-Docker integration: 137 passed, 10 failed; 10 Docker tests not run |

## P0: safety and data-integrity blockers

- [ ] **Fix the duplicated Docker image argument.** `Runner` currently emits
  `docker run ... IMAGE IMAGE /agent/entrypoint.sh ...`, so the work path does
  not start the agent.
- [ ] **Fix `cleanup-partial` scope.** It currently examines agent directories
  as though they were instance directories and can remove a complete agent
  output tree. Correct both the Python and legacy Bash paths before documenting
  either command as safe.
- [ ] **Fix the shared output mount contract.** In both Python and legacy Bash,
  the host mount and `SWE_OUTPUT_ROOT` add the agent component at different
  layers, creating a doubled-agent path.
- [ ] **Preserve partial artifacts on timeout and container error.** Copy or
  retain container outputs before removal, then record the host result without
  overwriting useful agent metadata.
- [ ] **Implement actual process locking.** `Config.lock_file` exists, but the
  Python command path does not acquire it. A second same-agent/instance run can
  force-remove the first process's active container.
- [ ] **Implement production signal handling.** Current signal tests install
  handlers inside the tests; the CLI/runner does not install equivalent
  handlers or scope cleanup to its active container.
- [ ] **Scope Docker cleanup for concurrency.** Global `swe_*` cleanup can
  interfere with another harness process.
- [ ] **Fix the public eval path.** It hand-builds invalid JSONL, bypasses result
  folding, reuses fixed agent-only namespaces, and passes multiple evaluator
  instance IDs as one comma-joined argument.
- [ ] **Reject empty copied output as success.** Missing `result.json` must not
  default to `patch_collected`.

## P1: run provenance and correctness

- [ ] Store each attempt's patch, result, logs, and agent output inside its
  immutable attempt directory, or explicitly redesign manifests as metadata
  indexes over immutable artifact locations.
- [ ] Make one `run-all` invocation create one run manifest and pass that run ID
  through every instance attempt.
- [ ] Make attempt addressing unambiguous by requiring run, instance, and
  attempt identity. Remove the fallback that fabricates an unrelated task
  directory when an attempt is not found.
- [ ] Allocate attempt IDs atomically; do not use
  `len(existing_attempts) + 1` with `exist_ok=True`.
- [ ] Make evaluation identifiers, predictions, and reports unique per run;
  do not use the fixed agent name as the only `run_id`.
- [ ] Define resume semantics against run/attempt state rather than only the
  existence of a flat `result.json`.
- [ ] Populate and validate dataset, orchestrator commit, bundle, profile, and
  effective runtime provenance when creating or resuming a run.
- [ ] Make JSON state publication atomic and serialize read-modify-write updates
  per run/attempt.
- [ ] Fix `export_events`; it currently calls an undefined module-level
  `list_attempts`.
- [ ] Add exact Docker-argument tests for output mounts, environment variables,
  security flags, and copy paths.
- [ ] Parse and test supported environment configuration consistently. At
  present, `SWE_WORKSPACE_DIR` is the only environment override applied by
  `Config`.
- [ ] Decide whether `run.sh` remains a supported legacy entrypoint, becomes a
  thin Python launcher, or is retired after parity validation.

## P2: validation and repository hygiene

- [ ] Add CI for collection, unit tests, integration tests, and static checks.
- [ ] Replace self-validating hardening tests with tests that invoke production
  cleanup, signal, lock, artifact, manifest, and eval code paths.
- [ ] Make the ten failing mocked runner integration tests hermetic, then run
  the complete 297-test Python suite in a clean environment and record the
  command, dependency versions, platform, and result.
- [ ] Run the optional real-Docker suite separately and record Docker/WSL
  versions and skipped tests.
- [ ] Add `runs/`, `*.egg-info/`, `.pytest_cache/`, and coverage outputs to the
  appropriate ignore policy; remove generated package metadata from source
  control in a separate code-hygiene change.
- [ ] Pin or lock development dependencies sufficiently for reproducible CI.
- [ ] Document `push.sh` as an external Docker-registry publishing action or
  rename it to make that effect explicit.

## P3: agent parity and selective Codex port

- [ ] Stabilize the generic agent bundle and output contracts using `pi`.
- [ ] Review the archived Codex adapter file by file; do not merge the archived
  branch or its 22-commit history into the rewrite.
- [ ] Port only compatible Codex source/configuration into a new
  `agents/codex/` implementation based on the stabilized Python contracts.
- [ ] Add discovery, bundle, entrypoint, artifact, and model-backed canary tests
  for Codex before advertising it in README examples.

## Required validation gates

1. P0 cleanup and output-path regressions have production-path tests.
2. The exact production Docker argv contains one image and starts the agent
   entrypoint.
3. Timeout/error canaries prove canonical partial artifacts survive.
4. Batch execution proves a single run owns isolated, immutable attempts and
   updates the intended instance.
5. Evaluation proves valid JSON, correct instance arguments, and non-colliding
   predictions/reports across runs.
6. The Python test suite passes in CI and the real-Docker subset is reported
   separately.
7. Documentation examples are generated or checked against Click help.
8. Josh reviews the rewrite and the integration target before any PR is moved
   from draft or retargeted to `main`.

## Documentation-only branch scope

The `gage/refactor-python-doc-audit` branch updates repository documentation
only. Runtime fixes, test rewrites, ignore changes, generated-file removal, and
agent ports belong in separate follow-up branches after review.
