# AGENTS.md — Project Workflow & Agent Architecture

## Project Overview

This repo is the **SWE-bench Agent Harness**: a system for running self-contained coding-agent bundles against [SWE-bench Verified](https://www.swebench.com/) tasks, collecting their patches, and evaluating them with the official SWE-bench evaluation harness.

The goal is to compare agent performance on real-world GitHub issues in a reproducible, containerized environment. The included `pi` adapter uses a local llama.cpp model so results can be compared across agents without sharing output state.

### Key directories

| Directory | Purpose |
|-----------|---------|
| `src/swebench_orchestrator/` | Python CLI & orchestrator logic (Click-based interface, Docker lifecycle) |
| `harnesses/` | Agent harness definitions — each subdirectory is one agent (`pi/`, `codex/`) |
| `workspace/outputs/` | Per-agent, per-instance output artifacts (patches, results, logs) |
| `scripts/` | Utility scripts |

### How it works

1. **Build** an agent bundle (`swebench-orchestrator build <agent>`) — produces a relocatable container image with pinned dependencies.
2. **Run** the agent on an instance (`swebench-orchestrator run pi <instance_id>`) — spins up the official SWE-bench Docker image, mounts the agent bundle read-only, and executes its entrypoint.
3. **Evaluate** (`swebench-orchestrator eval pi`) — runs the official SWE-bench harness against collected patches.

---

## Workflow: Issues → Worktrees → Pull Requests

We use a **GitHub issues → worktrees → pull requests** workflow for all development on this project:

### 1. GitHub Issues
- Every task, bug fix, or feature starts as a **GitHub issue**.
- The issue description serves as the problem statement — agents consume these directly during evaluation.
- Link issues to PRs using keywords (`Fixes #N`, `Closes #N`) for automatic tracking.

### 2. Worktrees
- Create a **worktree** for each active task:
  ```bash
  git worktree add -b fix/issue-42 ../swe-bench-fix-42 origin/main
  ```
- Worktrees keep the main clone clean and allow parallel development on multiple issues without branch-switching.
- Each worktree is self-contained with its own `.git` index, working tree, and reflog.

### 3. Pull Requests
- Open a **pull request** from your worktree branch targeting `main`.
- PRs should reference the originating issue.
- Include a clear description of what changed and why, plus any relevant test or evaluation results.

---

## Agent Architecture

Each agent is defined as a folder under `harnesses/<agent>/` with this structure:

```
harnesses/
└── <agent>/                 # one agent (directory name = agent id)
    ├── entrypoint.sh        # SOURCE OF TRUTH — container entrypoint
    ├── build_bundle.sh      # builds <agent>/bundle/ from this folder
    ├── .pi/                 # pi CLI config (copied into the bundle at build time)
    │   ├── settings.json
    │   ├── models.json
    │   ├── auth.json
    │   └── npm/             # extra npm packages, copied as-is
    └── bundle/              # GENERATED — gitignored, do NOT edit by hand
```

### ⚠️ Rebuild rule (mandatory)

**Any modification to an agent folder must be followed by a rebuild:**

```bash
swebench-orchestrator --build <agent>      # regenerates <agent>/bundle/ from the folder
```

Editing files inside `bundle/` directly is forbidden — it is gitignored and overwritten on every build. Always change the source folder and rebuild.

See [`harnesses/AGENTS.md`](./harnesses/AGENTS.md) for the full agent folder schema and harness integration details.
