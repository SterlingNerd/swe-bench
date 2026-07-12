We're pivoting this project setup to be simpler. Use this `todo.md` as a checklist to keep working until done. Add sub-tasks as needed. Occasionally remind yourself you're working from this file so it's context doesn't get lost. Commit after each primary task. If you are unsure, try to follow best practices. If you're still unsure, add a discussion note to the bottom of `todo.md` and continue with your best judgement if possible.

1. [x] Remove swe-base image and all references
   - [x] docker rmi the swe-base image locally
   - [x] Search for swe-base in Dockerfile(s), docker-compose, Makefile, scripts
   - [x] Remove any build stages or multi-stage references that depended on it
   - [x] Verify nothing else in the repo references it (grep -r)
   - [x] Commit cleanup
   - **New architecture:** No Docker images built — we use swebench eval images, mount our agent bundle at /agent, call entrypoint.sh inside

2. [x] Refactor `--build pi` into a self-contained agent bundle
   - [x] Audit current `--build pi` — what does it do today? (read script)
   - [x] Design output layout: what goes in the bundle dir?
     - pi binary / Node.js package
     - all skills (from .config/pi or wherever they live)
     - config files (pi config, any agent settings)
     - node_modules with pinned deps (no system-level conflicts)
   - [x] Pin dependency versions explicitly (avoid transitive drift)
   - [x] Ensure the bundle is fully relocatable (no hardcoded paths)
   - [x] Test: build from scratch, verify `pi` runs standalone inside it
   - [x] Update any CI / Makefile targets that call `--build pi`

3. [x] Container runtime: mount agent bundle read-only, outputs writable
   - [x] Study how SWE-bench launches containers (check `run.sh` or harness code)
   - [x] Mount the agent bundle at `/agent` with `-v ...:ro`
   - [x] Mount a writable volume at `/output` for all agent outputs
   - [x] Verify the swebench/sweb/env container has everything needed (bash, git, etc.)
   - [x] Handle multiple architectures if needed (x86_64 vs aarch64)
   - [x] Test: can the container see `/agent` (ro) and write to `/output`?

4. [x] Pass inference URL so pi can connect inside the container
   - [x] Use pre-baked `models.json` + `auth.json` in the agent bundle (no secrets to worry about)
   - [x] Set baseUrl to `http://host.docker.internal:11434/v1` in the baked config
   - [x] Test: point pi at a mock server, verify it connects

5. [x] Write `entrypoint.sh` shim
   - [x] Study existing `agents/pi/entrypoint.sh` — it already does most of this (clone, run pi, extract patch)
   - [x] Determine what info is available from the `--index` cache file:
     - `instance_id`, `repo`, `base_commit`, `problem_statement`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`
   - [x] Decide: does entrypoint.sh need to accept all these as args, or can it read from a mounted index?
     - If args: map each CLI arg to the right field
     - If mounted index: entrypoint reads JSON and extracts what it needs
   - [x] Fill any gaps — if `--index` doesn't have something we need (e.g. test_patch), figure out how to get it
   - [x] All outputs go to `/output/[repo__num]/` (writable mount)
   - [x] Handle edge cases: repo already cloned? broken commits?
   - [x] Make it executable and test locally with a sample instance

6. [x] Save output patch to standardized location
   - [x] Output dir: `/output/[repo__num]/` (e.g. `/output/django__django-11039/`)
   - [x] In entrypoint.sh, write the generated diff as `patch.diff`
   - [x] Ensure the patch is a valid unified diff against the repo's base commit
   - [x] Handle case where pi produces no changes (empty patch? skip?)
   - [x] Align with SWE-bench conventions — check what the eval harness expects for patch location/format

7. [x] Save session/debug files alongside the patch
   - [x] Output dir: `/output/[repo__num]/` (same as patch)
   - [x] Save anything useful for debugging:
     - pi session file (`session.jsonl` — already saved in current entrypoint.sh)
     - agent output log (`agent_output.txt` — already captured)
     - problem statement text
     - any pi debug logs or state files
   - [x] Ensure these don't interfere with the eval step (different filenames/extensions)

8. [x] Eval step: convert outputs → SWE-bench prediction JSONL → run harness
   - [x] Install swebench package (`pip install swebench`) if not present — add `./run.sh --init` for this
   - [x] Read the [SWE-bench eval guide](https://www.swebench.com/SWE-bench/guides/evaluation/) carefully
   - [x] Design the conversion:
     - Scan `/workspace/outputs/[repo__num]/` dirs for `patch.diff` files per instance
     - Map each patch to SWE-bench prediction format (JSONL with instance_id, model_patch)
   - [x] Call official swebench eval harness (not reimplement)
   - [ ] Parse and summarize results (pass/fail, test outcomes)
   - [ ] Consider making this a single `./eval.sh` script for reproducibility

9. [ ] Re-discuss inference URL configuration once the rest is working
   - [x] Current: hardcoded `http://host.docker.internal:11434/v1` in pre-baked models.json
   - [ ] Future: allow overriding via env var / config for different providers (Anthropic, OpenRouter, etc.)
   - [ ] Decide: baked config per-provider? env var override? runtime rewrite?

────────────────────────────────────────────────────────────────────────────────
### REVIEW FIXES (applied)

B. [x] Fix pi working directory — cd into repo before running pi, git diff from there
C. [x] Fix pi config discovery — use PI_CODING_AGENT_DIR=/tmp/.pi/agent with .pi/agent/ layout
E. [x] Fix uid 1001 write paths — outputs to /workspace/outputs/ (mounted rw)
D. [x] Stop swallowing errors — remove `|| true` on critical commands, surface real failures

S1. [x] Remove --cap-add NET_RAW (unnecessary)
S2. [x] Fix memory-swap: 8g → 16g
S3. [x] Make root filesystem --read-only in container
S4. [x] Fix cleanup() trap — only kill containers this script created
S5. [x] Pin pi-coding-agent version in build_bundle.sh (not @latest)
S6. [x] Remove Dockerfile.pi and .dockerignore

────────────────────────────────────────────────────────────────────────────────
### NEW ISSUES FROM SECOND REVIEW

CRITICAL:
1. [x] git diff drops new/untracked files — fixed: git add -A && git diff --cached
2. [ ] --eval doesn't match README ("Docker-free" vs actual harness) — README updated to match code
3. [ ] Unreproducible result folding — eval_local_worker.py orphaned, need to decide path

MEDIUM:
4. [x] CONTAINER_ID capture bug — removed broken cleanup mechanism, --rm handles it
5. [ ] --run-all over 500 instances pulls eval image per instance — document in README
6. [ ] eval_local_worker.py Django branch is coarse — acceptable for benchmark
7. [ ] session capture likely never copies (pi names by session-id, not instance-id)

LOW:
8. [x] todo.md stale — updated to reflect reality
9. [x] README drift — rewritten to match current code
10. [ ] build_bundle.sh rm -rf comment — low priority
