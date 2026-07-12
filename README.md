# SWE-bench + Pi Coding Agent

A locked-down Docker sandbox for running coding agents against **SWE-bench Verified** tasks.

## Architecture

```
swe-bench/
├── run.sh                         # Unified orchestrator
├── eval_local_worker.py           # Per-instance evaluator (runs on host)
│
├── agents/base/                   # Minimal base image (bash + git)
│   └── Dockerfile.base            # python:3.10-slim with git
│
├── agents/pi/                     # Pi agent — self-contained bundle
│   ├── .pi/                       # Pi config (settings, models, auth)
│   │   ├── settings.json          # Provider, model, retry settings
│   │   ├── models.json            # Local llama.cpp provider definition
│   │   └── npm/                   # Pi packages (loop-police, etc.)
│   ├── build_bundle.sh            # Builds self-contained agent bundle
│   ├── entrypoint.sh              # Clone → run pi → extract patch
│   └── bundle/                    # Built bundle (Node.js + pi CLI + config)
│
└── workspace/outputs/             # Per-instance results
```

**Key design:** The base image is minimal (bash + git). Agent bundles are
self-contained directories with Node.js, pi CLI, and config — mounted
read-only at runtime. This eliminates the need for per-agent Docker images.

## Quick Start

### 1. Index the dataset (first time only)

```bash
./run.sh --index
```

Fetches and caches all 500 SWE-bench Verified instances from HuggingFace.

### 2. Build images + bundles

```bash
./run.sh --build          # build base image + all agent bundles
./run.sh --build pi       # build base, then only the 'pi' bundle
```

Always builds `swe-base` (minimal: bash + git), then each agent's self-contained
bundle under `agents/<agent>/bundle/`. Pass an agent name (e.g. `pi`, `codex`)
to build just that one; omit it to build all agent bundles. Existing images/bundles
are skipped.

To force a from-scratch build (e.g. to pull the latest pi CLI or refresh cached
layers), use `--rebuild` instead — it always rebuilds with `--no-cache` and skips
nothing. A SCOPE argument controls what is rebuilt:

```bash
./run.sh --rebuild          # rebuild base + all agent bundles from scratch
./run.sh --rebuild all      # same as above (default)
./run.sh --rebuild base     # rebuild only the shared base image
./run.sh --rebuild pi       # rebuild only the 'pi' agent bundle (base NOT rebuilt)
```

### 3. Run an agent against a specific instance

```bash
./run.sh --run pi django__django-11039
```

Clones the repo, runs Pi headlessly (from the mounted bundle), and extracts the
patch. Results persist in `outputs/django__django-11039/`. Evaluate afterward with
`./run.sh --eval pi` (Docker-free, quickstart-style — see below).

### 4. Run against all instances

```bash
./run.sh --run-all pi
```

Iterates through all 500 verified instances sequentially.

### 4b. Evaluate collected patches (Docker-free, quickstart-style)

```bash
./run.sh --eval pi          # evaluate collected patches (no Docker access)
./run.sh --summarize pi     # combine results -> outputs/summary.json
```

Evaluation follows the [SWE-bench quickstart](https://www.swebench.com/SWE-bench/guides/quickstart/)
methodology and is a **separate step from `--run`** (the agent "work" step).
Runs on the host (no Docker needed) — uses system Python or pyenv if available.

For each collected patch, `--eval`:
1. clones the repo at the base commit,
2. applies the model patch **and** the dataset's `test_patch`,
3. installs the project in a venv,
4. runs the instance's `FAIL_TO_PASS` / `PASS_TO_PASS` tests
   (Django uses `tests/runtests.py`; other repos use `pytest`).

It writes `local_eval.json` per instance and folds the result into
`result.json`. It can be slow (it bootstraps each project from source). A
`predictions.json` in the standard SWE-bench format is also written, in case a
Docker-enabled harness eval is ever run elsewhere.

### 5. Check status

```bash
./run.sh --status
```

Shows color-coded completion overview.

## How it works

### Base image (`swe-base`)
Minimal image with just the essentials:
- Python 3.10 (slim) + bash + git
- No agent-specific code — everything comes from mounted bundles

### Agent bundle (`agents/pi/bundle/`)
Self-contained, relocatable directory containing:
- Node.js binary (pinned version, architecture-specific)
- `pi` CLI and all npm dependencies
- Config files (settings.json, models.json, auth.json)
- entrypoint.sh shim

Built by `agents/pi/build_bundle.sh`. No Docker image needed.

### Container runtime
When running an agent:
1. Base image provides bash + git for cloning repos
2. Agent bundle is mounted read-only at `/agent`
3. Outputs go to writable `/output/[instance_id]/`
4. Cached repos live in `/workspace/repos/`

### Entrypoint (shared)
The entrypoint script handles:
1. Receives: `instance_id`, `repo_url`, `base_commit`, `problem_statement`
2. Clones repo at correct commit
3. Runs the agent command (`pi -p` from the bundled binary)
4. Extracts patch via `git diff`
5. Evaluation is a separate, Docker-free step (`--eval`)

### Output structure
```
outputs/<instance_id>/
├── meta.json                # instance_id, repo_url, base_commit
├── problem_statement.txt    # Full GitHub issue text
├── agent_output.txt         # Raw stdout from agent
├── session.jsonl            # Full pi session (tool calls, responses)
├── patch.diff               # Git diff of all changes made
├── result.json              # {"status": "resolved|failed|no_patch", ...}
└── eval/                    # (created by --eval)
    ├── predictions.json     # SWE-bench JSON input
    └── harness.log          # Full evaluation output
```

## Security Hardening

Containers are intentionally locked down:
- **Dropped all capabilities** — only `NET_RAW` added back
- **No new privileges** — `no-new-privileges:true`
- **Memory limit** — 8 GB RAM + swap, 500 PID limit
- **tmpfs mounts** — `/tmp` is tmpfs with `noexec,nosuid`

## Configuration

### LlamaCPP / Local Model
- **Endpoint:** `http://host.docker.internal:11434/v1` (from inside Docker)
- **API Key:** `local-key` — bogus/fake key, safe to publish

## Git Remote
- **origin:** https://github.com/SterlingNerd/swe-bench.git
