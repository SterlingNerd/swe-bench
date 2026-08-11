# Main Synchronization Review: `aa0644f..215f6d7`

## Scope and verdict

This document records the 2026-08-10 import of upstream `main` into the
documentation-only `gage/refactor-python-doc-audit` side branch. The previous
imported base was `aa0644f362015b4d8f482388daf70abec19c3f80`; the fetched head
was `215f6d7d41c14fe786e25d8d335e6b4918b5b3c9`. The range contains 17 commits
(16 on the first-parent path plus the wrapper commit merged at `a970b24`) and
changes 45 files with 3,596 insertions and 6,668 deletions.

The side branch was backed up before rebasing. Upstream's deletions of the old
`TODO.md` and `TESTPLAN.md` were preserved, and no runtime change is introduced
by this review.

**Verdict:** upstream fixes the original duplicate-image and doubled-output-path
blockers and adds useful lifecycle, logging, configuration, status, and test
work. It is not yet accurate to describe the deterministic suite as green or
the original audit as fully resolved. The non-Docker gate has 23 failures, and
several safety and provenance findings remain open.

The historical [PR #7 audit](pr7-refactor-python-3853fe0.md) remains immutable
and applies only to commit `3853fe0`.

## Imported changes

| Area | Upstream changes in this range |
|---|---|
| Runtime migration | Removed the legacy Bash orchestrator and its Bash tests, then added a thin `run.sh` virtual-environment wrapper. |
| Runner command and paths | Removed the duplicate image token; made bundle/output/evaluator paths absolute; changed the output mount; runs the container as the invoking UID/GID; revised copy flattening and destination placement. |
| Lifecycle | Added core timeout validation, a CLI-wide `flock`, SIGINT/SIGTERM handlers, and a `run-all` wait/force-release safety net. |
| Configuration and logging | Added standard Python logging plus `MAX_STORAGE_PCT` and `SWEBENCH_REGISTRY` environment parsing; removed the image-cache feature. |
| Evaluation and status | Removed one redundant fold call and added standalone status generation/schema scripts with tests. |
| Tests | Removed roughly 4,540 lines of Bash tests; added signal, locking, status, runner, path, and copy tests. |
| Hygiene and docs | Added `runs/` to `.gitignore` and corrected the README's main quick-start examples to Click subcommands. |

## Validation

Validation used Python 3.12.3 in an isolated temporary virtual environment with
Click 8.4.2, Pydantic 2.13.4, pytest 9.1.1, pytest-mock 3.15.1, Docker 7.2.0,
GitPython 3.1.59, and python-dotenv 1.2.2. No real-Docker test was run.

```text
collection:                       413 tests
unit:                             234 passed
non-Docker integration:           146 passed, 23 failed
deterministic non-Docker total:   380 passed, 23 failed
real-Docker integration:          10 not run
```

Commands:

```bash
PYTHONPATH=src /tmp/swebench-sync-venv/bin/pytest --collect-only -q
PYTHONPATH=src /tmp/swebench-sync-venv/bin/pytest -q tests/unit
PYTHONPATH=src /tmp/swebench-sync-venv/bin/pytest -q \
  --ignore=tests/integration/test_docker_e2e.py
```

Twenty failures exercise the runner copy path across
`test_e2e.py`, `test_run_all.py`, and `test_runner_mocked.py`. Commit `e134308`
stops pre-creating the instance directory, but the flattening code renames files
into that directory without ensuring it exists. The mocked `docker cp` creates
the temporary source but not the bind-mounted destination, so `Path.rename`
raises `FileNotFoundError`; dependent `run-all` assertions then also fail.

Two eval-folding tests exit before their intended assertions because the CLI
requires the configured SWE-bench Python executable to exist even though the
subprocess is mocked. The third eval-folding failure is order-dependent: an
earlier Click exit leaves the in-process CLI lock held, while that summarize
test passes alone.

These results do not establish whether the optional real-Docker path works. In
particular, the new exact-command tests validate selected argv components but
do not replace a production-path Docker canary.

## Historical finding disposition

| Finding | Status at `215f6d7` | Evidence |
|---|---|---|
| F01 duplicate image | Resolved | `runner.py` now begins the command with `/agent/entrypoint.sh`; `DockerOps` inserts the image once. A focused unit test asserts this. |
| F02 doubled agent output path | Resolved in command composition | The host output root is mounted at `/workspace/outputs`, while `SWE_OUTPUT_ROOT` adds the agent exactly once. Absolute-path tests cover selected flags. |
| F03 `cleanup-partial` can erase an agent tree | Open | `cli.py` still iterates immediate children of the global output root, tests for files at that level, and recursively removes the child. |
| F04 failure artifact finalization | Partial | The corrected bind mount places partial files at the canonical host path and the container runs as the host user. Timeout/error branches still remove the container before copy/finalization and do not attach artifacts to immutable attempts. |
| F05 empty output reported as success | Open | An empty copied directory can set `cp_ok=True`; missing or invalid `result.json` still defaults to `patch_collected`. One test explicitly expects the invalid-JSON fallback. |
| F06 concurrent run ownership | Partial | The Click entrypoint now takes a global lock, but direct `Runner` callers bypass it and deterministic container names still trigger unconditional stale-container removal. |
| F07 signal/exception finalization | Partial | Production installs SIGINT/SIGTERM handlers, but they stop every running `swe_*` container and exit without attempt-scoped artifact finalization. Unexpected exceptions still lack an outer runner lifecycle finalizer. |
| F08 ambiguous attempt result updates | Open | `update_attempt_result` still accepts no instance ID, selects the first matching `attempt-NNN`, and fabricates a fallback task path when none matches. |
| F09 immutable attempt allocation/artifacts | Open | Attempt IDs still use `len(existing)+1` with `exist_ok=True`; complete artifacts remain in the flat output tree instead of the attempt directory. |
| F10 batch identity, provenance, resume | Open | `run-all` still creates a new run per instance, provenance defaults empty, and resume skips on flat `result.json` existence. |
| F11 evaluation correctness/isolation | Partial | Predictions now use valid `json.dumps` and the redundant fold was removed. CLI and runner eval implementations remain duplicated; predictions, report directory, and `run_id` remain agent-scoped; instance IDs are still one comma-joined argument; report discovery can select the newest unrelated JSON. |
| F12 atomic state updates | Open | `write_json` still truncates and rewrites final paths directly without serialization. |
| F13 event export | Open | `export_events` still calls undefined module-level `list_attempts`. |
| F14 Docker ownership scope | Open | Cleanup and signal handling target global `swe_*` names; image pruning targets every `swebench/` repository; `run-all` waits for or force-releases every matching agent container. No ownership labels are used. |
| F15 critical storage behavior | Partial | The configured threshold now reaches `check_storage`, but even critical usage only logs a warning and does not stop new work. |

## Verification and documentation status

- The former mocked-runner Docker leak was corrected by explicit image methods,
  but the deterministic suite now fails for the copy destination and eval/lock
  reasons above.
- Seven focused runner command/path tests are valuable additions, while several
  safety-named tests still simulate their conclusions instead of driving the
  responsible production path.
- No `.github/workflows` merge gate exists at this commit.
- `runs/` is now ignored. Tracked `*.egg-info`, `.pytest_cache`, coverage output,
  and the unpinned SWE-bench install remain unresolved hygiene/reproducibility
  items.
- README quick-start commands use Click subcommands, but the CLI's own help text,
  several CLI error messages, the `run.sh` wrapper comments, and the imported
  `agents/agents.md` still used obsolete option-style command names before this
  side-branch documentation correction.
- The imported README described a Codex adapter that is not present, claimed no
  output bind mount, listed obsolete 8/16 GB limits and a read-only root flag,
  and understated cleanup scope. The side branch corrects those statements.

## Next merge gates

1. Repair the copy destination contract and make all 403 non-Docker tests pass
   from a clean environment.
2. Add a focused regression proving `cleanup-partial` cannot remove an agent
   root; make discovery dry-run by default before enabling deletion.
3. Require run, instance, and attempt identity for state updates, then store
   immutable artifacts under the attempt.
4. Consolidate evaluation behind one service with unique run/eval namespaces,
   separate instance arguments, and exact report selection.
5. Scope Docker resources with ownership labels and finalize only the active
   run on signals, timeout, errors, and exceptions.
6. Add a deterministic CI gate and report the real-Docker canary separately.
