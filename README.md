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
├── scripts/run_artifacts.py       # Run manifest and attempt contract
├── tests/                         # Artifact and lifecycle regression tests
└── workspace/runs/
    ├── latest/<agent>             # Plain-text latest-run pointer
    └── <run_id>/                  # One immutable experiment namespace
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
  `POST /v1/responses`, including tool-call events. Its advertised model id
  and usable context must match the configured values.
- The default model id is `qwen3.6-35b-a3b`, with the intentionally fake bearer
  token `local-key`.

Verify Docker and the model server before launching an agent:

```bash
docker version
docker run --rm hello-world
curl -fsS http://localhost:11434/v1/models
```

Docker Desktop does not need to start automatically at Windows sign-in. If it
is started manually, use the server response—not the GUI process alone—as the
readiness gate. From Windows PowerShell:

```powershell
docker desktop start --timeout 120
wsl -d Ubuntu -- bash -lc 'test -S /var/run/docker.sock && docker version >/dev/null'
```

The second command must exit successfully before launching the harness. For
long or unattended benchmark runs, disable Docker Desktop Resource Saver so an
idle interval does not stop its Linux VM. A transient WSL integration error
such as `Wsl/Service/0x8007274c` is a connection timeout during integration
startup; use Docker Desktop's **Restart the WSL integration** action, then
repeat the readiness check.

Reserve `wsl --shutdown` for recovery when the integration remains unhealthy:
it stops every WSL distribution, including the shell or Codex session running
the harness. After using it, start Docker Desktop again, reopen Ubuntu, and
repeat all prerequisite checks.

Keep Docker/containerd's active image store on supported local storage. Do not
move Docker's `data-root` or Docker Desktop's managed disk onto NFS. The
planned P1 NAS integration is a separate writable OCI registry with a bounded
local working set; it is not a network-mounted Docker image store.

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

Run the full dataset with an explicit run ID and checkpoint timeout. `--resume`
continues only untouched tasks from that same manifest; it never silently
retries an existing agent attempt:

```bash
./run.sh --run-all codex --run-id verified-codex-baseline --timeout 3600
./run.sh --run-all codex --run-id verified-codex-baseline --resume
```

Install and invoke the official evaluator, then compare summaries:

```bash
./run.sh --init
./run.sh --eval codex --run-id verified-codex-baseline
./run.sh --summarize codex --run-id verified-codex-baseline
./run.sh --status codex --run-id verified-codex-baseline
```

Use `./run.sh --help` for the complete command and environment-variable list.

## Output Contract

```text
workspace/runs/<run_id>/
├── manifest.json
├── tasks/<instance_id>/attempts/<attempt_id>/
│   ├── attempt.json
│   ├── meta.json
│   ├── problem_statement.txt
│   ├── agent_output.txt
│   ├── agent_stderr.txt      # Codex only
│   ├── trajectory.jsonl      # Codex only
│   ├── pi-sessions/          # Pi only
│   ├── patch.diff
│   ├── result.json
│   ├── container-state.json
│   └── termination-request.json  # Timeout/cancel only
└── reports/
    ├── summary.json
    └── evaluations/<evaluation_id>/
        ├── predictions.jsonl
        ├── selected-attempts.json
        ├── evaluation.json
        └── harness/
```

Every invocation allocates a new `attempt-NNNN` directory. Finalization records
the patch and result sizes and SHA-256 digests in the manifest. The first
finalized, non-empty `patch_collected` attempt is selected automatically; later
attempts require explicit selection and cannot silently replace it.

Possible pre-evaluation statuses include `patch_collected`, `no_patch`,
`agent_error`, `invalid_result`, `container_error`, `oom_killed`, `timed_out`,
and `operator_cancelled`. Evaluation outcomes are stored in a report overlay;
`--eval` never mutates a finalized attempt's `result.json`.

Legacy `workspace/outputs/` trees are not auto-migrated, selected, evaluated,
summarized, or cleaned by the manifest-backed commands.

## Container Runtime

Each instance has a pre-built swebench image:

```text
swebench/sweb.eval.x86_64.django_1776_django-7530:latest
```

`run.sh` spins up that image with:

1. Agent bundle mounted read-only at `/agent`
2. Only the current attempt directory bind-mounted at
   `/workspace/outputs/<agent>/<instance_id>/`
3. Cached repos in `/tmp/repos` (tmpfs, ephemeral)
4. Calls `/agent/entrypoint.sh` as the container command

The attempt-scoped bind mount preserves diagnostics without exposing prior
attempts to the task. Containers run detached. On timeout or operator cancel,
the host writes `termination-request.json`, sends TERM, lets the entrypoint
capture an atomic binary diff with a temporary Git index, records Docker
`State`, and removes the container only after artifacts validate. Incomplete
artifacts retain the stopped container for diagnosis.

## Codex Adapter

`agents/codex/build_bundle.sh` downloads the official pinned Codex CLI 0.144.5
package for the current CPU architecture and verifies its release SHA-256
digest before extracting it. The bundle includes Codex, its sandbox helper,
and ripgrep.

The entrypoint creates an ephemeral `CODEX_HOME`, renders the Responses API
provider from `SWE_CODEX_*` settings, runs `codex exec --ephemeral --json`, and
captures the final message plus the full JSONL trajectory. It never mounts or
copies host ChatGPT/OpenAI credentials into the benchmark container.

Codex's nested sandbox cannot create namespaces under the Docker policy, so the
CLI runs with its internal approvals and sandbox bypassed. The disposable
Docker container is the outer process/filesystem boundary, matching Pi's
execution model, but task code can inspect its environment and reach the model
endpoint. Avoid replacing the fake local token with broad host credentials or
mounting private host paths into these containers.

## Security Hardening

Runtime containers have a 32 GB memory limit, a 64 GB memory-plus-swap limit,
a 500 PID limit, all Linux capabilities dropped, and `no-new-privileges`
enabled. The agent bundle is read-only, while `/testbed`, `/workspace`, and the
`/tmp` tmpfs remain writable as required by coding agents and test suites.

## Cleanup

`./run.sh --cleanup` is deliberately narrow: it removes only containers named
`swe_*` and images whose repository begins with `swebench/sweb.`. It does not
prune or remove unrelated Docker resources.

Partial artifact cleanup is manifest-bounded and dry-run by default:

```bash
./run.sh --cleanup-partial --agent codex --run-id verified-codex-baseline
./run.sh --cleanup-partial --agent codex --run-id verified-codex-baseline --apply
```

Only exact, manifest-listed, unfinalized attempt directories can be removed.
Finalized attempts, selected attempts, run roots, and legacy outputs are never
candidates.

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
python3 -B -m unittest -v tests/test_run_artifacts.py
bash tests/test_harness.sh
```

## Git Remote

- **origin:** https://github.com/SterlingNerd/swe-bench.git
