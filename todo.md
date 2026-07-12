We're pivoting this project setup to be simpler. Use this `todo.md` as a checklist to keep working until done. Add sub-tasks as needed. Occasionally remind yourself you're working from this file so it's context doesn't get lost. Commit after each primary task. If you are unsure, try to follow best practices. If you're still unsure, add a discussion note to the bottom of `todo.md` and continue with your best judgement if possible.

1. [x] Remove swe-base image and all references (We're replacing swe-base with swebench/sweb.env images)
   - [x] `docker rmi` the swe-base image locally
   - [x] Search for `swe-base` in Dockerfile(s), docker-compose, Makefile, scripts
   - [x] Remove any build stages or multi-stage references that depended on it
   - [x] Verify nothing else in the repo references it (grep -r)
   - [x] Commit cleanup
   - **New architecture:** No Docker images built by us — we use swebench eval images (swebench/sweb.eval.x86_64.<repo>_1776_<issue>:latest), mount our agent bundle at /agent, call entrypoint.sh inside

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
   - **Implementation:** run.sh --run spins up swebench eval image, mounts bundle at /agent, calls entrypoint.sh

4. [x] Pass inference URL so pi can connect inside the container
   - [x] Use pre-baked `models.json` + `auth.json` in the agent bundle (no secrets to worry about)
   - [x] Set baseUrl to `http://host.docker.internal:11434/v1` in the baked config
   - [ ] Test: point pi at a mock server, verify it connects

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
   - [x] Read the [SWE-bench eval guide](https://www.swebench.com/SWE-bench/guides/evaluation/) carefully
   - [x] Design the conversion:
     - Scan `/output/[repo__num]/` dirs for `patch.diff` files per instance
     - Map each patch to a JSONL entry with `instance_id`, `model_patch`
   - [x] Write the prediction JSONL file (standard SWE-bench format)
   - [ ] Run SWE-bench eval harness against it
   - [ ] Parse and summarize results (pass/fail, test outcomes)
   - [ ] Consider making this a single `./eval.sh` script for reproducibility

9. [ ] Re-discuss inference URL configuration once the rest is working
   - [ ] Current: hardcoded `http://host.docker.internal:11434/v1` in pre-baked models.json
   - [ ] Future: allow overriding via env var / config for different providers (Anthropic, OpenRouter, etc.)
   - [ ] Decide: baked config per-provider? env var override? runtime rewrite?
