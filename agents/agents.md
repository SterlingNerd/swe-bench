# Agent adapters

This directory contains agent source definitions. On `refactor-python`, `pi/`
is the only implemented adapter. There is no `agents/codex/` directory on this
branch.

An agent's source directory is authoritative. Its generated `bundle/` directory
is ignored build output and must not be edited or committed.

## Current tree

```text
agents/
├── agents.md
└── pi/
    ├── entrypoint.sh          # Container entrypoint source
    ├── build_bundle.sh        # Bundle builder
    └── .pi/                   # Pi configuration copied during build
        ├── settings.json
        ├── models.json
        ├── auth.json
        └── npm/
```

After a build, the ignored `agents/pi/bundle/` contains the relocatable runtime
mounted read-only at `/agent` in an SWE-bench evaluation container.

## Generic adapter contract

An adapter directory is expected to provide:

```text
agents/<agent>/
├── entrypoint.sh              # Required runtime entrypoint
├── build_bundle.sh            # Required bundle build script
├── <agent-specific config>/   # Optional source configuration
└── bundle/                    # Generated; never edit by hand
```

The bundle must expose `/agent/entrypoint.sh`. Normal execution passes:

```text
<instance_id> <repo_url> <base_commit> <problem_statement>
```

The harness also sets:

- `SWE_AGENT_NAME` — adapter identifier.
- `SWE_OUTPUT_ROOT` — container directory beneath which the entrypoint writes
  the instance directory.

The intended per-instance output set is:

```text
<SWE_OUTPUT_ROOT>/<instance_id>/
├── patch.diff
├── result.json
├── meta.json
├── agent_output.txt
└── problem_statement.txt
```

Agent-specific session data and evaluation artifacts may be added without
changing the required files.

## Current output-contract blocker

Before reaching the output contract, the experimental Python runner also
constructs an invalid Docker argv: both `Runner` and `DockerOps` insert the
image, yielding `docker run ... IMAGE IMAGE /agent/entrypoint.sh ...`. The
second image is treated as the container command. The command-composition and
output-layout fixes must be validated together through the production runner.

The intended host mapping is one agent directory mounted at one neutral
container output root:

```text
host:      workspace/outputs/<agent>/
container: /workspace/outputs/
variable:  SWE_OUTPUT_ROOT=/workspace/outputs
result:    workspace/outputs/<agent>/<instance_id>/
```

Neither current work frontend satisfies that mapping. The experimental Python
runner and legacy `run.sh` both mount the host agent directory at
`/workspace/outputs` while setting
`SWE_OUTPUT_ROOT=/workspace/outputs/<agent>`, which can create
`<agent>/<agent>/<instance_id>` on the host. Their copy paths follow the
doubled container path and therefore do not prove that the host layout is
correct.

Do not treat a model-backed run through either frontend as qualified until the
mount, environment, entrypoint output, and copy source are asserted together by
a production-path contract test.

## Build rule

Any source change inside an adapter requires rebuilding its generated bundle.
The available interfaces are:

```bash
# Legacy Bash interface
./run.sh --build pi
./run.sh --rebuild pi

# Experimental Python interface
swebench-orchestrator build pi
swebench-orchestrator rebuild pi
```

Never edit `agents/pi/bundle/` directly: the next build replaces it.

## Pi-specific source

The `.pi/` directory contains Pi CLI configuration and optional npm packages.
`build_bundle.sh` copies that source into the generated bundle along with the
pinned runtime tools and `entrypoint.sh`.

Credentials and configuration committed here must be safe for repository
visibility. Runtime secrets should use an approved external injection path;
they must not be added to the generated or source bundle tree.

## Future Codex port

A Codex adapter exists in separate archived development history, but that
history also contains obsolete Bash orchestration, conflicting documentation,
and test deletions. It must not be merged wholesale into `refactor-python`.

After the generic Python contracts stabilize:

1. review the archived Codex files individually;
2. create a fresh `agents/codex/` adapter on a branch based on the current
   Python rewrite;
3. preserve the generic argument and output contracts;
4. add discovery, bundle, entrypoint, failure, artifact, and configuration
   tests; and
5. require a model-backed canary before adding Codex to README usage examples.

## Related documentation

- [../README.md](../README.md) — branch status, entrypoints, and layouts
- [../TODO.md](../TODO.md) — blockers and selective-port plan
- [../TESTPLAN.md](../TESTPLAN.md) — required agent/output contract tests
- [../docs/audits/pr7-refactor-python-3853fe0.md](../docs/audits/pr7-refactor-python-3853fe0.md)
  — complete PR #7 audit and suggested regression contracts
