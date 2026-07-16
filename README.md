# SWE-bench Agent Harness

Run self-contained coding-agent bundles against **SWE-bench Verified** tasks,
collect patches, and evaluate them with the official SWE-bench harness. The
included `pi` and `codex` adapters can be compared without sharing output
state.

## Architecture

```text
swe-bench/
├── run.sh                         # Build, run, evaluate, summarize
├── agents/
│   ├── pi/                        # Pi CLI, local-provider config, entrypoint
│   └── codex/                     # Codex CLI, local-provider config, entrypoint
├── tests/test_harness.sh          # Host-side harness contract tests
└── workspace/outputs/
    ├── pi/<instance_id>/          # Pi artifacts
    └── codex/<instance_id>/       # Codex artifacts
```

Each agent is built as a relocatable bundle under `agents/<agent>/bundle/`.
`run.sh` mounts the selected bundle read-only at `/agent` in the official
per-instance SWE-bench image. The image's repository is already checked out at
`/testbed`; the agent edits it and the entrypoint extracts a staged binary diff.

## Prerequisites

- Docker Desktop using the WSL 2 engine, with this Ubuntu distribution enabled
  under **Settings > Resources > WSL Integration**.
- A local OpenAI-compatible model server reachable from containers at
  `http://host.docker.internal:11434/v1`.
- For Codex, the server must implement the streaming Responses API at
  `POST /v1/responses`.
- The default model id is `qwen3.6-35b-a3b`, with the intentionally fake bearer
  token `local-key`.

Verify Docker and the model server before launching an agent:

```bash
docker version
docker run --rm hello-world
curl -fsS http://localhost:11434/v1/models
```

If `/usr/bin/docker` starts returning an I/O error even though integration was
already enabled, quit Docker Desktop, run `wsl --shutdown` in Windows
PowerShell, reopen Docker Desktop, wait for it to report that the engine is
running, then reopen Ubuntu and repeat the Docker checks above.

## Quick Start

Index the 500 verified instances and build both bundles:

```bash
./run.sh --index
./run.sh --build
```

Build only one agent, or force a fresh rebuild:

```bash
./run.sh --build codex
./run.sh --rebuild pi
```

Run either agent on the same instance:

```bash
./run.sh --run pi django__django-7530
./run.sh --run codex django__django-7530
```

Run the full dataset with an enforced per-instance timeout. `--resume` skips
only existing results for the selected agent:

```bash
./run.sh --run-all codex --timeout 3600 --resume
```

Install and invoke the official evaluator, then compare summaries:

```bash
./run.sh --init
./run.sh --eval pi
./run.sh --eval codex
./run.sh --summarize
./run.sh --status
```

Use `./run.sh --help` for the complete command and environment-variable list.

## Output Contract

```text
workspace/outputs/<agent>/<instance_id>/
├── meta.json                 # Instance, agent, repository, and base commit
├── problem_statement.txt     # Original SWE-bench issue
├── agent_output.txt          # Agent's final/plain output
├── agent_stderr.txt          # Codex diagnostics (Codex only)
├── trajectory.jsonl          # Codex JSONL event stream (Codex only)
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

```text
swebench/sweb.eval.x86_64.django_1776_django-7530:latest
```

`run.sh` spins up that image with:

1. Agent bundle mounted read-only at `/agent`
2. Outputs written to internal `/workspace/outputs/<agent>/<instance_id>/`
3. Cached repos in `/tmp/repos` (tmpfs, ephemeral)
4. Calls `/agent/entrypoint.sh` as the container command

After the container exits, `run.sh` uses `docker cp` to copy outputs out to
the host. This avoids uid/gid permission issues. If a container dies too
violently for `docker cp` (for example, an OOM kill), the output may be lost.

## Codex Adapter

`agents/codex/build_bundle.sh` downloads the official pinned Codex CLI package
for the current CPU architecture and verifies its SHA-256 digest before
extracting it. The bundle includes Codex, its sandbox helper, and ripgrep.

The entrypoint creates an ephemeral `CODEX_HOME`, renders the Responses API
provider from `SWE_CODEX_*` settings, runs `codex exec --ephemeral --json`, and
captures the final message plus the full JSONL trajectory. It never mounts or
copies host ChatGPT/OpenAI credentials into the benchmark container.

Codex's nested sandbox cannot create namespaces under the Docker policy, so the
CLI runs with its internal approvals and sandbox bypassed. The disposable
Docker container is the security boundary, matching Pi's execution model.
Avoid replacing the fake local token with host credentials or mounting private
host paths into these containers.

## Security Hardening

Runtime containers have an 8 GB memory limit, a 16 GB memory-plus-swap limit,
a 500 PID limit, all Linux capabilities dropped, and `no-new-privileges`
enabled. The agent bundle is read-only, while `/testbed`, `/workspace`, and the
`/tmp` tmpfs remain writable as required by coding agents and test suites.

## Cleanup

`./run.sh --cleanup` is deliberately narrow: it removes only containers named
`swe_*` and images whose repository begins with `swebench/sweb.`. It does not
prune or remove unrelated Docker resources.

## Configuration

### LlamaCPP / Local Model

- **Endpoint:** `http://host.docker.internal:11434/v1` (from inside Docker)
- **API Key:** `local-key` — bogus/fake key, safe to publish

The Codex adapter accepts these optional host environment overrides and passes
only explicitly set values into Codex containers:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SWE_CODEX_MODEL` | `qwen3.6-35b-a3b` | Responses API model id |
| `SWE_CODEX_BASE_URL` | `http://host.docker.internal:11434/v1` | Provider base URL visible inside Docker |
| `SWE_CODEX_API_KEY` | `local-key` | Provider bearer token |
| `SWE_CODEX_CONTEXT_WINDOW` | `256000` | Model context window |
| `SWE_CODEX_AUTO_COMPACT_TOKEN_LIMIT` | `230400` | Auto-compaction threshold |

For example:

```bash
SWE_CODEX_MODEL=my-model \
SWE_CODEX_BASE_URL=http://host.docker.internal:8080/v1 \
./run.sh --run codex django__django-7530
```

Use only a fake or narrowly scoped proxy token for `SWE_CODEX_API_KEY`.
Repository-controlled commands and tests run inside the same container and may
be able to inspect its environment. Do not pass a personal Codex login or a
broadly privileged API credential.

Run the host-side regression checks with:

```bash
bash tests/test_harness.sh
```

## Git Remote

- **origin:** https://github.com/SterlingNerd/swe-bench.git
