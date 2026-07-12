# SWE-bench + Pi Coding Agent

A locked-down Docker sandbox for running coding agents against **SWE-bench Verified** tasks.

## Repo Structure

```
swe-bench/
├── README.md                      # This file
├── run.sh                         # Unified orchestrator (--index, --list, --build, --run)
│
├── agents/base/                   # Shared base image (not agent-specific)
│   └── Dockerfile.base            # Python 3.10 + swebench + pyenv + system deps
│
├── agents/pi/                     # Pi agent container definition
│   ├── .pi/                       # Pi config (settings, models, auth, npm)
│   │   ├── settings.json          # Provider, model, retry settings
│   │   ├── models.json            # Local llama.cpp provider definition
│   │   └── npm/                   # Pi packages
│   ├── Dockerfile.pi              # Pi agent on top of base (Node.js + pi CLI)
│   └── entrypoint.sh              # Generic clone → run agent → extract patch → eval
│

```

## Quick Start

### 1. Index the dataset (first time only)

```bash
./run.sh --index
```

Fetches and caches all 500 SWE-bench Verified instances from HuggingFace.

### 2. Build images

```bash
./run.sh --build          # build base + all agent images
./run.sh --build pi     # build base, then only the 'pi' agent image
```

Always builds `swe-base` (shared infrastructure), then each agent image
(`swe-<agent>`). Pass an agent name (e.g. `pi`, `codex`) to build just that
one; omit it to build all agent images. Existing images are skipped.

To force a from-scratch build (e.g. to pull the latest pi CLI or refresh
cached layers), use `--rebuild` instead — it always rebuilds with `--no-cache`
and skips nothing. A SCOPE argument controls what is rebuilt:

```bash
./run.sh --rebuild          # rebuild base + all agent images from scratch
./run.sh --rebuild all      # same as above (default)
./run.sh --rebuild base     # rebuild only the shared base image
./run.sh --rebuild pi       # rebuild only the 'pi' agent image (base NOT rebuilt)
```

### 3. Run an agent against a specific instance

```bash
./run.sh --run pi django__django-11039
```

Clones the repo, runs Pi headlessly, and extracts the patch. Results persist in `outputs/django__django-11039/`. Evaluate afterward with `./run.sh --eval pi` (Docker-free, quickstart-style — see below).

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
Containers are given **no Docker access** — eval runs inside `swe-base` (which
bundles build tooling + pyenv pythons) and applies patches + runs tests directly.

For each collected patch, `--eval`:
1. clones the repo at the base commit,
2. applies the model patch **and** the dataset's `test_patch`,
3. installs the project in a pyenv venv,
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
Contains everything any agent container needs:
- Python 3.10 + pyenv (7 versions: 3.5–3.11)
- swebench harness + common tooling (tox, pytest, cython, etc.)
- System deps (gcc, make, cmake, imagemagick, ffmpeg, graphviz, texlive*, etc.)
- Docker CLI (available in the image; evaluation runs Docker-free — see Evaluation)

### Agent image (`swe-pi`)
Layers the Pi coding agent on top of base:
- Node.js + `pi` CLI
- Pi config (provider, model, auth keys mounted at runtime)
- Generic entrypoint that handles clone → run agent → extract patch (evaluation is done via `--eval`)

### Entrypoint (shared across agents)
The entrypoint script is generic — any agent container (pi, codex, claude) can use it:
1. Receives: `instance_id`, `repo_url`, `base_commit`, `problem_statement`
2. Clones repo at correct commit
3. Runs the agent command (`pi -p` for Pi)
4. Extracts patch via `git diff`
5. Evaluation is a separate, Docker-free step (`--eval`); see Evaluation below
6. Saves results to `outputs/<instance_id>/`

### Output structure
```
outputs/<instance_id>/
├── meta.json                # instance_id, repo_url, base_commit
├── problem_statement.txt    # Full GitHub issue text
├── agent_output.txt         # Raw stdout from agent
├── session.jsonl            # Full pi session (tool calls, responses)
├── patch.diff               # Git diff of all changes made
├── result.json              # {"status": "resolved|failed|no_patch", "elapsed_seconds": N}
└── eval/                  # (created by --eval)
    ├── predictions.json     # SWE-bench JSON input
    └── harness.log          # Full evaluation output
```

## Security Hardening

Containers are intentionally locked down:
- **Dropped all capabilities** — only `NET_RAW` added back
- **No new privileges** — `no-new-privileges:true`
- **Memory limit** — 8 GB RAM + swap, 500 PID limit
- **Unprivileged user** — runs as `agent` user, not root
- **tmpfs mounts** — `/tmp` and workspace are tmpfs with `noexec,nosuid`

## Configuration

### LlamaCPP / Local Model
- **Endpoint:** `http://localhost:11434` (or `host.docker.internal:11434/v1` from inside Docker)
- **API Key:** `local-key` — bogus/fake key, safe to publish

## Git Remote
- **origin:** https://github.com/SterlingNerd/swe-bench.git
