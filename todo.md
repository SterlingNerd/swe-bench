We're pivoting this project setup to be simpler. Use this `todo.md` as a checklist to keep working until done. Add sub-tasks as needed. Occasionally remind yourself you're working from this file so it's context doesn't get lost. 

1. [ ] Remove swe-base image and all references
   - [ ] `docker rmi` the swe-base image locally
   - [ ] Search for `swe-base` in Dockerfile(s), docker-compose, Makefile, scripts
   - [ ] Remove any build stages or multi-stage references that depended on it
   - [ ] Verify nothing else in the repo references it (grep -r)
   - [ ] Commit cleanup
2. [ ] Refactor `--build pi` into a self-contained agent bundle
   - [ ] Audit current `--build pi` — what does it do today? (read script)
   - [ ] Design output layout: what goes in the bundle dir?
     - pi binary / Node.js package
     - all skills (from .config/pi or wherever they live)
     - config files (pi config, any agent settings)
     - node_modules with pinned deps (no system-level conflicts)
   - [ ] Pin dependency versions explicitly (avoid transitive drift)
   - [ ] Ensure the bundle is fully relocatable (no hardcoded paths)
   - [ ] Test: build from scratch, verify `pi` runs standalone inside it
   - [ ] Update any CI / Makefile targets that call `--build pi`
3. [ ] Container runtime: mount agent bundle read-only, outputs writable
   - [ ] Study how SWE-bench launches containers (check `run.sh` or harness code)
   - [ ] Mount the agent bundle at `/agent` with `-v ...:ro`
   - [ ] Mount a writable volume at `/output` for all agent outputs
   - [ ] Verify the swebench/sweb/env container has everything needed (bash, git, etc.)
   - [ ] Handle multiple architectures if needed (x86_64 vs aarch64)
   - [ ] Test: can the container see `/agent` (ro) and write to `/output`?
4. [ ] Pass inference URL so pi can connect inside the container
   - [ ] Use pre-baked `models.json` + `auth.json` in the agent bundle (no secrets to worry about)
   - [ ] Set baseUrl to `http://host.docker.internal:11434/v1` in the baked config
   - [ ] Test: point pi at a mock server, verify it connects
5. [ ] Write `entrypoint.sh` shim
   - [ ] Study existing `agents/pi/entrypoint.sh` — it already does most of this (clone, run pi, extract patch)
   - [ ] Determine what info is available from the `--index` cache file:
     - `instance_id`, `repo`, `base_commit`, `problem_statement`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`
   - [ ] Decide: does entrypoint.sh need to accept all these as args, or can it read from a mounted index?
     - If args: map each CLI arg to the right field
     - If mounted index: entrypoint reads JSON and extracts what it needs
   - [ ] Fill any gaps — if `--index` doesn't have something we need (e.g. test_patch), figure out how to get it
   - [ ] All outputs go to `/output/[repo__num]/` (writable mount)
   - [ ] Handle edge cases: repo already cloned? broken commits?
   - [ ] Make it executable and test locally with a sample instance
6. [ ] Save output patch to standardized location
   - [ ] Output dir: `/output/[repo__num]/` (e.g. `/output/django__django-11039/`)
   - [ ] In entrypoint.sh, write the generated diff as `patch.diff`
   - [ ] Ensure the patch is a valid unified diff against the repo's base commit
   - [ ] Handle case where pi produces no changes (empty patch? skip?)
   - [ ] Align with SWE-bench conventions — check what the eval harness expects for patch location/format
7. [ ] Save session/debug files alongside the patch
   - [ ] Output dir: `/output/[repo__num]/` (same as patch)
   - [ ] Save anything useful for debugging:
     - pi session file (`session.jsonl` — already saved in current entrypoint.sh)
     - agent output log (`agent_output.txt` — already captured)
     - problem statement text
     - any pi debug logs or state files
   - [ ] Ensure these don't interfere with the eval step (different filenames/extensions)
8. [ ] Eval step: convert outputs → SWE-bench prediction JSONL → run harness
   - [ ] Read the [SWE-bench eval guide](https://www.swebench.com/SWE-bench/guides/evaluation/) carefully
   - [ ] Design the conversion:
     - Scan `/output/[repo__num]/` dirs for `patch.diff` files per instance
     - Map each patch to a JSONL entry with `instance_id`, `model_patch`
   - [ ] Write the prediction JSONL file (standard SWE-bench format)
   - [ ] Run SWE-bench eval harness against it
   - [ ] Parse and summarize results (pass/fail, test outcomes)
   - [ ] Consider making this a single `./eval.sh` script for reproducibility
9. [ ] Re-discuss inference URL configuration once the rest is working
   - [ ] Current: hardcoded `http://host.docker.internal:11434/v1` in pre-baked models.json
   - [ ] Future: allow overriding via env var / config for different providers (Anthropic, OpenRouter, etc.)
   - [ ] Decide: baked config per-provider? env var override? runtime rewrite?
