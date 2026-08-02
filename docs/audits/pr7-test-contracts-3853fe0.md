# PR #7 Production Contract and Transition Findings

Scope: suggestion-only review at commit 3853fe0e344c5c3955e4a29d43ab5aeed2805a14. This branch starts at that commit and changes no production code.

Reproduced in a temporary git archive with Python 3.12.3: 297 collected (140 unit, 157 integration); 140 unit passed; with 10 explicit real-Docker tests excluded, integration was 137 passed and 10 failed. Real-Docker tests were not run. PR head had no workflow or reported checks.

## T01: mocked integration reaches real Docker

- Severity: High
- Classification: Missing verification
- Evidence: all 10 selected failures are tests/integration/test_runner_mocked.py. MockDockerOps overrides execution/copy but inherits real image_exists/pull_image, failing at Docker before intended assertions.
- Impact: the 297-pass claim is environment-dependent and a non-Docker gate can inspect Docker or attempt a pull without verifying Runner.
- Regression: make every unapproved Docker/subprocess call fail immediately and pass the deterministic suite with Docker unavailable.
- Minimal fix: explicitly fake every Runner boundary and report deterministic and opt-in Docker gates separately.

## T02: safety tests simulate their conclusions

- Severity: High
- Classification: Missing verification
- Evidence: test_signal_handling.py:21-74 installs handlers in tests, :80-145 directly calls cleanup, and :151-173 manually creates locks. test_cleanup_hardening.py:118-153 creates artifacts, :181-212 invents eval isolation, and :218-245 reimplements resume. test_runner_mocked.py:30-33,78-95 ignores command details.
- Impact: green tests coexist with missing production locks/signals, destructive cleanup, artifact loss, and eval collision defects.
- Regression: invoke Click, Runner, or RunManager for every guarantee while mocking only the process/Docker boundary; each test must fail at 3853fe0.
- Minimal fix: replace outcome simulations with production-path contract tests and name the responsible production function in each test.

## T03: CLI tests accept parser failures and stale syntax

- Severity: High
- Classification: Missing verification and documentation mismatch
- Evidence: cli.py:1-8,52-65 and tests advertise Bash-style --run although Click defines subcommands at :40,177-245. test_cli.py:30-87 and test_cli_integration.py:70-170 often accept 0, 1, or 2, so unknown options pass. The documented python -m path has no package __main__.py or module guard invoking main().
- Impact: examples/tests appear valid without dispatching production behavior.
- Regression: derive commands from Click help, require exact exit/output/side effects, and invoke the installed console entry point from a clean wheel.
- Minimal fix: keep Click thin, remove broad exit-code assertions, and document/test only supported syntax.

## T04: no automated merge gate

- Severity: High
- Classification: Missing verification
- Evidence: commit 3853fe0 has no .github/workflows entry and PR #7 reports no checks.
- Impact: collection, packaging, environment leaks, and regressions are not blocked before merge.
- Regression: validate workflow syntax and require collection, unit, deterministic integration, and build/install checks; report Docker separately.
- Minimal fix: add CI after the deterministic gate is hermetic and protect the required checks.

## T05: packaging and dependency hygiene are incomplete

- Severity: Medium
- Classification: Documentation mismatch
- Evidence: pyproject.toml:26-30 declares the console script, but src/swebench_orchestrator.egg-info is tracked; .gitignore omits runs/, *.egg-info/, .pytest_cache/, and coverage; cli.py:457-463 installs unpinned swebench.
- Impact: generated state can enter commits and evaluator behavior can drift independently.
- Regression: build/install from a clean archive, invoke the console command, run deterministic tests, and require a clean tree.
- Minimal fix: stop tracking generated metadata, ignore runtime/build outputs, and define a supported SWE-bench version strategy.

## T06: Python is not yet a run.sh replacement

- Severity: High
- Classification: Documentation mismatch
- Evidence: the full run.sh remains and does not delegate to Python; only agents/pi exists; Docker argv, output, cleanup, lifecycle, manifest, and eval defects prevent parity at this anchor.
- Impact: a complete-rewrite label can trigger premature migration away from the established baseline.
- Regression: publish a Bash/Python parity matrix for identical fixtures covering output paths, timeout/error artifacts, cleanup, resume, manifests, and eval.
- Minimal fix: label Python experimental, keep run.sh as legacy baseline, and define a compatibility/deprecation gate before changing defaults.

## T07: production contract matrix is absent

- Severity: High
- Classification: Design recommendation
- Evidence: no suite jointly asserts exact Docker argv/mounts, cleanup containment, failure artifacts, lock/signal behavior, one batch manifest, immutable attempts, isolated eval, and clean packaging via production entry points.
- Impact: cross-module regressions remain green behind helper-level tests.
- Regression: add one production-path failing test per confirmed defect: duplicate image, doubled path, agent-root cleanup, timeout/nonzero artifacts, empty copy, contention, signals, attempt ambiguity/overwrite, batch/resume provenance, eval JSON/argv/isolation, atomic JSON, and event export.
- Minimal fix: mock only external boundaries and reserve a small opt-in real-Docker canary for the final Runner path.

Suggested gates: collection/unit; hermetic integration with Docker unavailable; clean wheel/Click smoke; defect regressions; opt-in Docker smoke; published Bash/Python transition matrix.

Official contracts:
- https://click.palletsprojects.com/en/stable/commands-and-groups/
- https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows
- https://packaging.python.org/en/latest/specifications/entry-points/
- https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/run_evaluation.py
