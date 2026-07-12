# SWE-bench + Pi Coding Agent

Self-contained agent bundles mounted into swebench eval images for running coding agents against **SWE-bench Verified** tasks.

## Architecture

```
swe-bench/
├── run.sh                         # Orchestrator (index, build, run, eval)
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

**Key design:** We build self-contained agent bundles (Node.js + pi CLI + config).
Our `run.sh` spins up the swebench eval image for each instance, mounts our
bundle read-only at `/agent`, and calls `entrypoint.sh` inside it. SWE-bench
then compares our output patches.

## Quick Start

### 1. Index the dataset (first time only)

```bash
./run.sh --index
```

Fetches and caches all 500 SWE-bench Verified instances from HuggingFace.

### 2. Build agent bundles

```bash
./run.sh --build          # build all agent bundles
./run.sh --build pi       # build only the 'pi' bundle
```

No Docker images are built by us. Each agent's self-contained bundle is created
under `agents/<agent>/bundle/` containing Node.js, pi CLI, config files, and
entrypoint.

To force a from-scratch rebuild:

```bash
./run.sh --rebuild          # rebuild all bundles from scratch
./run.sh --rebuild pi       # rebuild only the 'pi' bundle
```

### 3. Run an agent against a specific instance

```bash
./run.sh --run pi django__django-7530
```

Spins up the swebench eval image for that instance, mounts our agent bundle
read-only at `/agent`, and calls `entrypoint.sh` inside it. Results persist in
`workspace/outputs/django__django-7530/`.

### 4. Run against all instances

```bash
./run.sh --run-all pi
```

Iterates through all 500 verified instances sequentially.

### 4b. Evaluate collected patches (official swebench harness)

```bash
./run.sh --init           # install swebench in .venv/swebench (one-time)
./run.sh --eval pi        # run official swebench harness on collected patches
./run.sh --summarize pi   # combine results -> outputs/summary.json
```

The `--eval` step uses the **official SWE-bench harness** (`swebench.harness.run_evaluation`).
It requires Docker (pulls eval images per instance) and network access.

For each collected patch, the harness:
1. Pulls the swebench eval image for that instance
2. Applies the model patch + the dataset's test_patch
3. Runs the instance's FAIL_TO_PASS / PASS_TO_PASS tests
4. Writes results to the output directory

### 5. Check status

```bash
./run.sh --status
```

Shows color-coded completion overview.

## How it works

### Agent bundle (`agents/pi/bundle/`)
Self-contained, relocatable directory containing:
- Node.js binary (pinned version, architecture-specific)
- `pi` CLI and all npm dependencies
- Config files (settings.json, models.json, auth.json) in `.pi/agent/` layout
- entrypoint.sh shim

Built by `agents/pi/build_bundle.sh`. No Docker image needed.

### Container runtime (swebench eval images)
Each instance has a pre-built swebench image:
```
swebench/sweb.eval.x86_64.django_1776_django-7530:latest
```

Our `run.sh` spins up that image with:
1. Agent bundle mounted read-only at `/agent`
2. Outputs written to writable `/workspace/outputs/[instance_id]/`
3. Cached repos in `/workspace/repos/`
4. Calls `/agent/entrypoint.sh` as the container command

### Entrypoint
The entrypoint script handles:
1. Receives: `instance_id`, `repo_url`, `base_commit`, `problem_statement`
2. Clones repo at correct commit
3. Runs the agent command (`pi -p` from the bundled binary)
4. Extracts patch via `git add -A && git diff --cached` (includes new files)
5. Writes results to `/workspace/outputs/[instance_id]/`

### Output structure
```
outputs/<instance_id>/
├── meta.json                # instance_id, repo_url, base_commit
├── problem_statement.txt    # Full GitHub issue text
├── agent_output.txt         # Raw stdout from agent
├── session.jsonl            # Full pi session (tool calls, responses)
├── patch.diff               # Git diff of all changes made (including new files)
├── result.json              # {"status": "resolved|failed|no_patch", ...}
└── eval/                    # (created by --eval / swebench harness)
    └── reports/             # SWE-bench evaluation reports
```

## Security Hardening

Containers are intentionally locked down:
- **Dropped all capabilities** — no extra caps added
- **No new privileges** — `no-new-privileges:true`
- **Read-only root filesystem** — `--read-only`
- **Memory limit** — 8 GB RAM + 16 GB swap, 500 PID limit
- **tmpfs mounts** — `/tmp` is tmpfs with `noexec,nosuid`

## Configuration

### LlamaCPP / Local Model
- **Endpoint:** `http://host.docker.internal:11434/v1` (from inside Docker)
- **API Key:** `local-key` — bogus/fake key, safe to publish

## Git Remote
- **origin:** https://github.com/SterlingNerd/swe-bench.git
