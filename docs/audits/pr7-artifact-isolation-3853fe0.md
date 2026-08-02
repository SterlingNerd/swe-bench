# PR #7 Artifact and Attempt Isolation Findings

Scope: suggestion-only review at commit 3853fe0e344c5c3955e4a29d43ab5aeed2805a14. This branch starts at that commit and changes no production code.

## A01: same-instance concurrency kills active work

- Severity: Critical
- Classification: Confirmed production defect
- Evidence: config.py:30 defines lock_file but production never acquires it; runner.py:120-123 uses a deterministic container name and releases any existing container; runner.py:115-118 reuses one output path.
- Impact: a second process can force-remove the first container and both can overwrite/combine files.
- Regression: block one Runner call, start a second for the same agent/instance, and require busy without releasing the first container.
- Minimal fix: acquire a nonblocking lease before mutation and label each container with its lease/run identity.

## A02: signals and exceptions bypass finalization

- Severity: Critical
- Classification: Confirmed production defect
- Evidence: runner.py:155-162 blocks in execution; cleanup exists only in explicit branches and runner.py:246-247. No outer lifecycle finally exists; docker_ops.py:384 imports signal without production handling.
- Impact: SIGINT, SIGTERM, KeyboardInterrupt, or collection exceptions can leave live containers, pending attempts, and unattributed output.
- Regression: invoke the real CLI around a blocking fake child, deliver SIGINT/SIGTERM, and require scoped cancellation, artifact finalization, interrupted state, and expected exit code.
- Minimal fix: install main-thread cancellation handlers and use one idempotent lifecycle context that always finalizes.

## A03: attempt updates target the wrong task

- Severity: Critical
- Classification: Confirmed production defect
- Evidence: manifest.py:218-230 numbers attempts per instance, so several tasks have attempt-001; update_attempt_result selects the first run_id/attempt_id match at :246-280 and fabricates a path for no match at :282-285.
- Impact: status/timing can attach to the wrong instance or malformed state.
- Regression: create two attempt-001 tasks, update the second, and prove only it changes; unknown attempts must raise.
- Minimal fix: require (run_id, instance_id, attempt_id), validate the direct path, and remove fallback creation.

## A04: attempts and artifacts are mutable

- Severity: Critical
- Classification: Confirmed production defect
- Evidence: manifest.py:221-230 uses len(existing)+1 and mkdir(exist_ok=True); only metadata/result enter attempt dirs at :229-238,287-295. runner.py:115-118,214-234 stores actual artifacts flat and deletes prior destinations.
- Impact: races/gaps reuse IDs and reruns erase historical patches, logs, and sessions.
- Regression: preserve distinct attempts, delete a lower suffix, then allocate concurrently; require unique IDs and unchanged prior artifacts/digests.
- Minimal fix: atomically claim unique attempt IDs and make each attempt directory the authoritative artifact mount; derive latest afterward.

## A05: run-all lacks one batch identity

- Severity: High
- Classification: Confirmed production defect
- Evidence: runner.py:683-697 calls run_instance without a shared run ID, so runner.py:626-633 creates one manifest per instance; models.py:63-69 leaves provenance hashes empty; resume at runner.py:684-688 skips on file existence.
- Impact: no single batch inventory/provenance exists and malformed, failed, stale, or mismatched results can be treated as complete.
- Regression: run two cached instances and require one manifest/task inventory/nonempty provenance; test malformed JSON, timeout, error, missing patch, and mismatched provenance.
- Minimal fix: create one batch manifest, pass its ID to all attempts, snapshot provenance, and define typed terminal/retryable resume states.

## A06: evaluation is invalid and colliding

- Severity: Critical
- Classification: Confirmed production defect
- Evidence: cli.py:271-303 builds JSONL using repr(patch), bypasses Runner.eval/folding, and reuses predictions.jsonl, eval/, and agent-only run_id. runner.py:415-425,556-570 shares fixed namespaces. Both comma-join IDs into one -i token although the harness uses nargs='+'.
- Impact: ordinary patches can make invalid JSONL, multi-ID filtering can fail, evals overwrite each other, and reports are stale or misattributed.
- Regression: invoke Click eval with multiline/quoted patches and json.loads each line; require separate ID argv elements; run two eval IDs and fold only their exact reports.
- Minimal fix: delegate Click to one Runner service using json.dumps, unique eval/run namespaces, separate ID arguments, and an exact report path.

## A07: JSON updates are neither atomic nor serialized

- Severity: High
- Classification: Confirmed production defect
- Evidence: models.py:143-147 truncates final JSON directly; runner.py:274-280,306-322,462-477 performs unlocked read-modify-write.
- Impact: interruption corrupts JSON and concurrent writers lose fields; resume may skip the broken file.
- Regression: fault before publication and preserve the previous valid file; synchronize disjoint writers and preserve both fields.
- Minimal fix: serialize per run/attempt and publish a flushed/fsynced same-directory temporary file with atomic replace.

## A08: audit export raises at runtime

- Severity: Medium
- Classification: Confirmed production defect
- Evidence: manifest.py:447 calls undefined module-level list_attempts; only RunManager.list_attempts exists at :327-355.
- Impact: recovery/debug export fails when it processes a run.
- Regression: create a run/attempt and require ordered export without exception.
- Minimal fix: call the manager method with run_id and retain real attempt timestamps.

Suggested order: leases/finalizer, immutable attempts, one batch manifest, isolated eval, then atomic publication.

Official contracts:
- https://docs.python.org/3/library/signal.html
- https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/run_evaluation.py
