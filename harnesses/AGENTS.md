# Agents — Harness Development

This directory holds **agent definitions** (harnesses). Each subdirectory is one
agent (e.g. `pi/`). Use this directory to add new agents or update existing ones.

> **Not for benchmarking.** To run benchmarks, see [`README.md`](../README.md).
> This directory and its docs are for *developing* agent harnesses only.

## Agent folder schema

```
harnesses/
└── <agent>/                 # one agent (directory name = agent id)
    ├── entrypoint.sh        # SOURCE OF TRUTH — container entrypoint (runs as /agent/entrypoint.sh)
    ├── build_bundle.sh      # builds <agent>/bundle/ from this folder
    ├── .pi/                 # pi CLI config (copied into the bundle at build time)
    │   ├── settings.json
    │   ├── models.json
    │   ├── auth.json
    │   └── npm/             # extra npm packages (e.g. loop-police), copied as-is
    └── bundle/              # GENERATED — gitignored, do NOT edit by hand
        ├── bin/             # node, pi, fd, rg
        ├── node_modules/    # pi-coding-agent + deps
        ├── .pi/agent/       # copy of .pi/ above
        └── entrypoint.sh    # copy of <agent>/entrypoint.sh (produced by the build)
```

### `entrypoint.sh` (source of truth)
The container entrypoint. The orchestrator mounts the bundle read-only at `/agent` and
executes `/agent/entrypoint.sh`. Contract:

- **Arguments:** `<instance_id> <repo_url> <base_commit> <problem_statement>`
- **Environment (set by the harness):**
  - `SWE_OUTPUT_ROOT` — output root. The agent must write per-instance output to
    `${SWE_OUTPUT_ROOT}/<instance_id>`. **Honor this variable** (do not hardcode the
    path) so the harness controls where outputs land.
  - `SWE_AGENT_NAME` — agent id (defaults to `pi`).
- **Output files** written under `${SWE_OUTPUT_ROOT}/<instance_id>/`:
  `patch.diff`, `result.json`, `meta.json`, `agent_output.txt`,
  `problem_statement.txt`, `pi-sessions/`, `eval/`.
- **Result statuses** in `result.json`:
  - `patch_collected` — agent modified files and produced a non-empty patch
  - `no_patch` — agent ran to completion (exit code 0) but did not modify any files;
    this is a legitimate result indicating the agent could not or chose not to produce
    a fix, not a crash or container error
  - `agent_error` — agent exited with a non-zero exit code
  - `container_error` — container failed before the agent could complete
  - `timed_out` — agent exceeded the configured timeout

### `.pi/`
pi CLI configuration. `settings.json`/`models.json`/`auth.json` and any `npm/`
packages are copied verbatim into the bundle during the build.

### `build_bundle.sh`
Builds a self-contained, relocatable bundle: downloads a pinned Node.js, installs
the pinned `pi-coding-agent` CLI + dependencies, fetches `fd`/`ripgrep`, then copies
`.pi/` config and `entrypoint.sh` into `bundle/`. Invoked by the harness
(`./run.sh build <agent>`).

### `bundle/` — GENERATED, gitignored
The built package that gets injected into the agent container (`-v bundle:/agent:ro`).
It is **not** source and is **not** tracked by git. Any edit made directly inside
`bundle/` is lost on the next build.

## How it plugs into the harness

The orchestrator (see [`README.md`](../README.md)) mounts `<agent>/bundle` read-only
at `/agent` and runs `/agent/entrypoint.sh`. It sets:

- `SWE_OUTPUT_ROOT=/workspace/outputs` — agent writes per-instance output here
- `SWE_AGENT_NAME=<agent>` — agent id

After the container exits, the orchestrator `docker cp`s outputs out and removes
the container.

> **Output delivery — why both a bind mount and `docker cp`?**
> This is deliberate, not redundant:
> - **Bind mount = crash safety net.** The agent writes directly into a host-backed
>   directory, so even if the container OOM-kills, gets `SIGKILL`ed, or otherwise dies
>   hard, the output already exists on the host. We never lose it.
> - **`docker cp` = correct file ownership.** The agent runs as root inside the
>   container, so files written to the bind mount are owned by root on the host. After
>   the copy, the orchestrator `chown`s the instance directory to the invoking user — the
>   easiest reliable way to fix ownership without `chmod` workarounds on the mount.
> The `docker cp` path must match where the agent actually writes
> (`${SWE_OUTPUT_ROOT}/<instance_id>`); a mismatch there is the classic cause of
> "Failed to copy outputs" errors.

## ⚠️ Rebuild rule (mandatory)

**Any modification to an agent folder must be followed by a rebuild of that agent.**

```bash
./run.sh --build <agent>      # regenerates <agent>/bundle/ from the folder
```

This applies to changes in `entrypoint.sh`, `.pi/*`, `build_bundle.sh`, or the
pinned tool versions. Editing files **inside** `bundle/` directly is forbidden:
`bundle/` is gitignored and is overwritten on the next `--build`, so hand edits are
never persisted and silently drift from the source. Always change the folder and
rebuild.

> Note: `build_bundle.sh` resolves paths absolutely, so it works whether invoked via
> `./run.sh --build <agent>` (absolute path) or directly
> (`bash harnesses/<agent>/build_bundle.sh harnesses/<agent>/bundle`, relative path).
