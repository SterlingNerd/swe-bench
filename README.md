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
./run.sh --build
```

Builds `swe-base` (shared infrastructure) + `swe-pi` (Pi agent on top).

### 3. Run an agent against a specific instance

```bash
./run.sh --run pi django__django-11039
```

Clones the repo, runs Pi headlessly, extracts the patch, evaluates it. Results persist in `outputs/django__django-11039/`.

### 4. Run against all instances

```bash
./run.sh --run-all pi
```

Iterates through all 500 verified instances sequentially.

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
- Docker CLI (for DinD evaluation)

### Agent image (`swe-pi`)
Layers the Pi coding agent on top of base:
- Node.js + `pi` CLI
- Pi config (provider, model, auth keys mounted at runtime)
- Generic entrypoint that handles clone → run → extract → eval

### Entrypoint (shared across agents)
The entrypoint script is generic — any agent container (pi, codex, claude) can use it:
1. Receives: `instance_id`, `repo_url`, `base_commit`, `problem_statement`
2. Clones repo at correct commit
3. Runs the agent command (`pi -p` for Pi)
4. Extracts patch via `git diff`
5. Evaluates using swebench harness
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
└── eval/
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
