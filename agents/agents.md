# Agents

This directory holds **agent definitions**. Each subdirectory is one agent (e.g.
`pi/`). The agent folder is the **source of truth**; the `bundle/` subdirectory
inside it is a generated build artifact and must never be edited by hand.

## Agent folder schema

```
agents/
└── <agent>/                 # one agent (directory name = agent id)
    ├── entrypoint.sh        # source container entrypoint
    ├── build_bundle.sh      # builds <agent>/bundle/
    ├── <agent-config>/      # optional adapter-specific source config
    └── bundle/              # generated and gitignored; never edit by hand
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

### `.pi/`
pi CLI configuration. `settings.json`/`models.json`/`auth.json` and any `npm/`
packages are copied verbatim into the bundle during the build.

### `build_bundle.sh`
Builds a self-contained, relocatable bundle: downloads a pinned Node.js, installs
the pinned `pi-coding-agent` CLI + dependencies, fetches `fd`/`ripgrep`, then copies
`.pi/` config and `entrypoint.sh` into `bundle/`. Invoked by the harness
(`swebench-orchestrator build <agent>`).

### `bundle/` — GENERATED, gitignored
The built package that gets injected into the agent container (`-v bundle:/agent:ro`).
It is **not** source and is **not** tracked by git. Any edit made directly inside
`bundle/` is lost on the next build.

## How it plugs into the harness (`swebench-orchestrator`)

- `swebench-orchestrator build <agent>` → runs `build_bundle.sh` → produces `<agent>/bundle/`.
- `swebench-orchestrator run <agent> <instance>` (or `run-all <agent>`):
  - Requires `<agent>/bundle/` to exist.
  - Mounts `${agent}/bundle` read-only at `/agent` and runs `/agent/entrypoint.sh`.
  - Sets `SWE_OUTPUT_ROOT=/workspace/outputs/<agent>` and bind-mounts the host
    `<outputs>` root at `/workspace/outputs`. Because the entrypoint appends the
    instance ID, output lands at `<outputs>/<agent>/<instance_id>` on the host.
  - Runs the container process as the invoking host UID and GID.
  - After a successful exit, copies the same container instance directory to a
    temporary host directory, flattens it into the canonical instance directory,
    and removes the container.

> **Output delivery — why both a bind mount and `docker cp`?**
> This is deliberate, not redundant:
> - **Bind mount = partial-output path.** The agent writes directly into a
>   host-backed canonical directory, including on timeout or container error.
> - **`docker cp` = successful-exit normalization.** The runner recopies and
>   flattens the instance output after a successful container exit.
> The `docker cp` path must match where the agent actually writes
> (`${SWE_OUTPUT_ROOT}/<instance_id>`); a mismatch there is the classic cause of
> "Failed to copy outputs" errors.
- `swebench-orchestrator eval <agent>`: runs the official SWE-bench harness against
  `<outputs>/<agent>/<instance_id>/patch.diff`.

## ⚠️ Rebuild rule (mandatory)

**Any modification to an agent folder must be followed by a rebuild of that agent.**

```bash
swebench-orchestrator build <agent>      # regenerates <agent>/bundle/ from the folder
```

This applies to changes in `entrypoint.sh`, `.pi/*`, `build_bundle.sh`, or the
pinned tool versions. Editing files **inside** `bundle/` directly is forbidden:
`bundle/` is gitignored and is overwritten on the next `build`, so hand edits are
never persisted and silently drift from the source. Always change the folder and
rebuild.

> Note: `build_bundle.sh` resolves paths absolutely, so it works whether invoked via
> `swebench-orchestrator build <agent>` (absolute path) or directly
> (`bash agents/<agent>/build_bundle.sh agents/<agent>/bundle`, relative path).

## Review references

- [Current synchronization review](../docs/audits/main-sync-aa0644f-215f6d7.md)
- [Historical PR #7 audit](../docs/audits/pr7-refactor-python-3853fe0.md)
