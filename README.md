# SWE-bench Agent Harness

Run self-contained coding-agent bundles against **SWE-bench Verified** tasks,
collect patches, and evaluate them with the official SWE-bench harness. The
included `pi` adapter uses the same local llama.cpp model so results can be
compared across agents without sharing output state.

## Architecture

```text
swe-bench/
├── src/swebench_orchestrator/   # Python CLI & orchestrator logic
│   ├── cli.py                   # Click-based command interface
│   ├── runner.py                # Container orchestration
│   ├── docker_ops.py            # Docker lifecycle management
│   ├── logging_config.py        # Console + file logging (Issue #11)
│   └── ...
├── harnesses/
│   ├── pi/                      # Pi CLI, local-provider config, entrypoint
│   └── codex/                   # Codex CLI, local-provider config, entrypoint
└── workspace/outputs/
    ├── pi/<instance_id>/        # Pi artifacts
    └── codex/<instance_id>/     # Codex artifacts
```

Each agent is built as a relocatable bundle under `harnesses/<agent>/bundle/`.
The orchestrator mounts the selected bundle read-only at `/agent` in the official
per-instance SWE-bench image. The image's repository is already checked out at
`/testbed`; the agent edits it and the entrypoint extracts a staged binary diff.

## Prerequisites

- Docker Desktop using the WSL 2 engine, with this Ubuntu distribution enabled
  under **Settings > Resources > WSL Integration**.
- A local OpenAI-compatible model server reachable from containers at
  `http://host.docker.internal:11434/v1`.
- The configured model id is `qwen3.6-35b-a3b`, with the intentionally fake
  bearer token `local-key`.

Verify Docker from the same WSL shell before running the harness:

```bash
docker version
docker run --rm hello-world
```

If `/usr/bin/docker` starts returning an I/O error even though integration was
already enabled, quit Docker Desktop, run `wsl --shutdown` in Windows
PowerShell, reopen Docker Desktop, wait for it to report that the engine is
running, then reopen Ubuntu and repeat the two Docker checks above.

## Quick Start

Install dependencies and index the 500 verified instances:

```bash
pip install -e ".[dev]"
./run.sh index
```

Build both agent bundles:

```bash
./run.sh build
```

Build only one agent, or force a fresh rebuild:

```bash
./run.sh build codex
./run.sh rebuild pi
```

Run either agent on the same instance:

```bash
./run.sh run pi django__django-7530
./run.sh run codex django__django-7530
```

Install the swebench harness (needed for evaluation):

```bash
./run.sh init
```

Use `./run.sh --help` for the complete command list.

## Running Benchmarks

The recommended workflow runs each instance through **work → eval → cleanup** in sequence:

```bash
# Run all instances, interleaving work and eval per instance
./run.sh run-all pi --timeout 3600
```

This does three phases per instance:
1. **Work** — runs the agent container, collects the patch
2. **Eval** — runs the swebench harness on that single instance's patch
3. **Cleanup** — removes the instance's Docker image to free disk

Each phase completes before the next instance starts, so disk usage stays bounded.

### Resuming interrupted runs

`--resume` skips instances that already have a `result.json`:

```bash
./run.sh run-all pi --timeout 3600 --resume
```

### Checking progress

```bash
# Per-agent status with emoji indicators
./run.sh status pi

# All agents at once
./run.sh status

# Detailed summary table
./run.sh summarize pi
```

Status symbols: ✓ resolved, ✗ failed, — no patch, ⌛ timed out, ! error, ? unknown.

### Batch eval (legacy)

`--eval` runs the harness on **all** collected patches at once. This is the old
approach — it requires more disk (all images pulled simultaneously) and doesn't
interleave work with eval. Prefer `run-all` for new runs. Use `--eval` only to
evaluate patches from a run that didn't include eval:

```bash
./run.sh eval pi
```

### Single-instance run (no interleaved eval)

`--run` executes the agent but does **not** run evaluation. Use this when you
want to collect patches first and evaluate later:

```bash
./run.sh run pi django__django-7530
```

## Output Contract

```text
workspace/outputs/<agent>/<instance_id>/
├── meta.json                 # Instance, agent, repository, and base commit
├── problem_statement.txt     # Original SWE-bench issue
├── agent_output.txt          # Agent's final/plain output
├── pi-sessions/              # Pi session state (Pi only)
├── patch.diff                # Binary-safe staged diff, including new files
├── result.json               # Run status, timings, exit codes, evaluation
└── eval/                     # Per-instance evaluation artifacts
```

Possible pre-evaluation statuses include `patch_collected`, `no_patch`,
`agent_error`, `container_error`, and `timed_out`. `--eval` adds `local_eval`
and promotes the status to `resolved`, `failed`, or `error` while preserving
the original agent metadata.

Aggregate files such as `predictions.jsonl`, `summary.json`, and evaluator
reports stay inside `workspace/outputs/<agent>/`. This prevents a Pi run from
being mistaken for, overwritten by, or evaluated as a Codex run.

## Container Runtime

Each instance has a pre-built swebench image:
```
swebench/sweb.eval.x86_64.django_1776_django-7530:latest
```

The orchestrator spins up that image with:
1. Agent bundle mounted read-only at `/agent`
2. Outputs written to internal `/workspace/outputs/<agent>/<instance_id>/`
3. Cached repos in `/tmp/repos` (tmpfs, ephemeral)
4. Calls `/agent/entrypoint.sh` as the container command

After the container exits, the orchestrator uses `docker cp` to copy outputs out to
the host. This avoids uid/gid permission issues — no bind mount for outputs,
no `chmod` workarounds needed. If a container dies too violently for `docker cp`
(e.g., OOM kill), the output is lost but the work is re-runnable.

## Security Hardening

Containers are intentionally locked down:
- **Dropped all capabilities** — no extra caps added
- **No new privileges** — `no-new-privileges:true`
- **Read-only root filesystem** — `--read-only`
- **Memory limit** — 8 GB RAM + 16 GB swap, 500 PID limit
- **tmpfs mounts** — `/tmp` is tmpfs with `noexec,nosuid`

## Registry Mirror (Recommended)

Setting up a Docker registry mirror (e.g., [Nerdctl](https://nerdctl.dev/) or
[Harbor](https://goharbor.io/)) is strongly encouraged to avoid Docker Hub
rate limits and reduce excessive image downloads. Each SWE-bench instance uses
a unique image, so a mirror saves both bandwidth and CI time.

Configuring a registry mirror is out of scope for this project — it's a
host/Docker configuration concern. See your container runtime's documentation
for setup instructions.

## Cleanup

```bash
./run.sh cleanup
```

Removes only containers named `swe_*` and images whose repository begins with
`swebench/sweb.`. Does not prune or remove unrelated Docker resources.

## Logging

The orchestrator uses Python's standard `logging` module with both console (stderr)
and file handlers. Logs are written to `workspace/run.log` by default.

```bash
# Verbose output (DEBUG level)
./run.sh -v run pi django__django-7530

# Check the log file
cat workspace/run.log
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SWE_WORKSPACE_DIR` | `<repo>/workspace` | Base directory for outputs and logs |
| `MAX_STORAGE_PCT` | `80` | Disk usage warning threshold (percentage) |
| `SWEBENCH_REGISTRY` | `swebench` | Docker registry prefix for swebench images |

### LlamaCPP / Local Model
- **Endpoint:** `http://host.docker.internal:11434/v1` (from inside Docker)
- **API Key:** `local-key` — bogus/fake key, safe to publish

## Git Remote
- **origin:** https://github.com/SterlingNerd/swe-bench.git
