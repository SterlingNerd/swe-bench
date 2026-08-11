# SWE-bench Agent Harness

Run self-contained coding-agent bundles against **SWE-bench Verified** tasks,
collect patches, and evaluate them with the official SWE-bench harness. The
current tree includes the `pi` adapter; other adapters require a separate,
reviewed port.

> [!IMPORTANT]
> The documentation-branch review at upstream commit `215f6d7` found 23
> failures in the 403-test non-Docker gate. Read the
> [current synchronization review](docs/audits/main-sync-aa0644f-215f6d7.md)
> before relying on cleanup, evaluation isolation, run manifests, or the
> claimed all-green test status. The earlier
> [PR #7 audit](docs/audits/pr7-refactor-python-3853fe0.md) is an immutable
> historical baseline, not a description of the current tree.

## Architecture

```text
swe-bench/
├── src/swebench_orchestrator/   # Python CLI & orchestrator logic
│   ├── cli.py                   # Click-based command interface
│   ├── runner.py                # Container orchestration
│   ├── docker_ops.py            # Docker lifecycle management
│   ├── logging_config.py        # Console + file logging (Issue #11)
│   └── ...
├── agents/
│   └── pi/                      # Pi CLI, local-provider config, entrypoint
└── workspace/outputs/
    └── pi/<instance_id>/        # Pi artifacts
```

Each agent is built as a relocatable bundle under `agents/<agent>/bundle/`.
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
swebench-orchestrator index
```

Build all available agent bundles:

```bash
swebench-orchestrator build
```

Build the Pi adapter, or force a fresh rebuild:

```bash
swebench-orchestrator build pi
swebench-orchestrator rebuild pi
```

Run Pi on one instance:

```bash
swebench-orchestrator run pi django__django-7530
```

Run the full dataset with an enforced per-instance timeout. `--resume` skips
only existing results for the selected agent:

```bash
swebench-orchestrator run-all pi --timeout 3600 --resume
```

Install and invoke the official evaluator, then compare summaries:

```bash
swebench-orchestrator init
swebench-orchestrator eval pi
swebench-orchestrator summarize
swebench-orchestrator status
```

Use `swebench-orchestrator --help` for the complete command and environment-variable list.

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
`agent_error`, `container_error`, and `timed_out`. `eval` adds `local_eval`
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

1. The agent bundle mounted read-only at `/agent`.
2. The host output root bind-mounted at `/workspace/outputs` and
   `SWE_OUTPUT_ROOT=/workspace/outputs/<agent>`.
3. The container process running as the invoking host UID and GID.
4. `/tmp` as an ephemeral tmpfs.
5. `/agent/entrypoint.sh` as the container command.

The entrypoint appends `<instance_id>`, so writes land directly at
`workspace/outputs/<agent>/<instance_id>/` through the bind mount. After a
successful exit, the runner also uses `docker cp` through a temporary directory
and flattens the copied files into that same canonical instance directory.
Timeout and container-error paths rely on the bind mount and do not run that
copy/normalization step.

## Security Hardening

Containers are intentionally locked down:

- **Dropped all capabilities** — no extra caps added
- **No new privileges** — `no-new-privileges:true`
- **Host identity** — runs as the invoking UID and GID
- **Memory limit** — 32 GB RAM + 64 GB memory-plus-swap, 500 PID limit
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

Do not run `swebench-orchestrator cleanup` while another checkout or operator
uses the same Docker daemon. It removes every container whose name matches
`swe_*` and every image whose repository begins with `swebench/`; resources do
not carry checkout or run ownership labels.

Do not run `swebench-orchestrator cleanup-partial`. Its flat-output traversal
examines immediate children of `workspace/outputs/` as though they were
instance directories and can recursively remove a complete agent output tree.

## Logging

The orchestrator uses Python's standard `logging` module with both console (stderr)
and file handlers. Logs are written to `workspace/run.log` by default.

```bash
# Verbose output (DEBUG level)
swebench-orchestrator -v run pi django__django-7530

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

## Review References

- [Main synchronization review at `215f6d7`](docs/audits/main-sync-aa0644f-215f6d7.md)
- [Historical PR #7 audit at `3853fe0`](docs/audits/pr7-refactor-python-3853fe0.md)

## Git Remote
- **origin:** https://github.com/SterlingNerd/swe-bench.git
