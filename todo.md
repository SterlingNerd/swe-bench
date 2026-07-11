# Bugfix Todo — swe-bench harness

Audit findings from repository review. Grouped by whether they block
end-to-end evaluation. Each item lists the file:line and the fix.

## Blocker: evaluation does not work end-to-end

### [x] 1. ~~Dataset cache is polluted → all lookups fail~~ (`run.sh:43-53`)
`fetch_dataset` captures the container's stdout to `$CACHE_FILE`, but the
`pip install datasets -q 2>&1 | tail -1` line prints its
`Successfully installed ...` summary to stdout *before* the JSON. The cache
file becomes `pip-summary\n<json>`, so every `json.load(...)` consumer
(`get_instance`, `do_list`, `do_run_all`, `do_index`) raises. `--list`,
`--run`, `--run-all` all crash.

Fix options:
- Send pip output to stderr instead of mixing into stdout, e.g.
  `pip install datasets -q >/dev/null 2>&1` (discard), or
- Redirect only the python `print(json.dumps(data))` into the file:
  `docker run --rm python:3.10-slim bash -c "pip install -q datasets >/dev/null 2>&1; python3 -c '...print(json.dumps(data))...'" > "$CACHE_FILE" 2>/dev/null`

### [x] 2. ~~Patch is double-escaped → `predictions.json` corrupt (`entrypoint.sh:83`)~~
```python
patch = patch.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
...
json.dump(pred, f)
```
Manual escaping + `json.dump` double-escapes newlines/quotes. After
`json.loads` on the way back, `model_patch` contains literal `\n` / `\"`
and the patch can't be applied.

Fix: delete the three `replace(...)` calls and `json.dump` the raw patch:
```python
with open('${OUTPUT_DIR}/patch.diff', 'r') as f:
    patch = f.read()
pred = [{'instance_id': '${INSTANCE_ID}', 'model_name_or_path': 'agent', 'model_patch': patch}]
with open('${OUTPUT_DIR}/eval/predictions.json', 'w') as f:
    json.dump(pred, f)
```

### [x] 3. ~~Evaluation can't run in `--run` (no Docker daemon) (`run.sh:209-225`, `entrypoint.sh:81-110`)~~
SWE-bench harness runs tests inside a Docker container, so it needs a
Docker daemon. `--interactive` mounts the host socket, but `--run` does not
and no in-container `dockerd` is started. `run_evaluation` fails, `|| true`
swallows it, `STATUS` stays `"unknown"`.

Fix: separated work from eval. `--run`/`--run-all` now only collect patches
(no Docker needed inside the agent container). A new `--eval <agent>` command
runs a harness container with Docker access to evaluate all collected patches.

### [x] 4. ~~Output location mismatch → `do_status` never finds results~~
(`entrypoint.sh:22` vs `run.sh` `do_status` / `README.md:95`)
Entrypoint writes to `${WORKSPACE}/outputs/<id>` (=$SWE_WORKSPACE_DIR/outputs),
while `do_status` and README read `${SWE_OUTPUT_DIR:-$REPO_ROOT/outputs}`.

Fix: make them agree — either set `OUTPUT_DIR` defaults to
`$WORKSPACE_DIR/outputs` in `do_status`, or change the entrypoint to write to
a path that `do_status` already points at. Also fix the README path.

## Security / hardening

### [x] 5. ~~README overstates hardening — no read-only root fs (`run.sh` docker runs, `README.md:110`)~~
README claims "Read-only root filesystem" but there is no `--read-only` flag
anywhere in `run.sh`. Container can write to root fs.

Fix: add `--read-only --tmpfs /home/agent/.cache:rw,...` (and any other
writable paths the agent needs) to the `--run` / `--interactive` `docker run`
commands, or remove the claim from the README.

### [x] 6. ~~`--interactive` grants host root via Docker socket (`run.sh:275`)~~
Mounting `/var/run/docker.sock` lets the container (and the agent inside it)
control the host Docker daemon = full host root.

Fix: removed the Docker socket mount from `do_interactive` (it doesn't need
Docker access). Also changed `do_eval` to run directly on the host instead of
wrapping it in a container — the swebench harness creates its own test
containers as needed.

