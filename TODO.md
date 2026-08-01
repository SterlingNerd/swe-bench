# TODO: SWE-bench Orchestrator Python Rewrite

## Audited status

**State:** experimental implementation present; integration, safety, and
provenance contracts are not yet merge-ready.

This status reflects static review of `refactor-python` at `3853fe0`. The latest
commit reports 297 Python tests passing. The repository contains 140 unit and
157 integration test functions, but this documentation-only pass did not run
them and the branch has no CI workflow.

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
| Python tests | 297 functions | Latest commit says passing; not re-run here |

## P0: safety and data-integrity blockers

- [ ] **Fix `cleanup-partial` scope.** It currently examines agent directories
  as though they were instance directories and can remove a complete agent
  output tree. Correct both the Python and legacy Bash paths before documenting
  either command as safe.
- [ ] **Fix the Python output mount contract.** Mount and
  `SWE_OUTPUT_ROOT` currently add the agent component at different layers,
  creating a doubled-agent path.
- [ ] **Preserve partial artifacts on timeout and container error.** Copy or
  retain container outputs before removal, then record the host result without
  overwriting useful agent metadata.
- [ ] **Implement actual process locking.** `Config.lock_file` exists, but the
  Python command path does not acquire it.
- [ ] **Implement production signal handling.** Current signal tests install
  handlers inside the tests; the CLI/runner does not install equivalent
  handlers or scope cleanup to its active container.
- [ ] **Scope Docker cleanup for concurrency.** Global `swe_*` cleanup can
  interfere with another harness process.

## P1: run provenance and correctness

- [ ] Store each attempt's patch, result, logs, and agent output inside its
  immutable attempt directory, or explicitly redesign manifests as metadata
  indexes over immutable artifact locations.
- [ ] Make one `run-all` invocation create one run manifest and pass that run ID
  through every instance attempt.
- [ ] Make evaluation identifiers, predictions, and reports unique per run;
  do not use the fixed agent name as the only `run_id`.
- [ ] Define resume semantics against run/attempt state rather than only the
  existence of a flat `result.json`.
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
- [ ] Run the complete 297-test Python suite in a clean environment and record
  the command, dependency versions, platform, and result.
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
2. Timeout/error canaries prove partial artifacts survive.
3. Batch execution proves a single run owns isolated, immutable attempts.
4. Evaluation proves predictions and reports cannot collide across runs.
5. The Python test suite passes in CI and the real-Docker subset is reported
   separately.
6. Documentation examples are generated or checked against Click help.
7. Josh reviews the rewrite and the integration target before any PR is moved
   from draft or retargeted to `main`.

## Documentation-only branch scope

The `gage/refactor-python-doc-audit` branch updates repository documentation
only. Runtime fixes, test rewrites, ignore changes, generated-file removal, and
agent ports belong in separate follow-up branches after review.
