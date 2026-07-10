# SWE-bench + Pi Coding Agent

A locked-down Docker sandbox for running the **Pi coding agent** (powered by a local llama.cpp model) against **SWE-bench Verified** tasks — and comparing results against other agents.

## What This Is

This repo sets up an isolated evaluation environment where:

1. A local LLM (llama.cpp on `localhost:11434`) runs the Pi coding agent inside a hardened Docker container
2. The agent attempts to fix real bugs in 5 open-source projects from SWE-bench Verified
3. Generated patches are packaged and evaluated using the official SWE-bench harness
4. Results can be compared against other agents (e.g., a friend's Codex agent)

## Benchmark Instances

| Instance ID | Project | Bug Description |
|---|---|---|
| `django__django-11039` | Django | `sqlmigrate` crashes on multi-database setups with non-standard naming — should inspect migration footprint instead of assuming a linear chain back to initial |
| `scikit-learn__scikit-learn-10508` | scikit-learn | `LabelEncoder` fails when `transform` is called on an empty array or one containing completely new string categories — should raise a clear `ValueError` |
| `astropy__astropy-14995` | Astropy | `NDDataRef` mask initialization fails when a mask is passed as a constant operand during deep copy arithmetic — needs basic type validation |
| `pytest-dev__pytest-7407` | pytest | `pytest.approx` throws a `TypeError` instead of resolving cell approximation when comparing complex numbers inside nested lists/tuples |
| `requests__requests-3362` | Requests | `json` parameter in `requests.request` rejects unicode strings in Python 2/3 transitions when handling raw byte streams, throwing an encoding `AttributeError` |

## Architecture

```
┌─────────────────────────────────────────────┐
│  Host Machine                                │
│                                              │
│  llama.cpp (:11434) ──→ host.docker.internal │
│                                              │
│  ┌──────────────────────────────────────┐    │
│  │ pi_swe_evaluator (Docker container)   │    │
│  │                                      │    │
│  │  Pi coding agent                     │    │
│  │  → connects to llama.cpp via         │    │
│  │     host.docker.internal:11434       │    │
│  │                                      │    │
│  │  SWE-bench harness (Python)          │    │
│  │  → clones repos, runs tests          │    │
│  │  → evaluates patches                 │    │
│  └──────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

## Security Hardening

The evaluation container is intentionally locked down:

- **Read-only root filesystem** — writable tmpfs only for `/tmp` and the workspace
- **Dropped all capabilities** — only `NET_RAW` added back
- **No new privileges** — `no-new-privileges:true`
- **Memory limit** — 8 GB RAM + swap, 500 PID limit
- **No outbound internet** — uses default bridge network (no extra networks added)
- **Unprivileged user** — runs as `agent` user, not root

## Quick Start

### 1. Build images

```bash
./run.sh
```

This builds two images:
- **`swe-pi-base`** — Python 3.10 + SWE-bench + workspace setup
- **`swe-pi-sandbox`** — base + Node.js + Pi coding agent

### 2. Run the agent

The container starts interactively with the Pi coding agent ready to go.

### 3. Prepare patches

```bash
./swe-bench.sh --install   # pip install swebench (if not already in image)
./swe-bench.sh --clone     # clone all 5 repos into ./repos/
./swe-bench.sh --prompts   # print copy-paste prompts for the agent
```

### 4. Evaluate

After the agent produces `.patch` files:

```bash
# Place patches in patches/local/<instance_id>.patch
./swe-bench.sh --eval
```

This packages loose patch files into SWE-bench JSON format and runs the official evaluation harness against `princeton-nlp/SWE-bench_Verified`.

## Configuration

### LlamaCPP / Local Model

- **Endpoint:** `http://localhost:11434` (or `http://host.docker.internal:11434/v1` from inside Docker)
- **API Key:** `local-key` — this is a **bogus/fake key** and is **safe to publish**. Do not treat it as real credentials.

### Pi Agent Config

| File | Purpose |
|---|---|
| `.pi/settings.json` | Default provider (`local`), model (`qwen3.6-35b-a3b`), retry settings, theme |
| `.pi/models.json` | Provider definition — `local` (llama.cpp) |
| `.pi/auth.json` | Auth keys mounted into the container at runtime |
| `.vmpirc.json` | VM network config — declares localhost:11434 as a local service |

### Docker Compose

```yaml
services:
  swe-pi-sandbox:
    image: swe-pi-sandbox
    container_name: pi_swe_evaluator
    environment:
      - ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./auth.json:/home/agent/.pi/auth.json:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

## Git Remote

- **origin:** https://github.com/SterlingNerd/swe-bench.git
