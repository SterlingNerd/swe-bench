# PR #7 Output and Cleanup Findings

Scope: suggestion-only review at commit 3853fe0e344c5c3955e4a29d43ab5aeed2805a14. This branch starts at that commit and changes no production code.

## O01: duplicate Docker image

- Severity: Critical
- Classification: Confirmed production defect
- Evidence: runner.py:141-160 starts command with image_name and also passes image_name to DockerOps.run_container; docker_ops.py:165-189 inserts the image again.
- Impact: argv becomes docker run ... IMAGE IMAGE /agent/entrypoint.sh, so Docker treats the second image as the in-container executable and normal Python work cannot start correctly.
- Regression: record the complete production Runner argv and require one image immediately followed by /agent/entrypoint.sh; add an opt-in Runner-level Docker smoke.
- Minimal fix: remove image_name from Runner's command list and let DockerOps insert it once.

## O02: doubled agent output directory

- Severity: Critical
- Classification: Confirmed production defect
- Evidence: runner.py:115-139 mounts host outputs/<agent> at /workspace/outputs but sets SWE_OUTPUT_ROOT=/workspace/outputs/<agent>; agents/pi/entrypoint.sh:47-48,67-80 appends the instance. run.sh:674-700 shares this mapping.
- Impact: writes land at host outputs/<agent>/<agent>/<instance>; success duplicates files and failures can be stranded outside canonical summary/resume/eval paths.
- Regression: assert exact mount, environment, entrypoint destination, and copy source through Runner; prove no <agent>/<agent> path. Cover Bash separately.
- Minimal fix: retain the agent-specific host mount, set SWE_OUTPUT_ROOT=/workspace/outputs, and copy /workspace/outputs/<instance>.

## O03: cleanup-partial deletes agent history

- Severity: Critical
- Classification: Confirmed production defect
- Evidence: config.py:35-38 makes output_dir the global root; cli.py:571-586 inspects immediate children for result/patch files and recursively deletes them.
- Impact: outputs/pi is treated as an incomplete run and the entire Pi history can be removed.
- Regression: invoke the real Click command with multiple agent roots, complete/incomplete instances, and sentinels; require roots, complete instances, unrelated agents, and siblings to survive.
- Minimal fix: require explicit agent/run scope, traverse <output>/<agent>/<instance>, add containment guards, refuse agent-root deletion, default to dry-run, and require apply.

## O04: failure artifacts are not normalized

- Severity: High
- Classification: Confirmed production defect
- Evidence: runner.py:166-196 removes the container and returns on timeout/error; collection runs only at runner.py:198-247. The Pi entrypoint writes incrementally at entrypoint.sh:67-95.
- Impact: canonical attempts miss partial patches, sessions, logs, and diagnostics. Correct bind-mounted bytes may survive removal, but O02 places them incorrectly; container-only data becomes unavailable.
- Regression: drive timeout, exit 137, and ordinary nonzero through Runner and require best-effort collection/metadata merge before release.
- Minimal fix: finalize artifacts in one try/finally path for every terminal state, then release only the owned container.

## O05: empty output reports success

- Severity: High
- Classification: Confirmed production defect
- Evidence: runner.py:211-235 accepts an existing copied directory; runner.py:264-287 defaults status to patch_collected without a typed result or patch.
- Impact: a zero-exit container producing no usable work can be recorded as successful.
- Regression: return an empty directory from the production copy boundary and require non-success plus retained diagnostics.
- Minimal fix: require valid result.json and an explicit patch.diff policy; never infer success from directory existence.

## O06: cleanup ownership is global

- Severity: High
- Classification: Confirmed production safety defect; the broad helper lacks a production caller at this anchor
- Evidence: storage.py:113-160 force-removes every container matching ^/swe_ without ownership labels; storage.py:61-107 removes every swebench/ image.
- Impact: if invoked, cleanup can terminate another checkout's work or evict unrelated shared images.
- Regression: model two labeled owners and unrelated swebench/ images; cleanup for one owner must preserve all others.
- Minimal fix: label/filter by repository and run, narrow the image namespace, and make forced global cleanup explicit.

## O07: critical disk pressure is advisory

- Severity: Medium
- Classification: Confirmed production defect
- Evidence: config.py:25 defines max_storage_pct, but runner.py:104-107 uses the default check and continues even when critical.
- Impact: work starts in predictable ENOSPC conditions and can truncate state.
- Regression: return critical pressure and require no pull/start; verify the configured threshold is passed.
- Minimal fix: fail closed at the configured threshold unless explicitly overridden.

Suggested order: O01/O02, then O04/O05, then O03/O06/O07, with production-path regressions in the same commits.

Official contracts:
- https://docs.docker.com/engine/storage/bind-mounts/
- https://docs.docker.com/reference/cli/docker/container/cp/
