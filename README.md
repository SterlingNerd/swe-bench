# SWE-bench Agent Harness

Run self-contained coding-agent bundles against **SWE-bench Verified** tasks,
collect patches, and evaluate them with the official SWE-bench harness.

> [!IMPORTANT]
> `refactor-python` is an experimental rewrite branch. The Python package is
> present alongside the legacy Bash orchestrator; `run.sh` has not been replaced
> by, and does not delegate to, the Python CLI. Treat the Python path as under
> review until the blockers in [TODO.md](TODO.md) are resolved.

## Safety status

- Do **not** run either implementation's `cleanup-partial` command. The current
  directory traversal can remove an entire agent output tree.
- Do not run global cleanup while another harness process is active. Cleanup
  targets shared `swe_*` containers and SWE-bench images.
- Python timeout and container-error paths do not yet guarantee that partial
  container artifacts are copied before removal.
- The Python runner's output mount and `SWE_OUTPUT_ROOT` currently disagree,
  producing a duplicated agent path. Do not rely on a model-backed Python run
  until that contract is fixed and covered by an exact-arguments test.

These warnings describe the current branch; they are not usage recommendations.

## What is implemented

```text
swe-bench/
├── run.sh                         # Legacy Bash orchestrator (still active)
├── pyproject.toml                 # Python package and CLI entry point
├── src/swebench_orchestrator/
│   ├── cli.py                     # Click command group
│   ├── config.py                  # Immutable configuration
│   ├── models.py                  # Pydantic data models
│   ├── dataset.py                 # Dataset cache and Hugging Face loading
│   ├── bundles.py                 # Agent discovery and bundle builds
│   ├── docker_ops.py              # Docker subprocess boundary
│   ├── manifest.py                # Run and attempt metadata
│   ├── runner.py                  # Run, eval, and summary orchestration
│   └── storage.py                 # Disk and Docker cleanup helpers
├── agents/
│   └── pi/                        # Only agent adapter on this branch
├── tests/
│   ├── unit/                      # 140 Python test functions
│   ├── integration/               # 157 Python test functions
│   └── t*.sh                      # Legacy Bash behavior suites
├── workspace/outputs/             # Flat working/evaluation artifacts
└── runs/                          # Python manifest and attempt metadata
```

The latest branch commit reports 297 Python tests passing (140 unit and 157
integration). This documentation update confirms the static test inventory but
does not re-run it. The branch currently has no CI workflow, so passing status
must be re-established in a controlled environment before merge.

## Entrypoints

### Legacy Bash

The established interface remains flag-based:

```bash
./run.sh --help
./run.sh --index
./run.sh --build pi
./run.sh --run pi django__django-7530
```

### Experimental Python CLI

The Python interface uses Click **subcommands**, not Bash-style command flags.
For an isolated development installation:

```bash
python3 -m venv .venv/orchestrator
.venv/orchestrator/bin/pip install -e '.[dev]'
.venv/orchestrator/bin/swebench-orchestrator --help
```

Representative syntax:

```bash
swebench-orchestrator index
swebench-orchestrator list django
swebench-orchestrator build pi
swebench-orchestrator rebuild pi
swebench-orchestrator run pi django__django-7530 --timeout 3600
swebench-orchestrator run-all pi --timeout 3600 --resume
swebench-orchestrator eval pi
swebench-orchestrator summarize pi
swebench-orchestrator status pi
swebench-orchestrator interactive pi django__django-7530
```

The CLI also exposes `init`, `cleanup`, and `cleanup-partial`. The cleanup
commands are intentionally omitted from the examples because their safety and
concurrency contracts require correction and direct contract tests.

## Current agent support

Only the `pi` adapter is present on this branch. Documentation or examples that
refer to an `agents/codex/` directory are stale. A Codex adapter exists only in
the separate archived development history and must be ported selectively after
the Python agent and output contracts stabilize; that history must not be
merged wholesale into this branch.

See [agents/agents.md](agents/agents.md) for the source/bundle contract.

## State and output layouts

Two layouts currently coexist:

1. `workspace/outputs/<agent>/<instance_id>/` is the flat operational layout
   used for patches, results, summaries, predictions, and evaluation reports.
2. `runs/<run_id>/tasks/<instance_id>/attempt-NNN/` records Python run and
   attempt metadata.

The attempt tree does not yet own a complete immutable copy of each attempt's
artifacts. Reruns can still update the flat output directory. Until artifact
isolation is implemented, manifests should be treated as metadata rather than
as a complete provenance store.

The intended per-instance output contract includes:

```text
patch.diff
result.json
meta.json
agent_output.txt
problem_statement.txt
```

Agent-specific session and evaluation directories may also be present.

## Configuration and runtime limits

The Python implementation requires Python 3.10 or newer. Its declared runtime
dependencies are Click, datasets, docker, GitPython, Pydantic, and
python-dotenv.

`SWE_WORKSPACE_DIR` is the environment override currently read by `Config`.
Other settings are constructor/default values until environment parsing is
implemented and tested.

The current Python Docker command requests:

- 32 GB memory and 64 GB memory-plus-swap
- 500 PID limit
- a 2 GB `/tmp` tmpfs with `noexec,nosuid`
- all Linux capabilities dropped
- `no-new-privileges:true`

These are implementation facts, not yet a validated portability profile.

## Registry helper

`push.sh` is not a Git helper. It builds matching local Docker images for
`linux/amd64` and pushes them to `docker-registry.sterling.digital`. Running it
changes external registry state and requires explicit operator intent.

## Review references

- [TODO.md](TODO.md) — audited status and blockers
- [TESTPLAN.md](TESTPLAN.md) — Python contract gates and legacy Bash baseline
- [agents/agents.md](agents/agents.md) — agent source and bundle contract