### [x] 7. ~~Committed credential-shaped `auth.json` is dead/unused (`auth.json`)~~
Root `auth.json` has `"key": "asdfasdf"`, is committed, but `run.sh` mounts
`agents/pi/.pi/auth.json` instead. Fake key (no leak) but bad hygiene and
confusing vs the documented layout.

Fix: delete the orphaned root `auth.json` (or move it to where it's actually
used) and reconcile with the README's `auth.json` description.

## Minor / robustness

### [x] 8. ~~`session-id` literal quotes → `session.jsonl` never saved (`entrypoint.sh:23,60`)~~
`AGENT_CMD="... --session-id '${INSTANCE_ID}'"` embeds literal single quotes
that become part of the argument; the later `cp /tmp/pi-sessions/${INSTANCE_ID}/session.jsonl` looks for the unquoted path and fails (`|| true`).
Fix: drop the single quotes: `--session-id ${INSTANCE_ID}`.

### [x] 9. ~~Dead `.replace('/', '/')` no-op (`run.sh:206`)~~
Leftover no-op string replace on `repo`. Remove.

### [x] 10. ~~Variable-in-`python3 -c` injection pattern (`run.sh:200-202`, `entrypoint.sh:81`)~~
Shell variables are embedded directly into `python3 -c "..."` sources. Works
for dataset-shaped IDs but fragile / latent injection if an id ever contains
a quote. Fix: pass values via env vars or stdin (`python3 - "$id" <<'PY'`).

### [x] 11. ~~`do_run_all` loop in subshell loses `set -e` semantics (`run.sh` `do_run_all`)~~
`fetch_dataset | python3 ... | while read` runs the loop in a subshell; a
failure inside `do_run` won't stop the batch.
Fix: use process substitution `while read ...; do done < <(fetch_dataset | ...)`.

### [x] 12. ~~`do_build` builds `swe-base` twice (`run.sh` `do_build`)~~
`agents/base/` is treated as an agent in the loop, so `swe-base` is built
both explicitly and in the loop. Harmless redundancy.
Fix: skip `base` in the agent loop (or build it only in the loop).

### [x] 13. ~~Stale README reference to deleted `docker-compose.yml` (`README.md:10`)~~
Git history removed the compose file; README still lists it. Update docs.

## Remaining bugs

### [x] 14. ~~`do_status` reads from wrong directory — never finds results (`entrypoint.sh:22` vs `run.sh:do_status`)~~
Entrypoint writes to `${WORKSPACE}/outputs/<id>` which maps to `$SWE_WORKSPACE_DIR/outputs/<id>` on the host.
But `do_status` reads from `${SWE_OUTPUT_DIR:-$REPO_ROOT/outputs}` — a completely different path.
These only match if you explicitly set `SWE_OUTPUT_DIR=./workspace/outputs`.

Fix: change `OUTPUT_DIR` default in `run.sh` to `${SWE_WORKSPACE_DIR:-${REPO_ROOT}/workspace}/outputs`,
or change the entrypoint to write to `$REPO_ROOT/outputs`. ✅ Fixed — `OUTPUT_DIR` now always derives from `WORKSPACE_DIR`.

### [x] 15. ~~`--run` status is always `"patch_collected"` — never shows resolved/failed (`entrypoint.sh:70-80`, `run.sh:do_eval`)~~
The entrypoint writes `{"status": "patch_collected", ...}` to `result.json`. It never runs the swebench
harness, so `--status` can only show "no result" for all instances until `--eval` runs successfully.
This is by design (separation of concerns), but misleading: `result.json` implies a final verdict
when it's really just an intermediate marker.

Fix: have `do_eval` parse the swebench harness output (`test_results.jsonl`) after evaluation and
update each instance's `result.json` with the actual status (`resolved` / `failed`). ✅ Fixed —
`do_eval` now writes results to a dedicated `results/` directory and updates `result.json` per instance.

### [x] 16. ~~`repo_url` gets `.git` appended in entrypoint — fragile (`entrypoint.sh:40`)~~
```bash
git clone "${REPO_URL}.git" "$REPO_DIR"
```
The HuggingFace dataset `repo` field is `django/django` (no `.git`), so this works today.
But if any future dataset variant includes `.git` in the URL, it becomes
`https://github.com/django/django.git.git` and clone fails.

Fix: strip trailing `.git` before appending.
✅ Fixed — `REPO_URL_CLEAN` now strips trailing `.git` before appending.
