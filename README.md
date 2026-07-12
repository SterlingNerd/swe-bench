# SWE-bench + Pi Coding Agent

Self-contained agent bundles mounted into **swebench harness** images for running coding agents against **SWE-bench Verified** tasks.

## Architecture

```
swe-bench/
├── run.sh                         # Orchestrator (index, build, eval, status)
├── eval_local_worker.py           # Per-instance evaluator (runs on host)
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

**Key design:** We do NOT build Docker images. The swebench harness provides
the container runtime. Our agent bundles are self-contained directories with
Node.js, pi CLI, and config — mounted read-only at runtime by the harness.

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

No Docker images are built. Each agent's self-contained bundle is created under
`agents/<agent>/bundle/` containing Node.js, pi CLI, config files, and entrypoint.

To force a from-scratch rebuild (e.g. to pull the latest pi CLI):

```bash
./run.sh --rebuild          # rebuild all bundles from scratch
./run.sh --rebuild pi       # rebuild only the 'pi' bundle
```

### 3. Run agents

The swebench harness launches containers using its own images, mounting our
agent bundle at `/agent` (read-only) and outputs at `/output` (writable).

### 4. Evaluate collected patches (Docker-free)

```bash
./run.sh --eval pi          # evaluate collected patches (no Docker access)
./run.sh --summarize pi     # combine results -> outputs/summary.json
```

Evaluation follows the [SWE-bench quickstart](https://www.swebench.com/SWE-bench/guides/quickstart/)
methodology and is a **separate step from agent execution**. Runs on the host
(no Docker needed).

For each collected patch, `--eval`:
1. clones the repo at the base commit,
2. applies the model patch **and** the dataset's `test_patch`,
3. installs the project in a venv,
4. runs the instance's `FAIL_TO_PASS` / `PASS_TO_PASS` tests
   (Django uses `tests/runtests.py`; other repos use `pytest`).

It writes `local_eval.json` per instance and folds the result into
`result.json`. A `predictions.json` in the standard SWE-bench format is also
written.

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
- Config files (settings.json, models.json, auth.json)
- entrypoint.sh shim

Built by `agents/pi/build_bundle.sh`. No Docker image needed.

### Container runtime (provided by swebench harness)
The harness launches containers using its own images with:
1. Agent bundle mounted read-only at `/agent`
2. Outputs written to writable `/output/[instance_id]/`
3. Cached repos in `/workspace/repos/`

### Entrypoint
The entrypoint script handles:
1. Receives: `instance_id`, `repo_url`, `base_commit`, `problem_statement`
2. Clones repo at correct commit
3. Runs the agent command (`pi -p` from the bundled binary)
4. Extracts patch via `git diff`
5. Writes results to `/output/[instance_id]/`

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

## Configuration

### LlamaCPP / Local Model
- **Endpoint:** `http://host.docker.internal:11434/v1` (from inside Docker)
- **API Key:** `local-key` — bogus/fake key, safe to publish

## Git Remote
- **origin:** https://github.com/SterlingNerd/swe-bench.git
