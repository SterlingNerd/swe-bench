# SWE-bench Harness Revamp Plan

- **Status:** P0 safety and correctness implemented; the isolated P1A state
  foundation and local-volume portion of P1D-0 are implemented; live runner
  activation, side-branch regression intake, and the real NAS P1D-0 gate remain
  before P1 can continue.
- **Target:** `agent/codex-swebench-runner`
- **Last researched:** 2026-07-21
- **Last implemented:** 2026-07-19

## Executive recommendation

Replace the current 500-task serial shell loop with a durable, budgeted
experiment supervisor. For our usage, SWE-bench Verified should primarily be
a regression suite for runner reliability, scaffold behavior, and cost. It
should not be treated as a clean frontier-capability benchmark.

The redesigned harness should:

1. Preserve every attempt and partial patch, including timeouts and operator
   cancellations.
2. Separate baseline agent behavior from loop-control and recovery policies.
3. Run paid Codex experiments through a credential-isolating, metered proxy.
4. Use fixed, stratified pilots before committing to a full 500-instance run.
5. Distinguish model failures from infrastructure, provider, and evaluator
   failures.
6. Make queue continuation, infrastructure retry, agent retry, and Codex
   session continuation distinct operations.

## Implementation round 1 log — roadmap P0 safety and correctness

Implemented on `agent/codex-swebench-runner` on 2026-07-19. This phase keeps
`run.sh` as the compatibility wrapper and introduces a JSON-manifest artifact
layer. Live SQLite scheduling, leases, richer failure classification,
paid-provider proxy, loop detection, and bounded concurrency remain deferred
to P1-P4.

### Delivered

- Added `scripts/run_artifacts.py` as the sole authority for run, task,
  attempt, selection, evaluation, summary, and partial-cleanup paths.
- Replaced reusable `outputs/<agent>/<instance>/` directories with unique
  `runs/<run_id>/tasks/<instance>/attempts/attempt-NNNN/` directories.
- Made finalized patches and result records tamper-evident by recording byte
  length and SHA-256. Evaluation fails closed if either selected artifact
  changes afterward.
- Made the first finalized, non-empty `patch_collected` attempt the recorded
  selection. Later eligible attempts do not silently replace it.
- Changed evaluation to snapshot manifest selections into
  `selected-attempts.json`, verify digests, and record outcomes in an immutable
  report overlay rather than editing attempt `result.json` files.
- Changed `--resume` into queue continuation for the named/latest manifest. It
  runs only tasks with no extant attempt and does not retry agent failures.
- Replaced broad partial-output discovery with manifest-listed cleanup.
  `--cleanup-partial` requires an agent, defaults to dry-run, and needs
  `--apply` before deleting exact unfinalized attempt paths.
- Changed work containers to detached lifecycle control. Timeout/cancel writes
  a termination request before TERM, captures Docker `State` before removal,
  distinguishes monitor exit from container exit, and retains stopped
  containers when attempt artifacts are incomplete.
- Added TERM/INT forwarding and idempotent checkpoint finalization to both
  Codex and Pi entrypoints. Patch capture uses a temporary Git index, atomic
  rename, and does not alter the task repository's live index.
- Kept legacy `workspace/outputs/` data read-only: it is not automatically
  migrated, selected, evaluated, summarized, or cleaned.

### Implemented artifact boundary

```text
runs/<run_id>/
├── manifest.json
├── tasks/<instance_id>/attempts/<attempt_id>/
│   ├── attempt.json
│   ├── result.json
│   ├── patch.diff
│   ├── container-state.json
│   └── termination-request.json   # timeout/cancel only
└── reports/
    ├── summary.json
    └── evaluations/<evaluation_id>/
        ├── predictions.jsonl
        ├── selected-attempts.json
        └── evaluation.json
```

### Implementation-round verification

The regression suites cover:

- one-way attempt allocation and stable selection;
- patch-digest tamper rejection;
- evaluation overlays without attempt mutation;
- cleanup dry-run, exact apply scope, and preservation of finalized/outside
  paths;
- detached Docker ordering (`checkpoint request -> stop -> inspect -> remove`);
- stopped-container retention for incomplete artifacts;
- manifest-derived evaluation, summaries, and status;
- Codex and Pi TERM checkpoints with preserved partial patches; and
- temporary-index patch capture without live-index mutation.

Run them with:

```bash
python3 -B -m unittest -v tests/test_run_artifacts.py
bash tests/test_harness.sh
```

### Deferred from the larger plan after P0

- activation of the P1A SQLite state machine in `run.sh`, crash-recovery
  leases, and status-selective retries;
- structured heartbeats and no-progress/tool/model-stream budgets;
- full failure taxonomy and automated provider/infrastructure classification;
- run-scoped paid-provider proxy and network isolation;
- shadow loop analysis and guarded recovery profiles; and
- bounded parallel workers and global spend/resource circuit breakers.

## Implementation round 2 log — isolated P1A foundation and P1D-0 local gate

Implemented and smoke-qualified on `agent/codex-swebench-runner` on
2026-07-19. This round deliberately stops before changing the live `run.sh`
scheduler.

### P1A state foundation delivered

- Added `scripts/run_state.py` with schema version 1, a forward-only migration
  ledger, `BEGIN IMMEDIATE` transactions, foreign keys, a 30-second busy
  timeout, `synchronous=FULL`, and rollback-journal mode.
- Avoided WAL deliberately. SQLite documents that WAL requires all processes
  on one host, and its July 2026 WAL-reset advisory includes the host's SQLite
  3.45.1 in the affected range. The rollback journal is the conservative
  supervisor default until a fixed SQLite is guaranteed.
- Added the complete state vocabulary and guarded transition surface:
  `planned -> preparing -> running -> checkpointing -> collected ->
  terminal/retryable`.
- Added reconciliation that imports existing manifest tasks and attempts,
  preserves finalized attempt descriptors, and exports scheduler state and an
  append-only event view beside the human-readable manifest.
- Pinned schema-level worker count to one. The schema also reserves the lease,
  event, budget, circuit-breaker, evaluation, and image-lease records needed by
  later P1 integration; their presence is not a claim that `run.sh` uses them
  yet.
- Added four focused smoke tests covering migration/journal mode, lifecycle
  invariants, finalized-attempt reconciliation, and rejection of a newer
  unknown schema before applying DDL.

The focused P1A smoke command is:

```bash
python3 -B -m unittest -v tests/test_run_state.py
```

P1A is an isolated, tested foundation in this round. It does **not** become the
live scheduling authority until P1B wires claims, preparation, attempt
allocation, heartbeats, finalization, and recovery through it and removes the
global shell lock. This boundary avoids running two divergent authorities in
production.

### P1D-0 local qualification evidence

The independent local-volume qualification passed with Distribution 3.1.1
(`registry:3`) at `127.0.0.1:5001`, using a fresh named volume and no proxy or
read-only configuration:

- `/v2/` returned HTTP 200 with
  `Docker-Distribution-API-Version: registry/2.0`.
- The selected `linux/amd64` `hello-world` manifest was pushed, pulled, and
  pulled again after registry restart at digest
  `sha256:d1a8d0a4eeb63aff09f5f34d4d80505e0ba81905f36158cc3970d8e07179e59e`.
- Containerized Skopeo copied the complete `hello-world` multi-platform index
  registry-to-registry with `--all`; source and destination index digests both
  resolved to
  `sha256:c3cbe1cc1aa588a64951ac6286e0df7b27fe2e6324b1001c619bb358770c0178`
  and the destination contained 22 child manifests.
- The real single-platform Matplotlib 25311 image pushed successfully as
  `linux/amd64`; the destination manifest preserved
  `sha256:767d72ed9ee6c6c85fc54ba39457207c64da5ba6fc56d74580ed419fab0e1d2a`
  and remained a Docker v2 single-platform manifest rather than an image
  index.
- Registry logs showed successful blob uploads and manifest writes. The
  disposable container, volume, temporary tags, downloaded qualification
  images, and header file were removed afterward; the operator's pre-existing
  Matplotlib and `hello-world` images were preserved.

This proves the runner's required single-platform and full-index mechanics on
a local registry. P1D-0 remains open until the intended NAS deployment repeats
the persistence and large-image tests and proves TLS hostname/CA validation,
push authorization, reverse-proxy headers and upload limits, concurrency, and
capacity on its actual storage backend. A NAS-only failure is a deployment or
storage-backend failure, not evidence that every architecture is required for
the normal `linux/amd64` benchmark path.

## Upstream test intake audit — 2026-07-21

The latest pull advanced `origin/main` from the shared base `349bcab` to
`b846bea` (`test: add comprehensive test suite for run.sh (165 tests)`). This
side branch remains deliberately unmerged: it is twelve branch commits ahead
of the shared base and carries the manifest, attempt, checkpoint, evaluation-
overlay, Codex, and P1A state architecture that upstream `main` does not have.

The upstream commit is still valuable as a coverage inventory. It adds CLI and
configuration checks; dataset, instance, and bundle tests; fake-Docker run and
failure paths; signal and run-all cases; evaluation, status, summary, and
end-to-end checks. It also fixes two old-runner defects. Both production
behaviors are already correct on this branch: timeout validation occurs before
run creation, and exit cleanup addresses only `ACTIVE_CONTAINER`, avoiding the
old no-match `grep` failure and cross-run broad cleanup.

The 165 tests cannot be copied or counted unchanged. They assume flat
`workspace/outputs/<agent>/<instance>` state, `docker cp`, broad container
selection, mutable `result.json` evaluation folding, and implicit resume rules.
Those expectations conflict with this branch's deliberate contracts.

| Upstream coverage | Side-branch contract | Required intake |
| --- | --- | --- |
| CLI, config, dataset, instance, bundle | Mostly compatible public behavior | Port with current run IDs and error codes |
| Fake-Docker `do_run` paths | Detached lifecycle and attempt bind mount | Rewrite fixture around create/start/wait/checkpoint/finalize |
| Signal handling | One tracked active container | Retain current tests and add idle/active EXIT and INT cases |
| Resume, eval, summary, status | Manifest selection and immutable overlay | Adapt assertions; never mutate an attempt result |
| Partial and Docker cleanup | Exact manifest ownership and retained recovery | Reject broad or flat-path deletion expectations |
| P1 orchestration | Absent upstream | Add new claim, lease, retry, heartbeat, breaker, and image tests |

The verified side-branch baseline on 2026-07-21 is 22 passing tests: five
artifact tests, four SQLite-state tests, and thirteen shell lifecycle tests.
The immediate test goal is behavioral coverage of the current architecture,
not artificial parity with an upstream count. The port should land before
`run.sh` switches scheduling authority so the cutover has both old public-
surface coverage and new transactional invariants.

### Roadmap-status correction

The implementation round above was previously labeled “Phase 1,” but its
delivered scope is roadmap **P0**, not roadmap P1. Queue continuation is the
only material P1 behavior already present. An audit of the branch confirms
that P1 is not complete:

- the P1A SQLite supervisor module exists, but the live shell path does not yet
  use it as scheduling authority;
- the global `flock` still serializes the entire runner;
- there is no status-selective infrastructure retry;
- there are no structured heartbeats or resource/provider metrics; and
- there are no batch budgets or global circuit breakers.

P1 must not be marked complete until every P1F gate below passes. This
correction preserves the implemented work while removing the naming ambiguity
between the first implementation round and the roadmap phase numbers.

### Audited P1 implementation gaps

The 2026-07-19 branch audit found these concrete behaviors that P1 must replace:

- `run.sh` holds one global `/tmp/swe-bench-run.lock` `flock` for the whole
  process.
- `SWEBENCH_REGISTRY` is hard-coded to `swebench` instead of accepting an
  authenticated NAS-registry endpoint and source mapping.
- `MAX_STORAGE_PCT` checks only `df` for the repository filesystem. It does not
  account for the active Docker context, containerd snapshots/content, image
  expansion, evaluation scratch space, or a reservation for the next cohort.
- `SWEBENCH_IMAGE_CACHE` saves and loads one tar per image. The CLI help claims
  this “keeps images off local disk,” but `docker load` necessarily restores an
  unpacked local image, so the claim is false.
- `begin-attempt` runs before dataset/image resolution, storage preflight, and
  image load/pull. A preparation failure can strand a `started` attempt that
  queue continuation will not revisit.
- Evaluation uses `--cache_level instance` and does not pass `--clean True`,
  maximizing instance-image retention.
- The manifest does not yet record the exact image reference, registry source,
  platform, or immutable image digest.

These are implementation facts, not assumptions. P1A-P1E must remove them,
and P1F must test their replacements.

## Operational evidence incorporated on 2026-07-19

The revised phases below incorporate the two most recent operator reports and
screenshots rather than treating image management as a generic cache concern:

- A roughly 250-instance run exhausted local storage. Django instance images
  were reported around 500 MB each, while some Matplotlib instance images
  appeared around 3 GB compressed and much larger after unpacking.
- Docker reported about 150 GB of images on a 250 GB drive. Docker 29's image
  listing distinguishes disk usage from content size; capacity decisions must
  use Docker's structured accounting plus filesystem/containerd accounting,
  not a single displayed size column.
- Docker Hub rate limiting then prevented further pulls. A NAS-hosted writable
  registry is therefore part of the deterministic image source and recovery
  design, not merely an optimization.
- One agent correctly decoded the `swebench/` prefix and then searched for the
  misspelled `swebbench/` prefix. Image discovery, movement, digest validation,
  and cleanup must be deterministic control-plane code using structured Docker
  output or APIs; an LLM must never grep or move Docker-managed storage.
- The runner currently uses per-image `docker save`/`docker load` archives and
  `--cache_level instance`. Separate archives duplicate shared parent layers,
  still require local unpacked storage, and instance-level retention conflicts
  with the limited local disk.
- Image/storage preparation currently occurs after an attempt is allocated.
  A pull, rate-limit, registry, or capacity failure can therefore leave a
  `started` attempt that queue continuation will skip. P1 moves preparation
  before model-attempt allocation so infrastructure failures do not become
  model DNFs or consume pass@1.

The exact investigated image was
`swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-25311:latest`, with
observed digest
`sha256:767d72ed9ee6c6c85fc54ba39457207c64da5ba6fc56d74580ed419fab0e1d2a`.
Registry inspection confirmed that this exact canary is a single-platform
Docker v2 manifest for `linux/amd64`, not a multi-platform image index. Docker
selecting only the host architecture when pulling a genuinely multi-platform
image is expected behavior and does not prevent an OCI registry from storing
the complete index. P1 therefore treats `linux/amd64` as the explicit
SWE-bench execution contract while preserving enough provenance to distinguish
a source index from its selected platform manifest.

The audit measured about 3.31 GB of registry content, 11.41 GB of local Docker
disk usage, and roughly 7 GB of visible image filesystem content; the targeted
`lib/matplotlib/tests/test_pickle.py::test_complete` evaluation passed. Adjacent
Matplotlib images had materially different unique compressed sizes, and Django
images were much smaller. This is the required real-image canary in P1F; the
plan must not assume images in the same repository family have the same storage
profile.

The same operational audit also recovered Docker Desktop after a transient WSL
integration timeout (`Wsl/Service/0x8007274c`, Windows `WSAETIMEDOUT`). After
Resource Saver was disabled and the integration restarted, both the Ubuntu and
`docker-desktop` WSL 2 distributions were running and `docker version` reached
the Docker 29.6.1 server. Automatic launch at Windows sign-in is an operator
preference, not a runner requirement. P1 must use an engine/API readiness check
rather than infer that integration is permanently disabled from a single
missing or stale socket observation.

## Motivation and benchmark limitations

The operational context motivating this plan is a partial 260/500 run with 12
DNFs. Several early DNFs appeared to be reasoning loops, and the runner used a
one-hour wall-clock timeout per task. This makes each loop potentially consume
an hour while collapsing several different failure classes into one outcome.

Important interpretation constraints:

- SWE-bench Verified contains 500 tasks. OpenAI's original annotations labeled
  196 tasks as under 15 minutes for an experienced engineer and 45 tasks as
  over one hour. A one-hour inference limit is therefore an experimental
  policy, not a benchmark requirement.
- The official SWE-bench harness's default 1,800-second timeout applies to test
  execution during evaluation. It is not a canonical agent-solving timeout.
- OpenAI no longer recommends SWE-bench Verified as a frontier coding metric
  because of contamination and flawed tasks/tests. A February 2026 audit found
  material issues in 59.4% of a 138-task subset that frontier models frequently
  failed.
- OpenAI's July 2026 audit also estimated that roughly 30% of SWE-Bench Pro is
  broken and retracted its earlier recommendation to move from Verified to Pro.

Consequently, our results should be framed as performance of a pinned system on
a legacy regression benchmark. They remain useful for comparing our own
runner, model, and scaffold configurations when experimental controls are held
constant.

## P0 risk disposition and remaining risks

### Addressed in P0: destructive cleanup scope

The old flat-output cleanup hazard is closed. `--cleanup-partial` now requires
an agent, resolves an exact run, obtains deletion candidates from its manifest,
defaults to dry-run, and requires `--apply`. Regression tests prove that
finalized, selected, legacy, and outside paths are preserved.

### Addressed in P0: timeout artifact loss

The work container now runs detached. Timeout and operator-cancel paths write a
termination request, signal the agent entrypoint, checkpoint the working tree,
capture Docker state, and finalize metadata before removal. An incomplete
checkpoint retains the stopped container instead of discarding recoverable
work.

### Partially addressed in P0: continuation and retry

`--resume` is now manifest-backed queue continuation: it runs only untouched
tasks and never retries an existing model attempt. It deliberately does not
yet perform status-selective infrastructure retry or Codex session
continuation; those distinct operations remain in P1B.

### Addressed in P0: stale attempt contamination

Attempts now have immutable, uniquely allocated directories. Evaluation uses
the first explicitly selected eligible attempt, verifies patch/result digests,
and writes a separate immutable evaluation overlay.

### Remaining for P1/P3: insufficient failure attribution

The current result schema cannot reliably distinguish:

- reasoning loop or no-progress stall;
- productive wall-clock exhaustion;
- hung tool command;
- authentication, rate-limit, or provider failure;
- stream-idle failure;
- OOM, signal, or container-runtime failure;
- operator cancellation;
- patch-extraction failure; and
- official evaluator failure.

### Remaining for P2: credential exposure

The adapter defaults to a local Responses-compatible endpoint and a fake key.
Pointing it directly at a paid endpoint would place the credential inside a
container that executes repository-controlled code with broad filesystem
access. Mounting a personal Codex `auth.json` would be even more sensitive and
must not become the paid-run solution.

## Experimental tracks

Keep the following profiles separate and include the profile name and hash in
every run manifest.

| Profile | Purpose | Attempts | Loop behavior |
| --- | --- | ---: | --- |
| `baseline-local` | Preserve the local-model comparison | 1 | Passive telemetry only |
| `baseline-codex-api` | Measure the unmodified Codex CLI scaffold | 1 | Passive telemetry only |
| `guarded-codex-api` | Optimize operational completion and spend | Declared | Loop detection and declared recovery |

A loop detector that nudges, replans, stops, or restarts an agent changes the
scaffold. It is a valid system component, but it must not be silently folded
into the baseline.

If direct language-model comparison becomes a goal, add a separately pinned
mini-SWE-agent adapter as the model-centric control. Raw Codex CLI results are
full-system/scaffold results.

## Target architecture

Keep `run.sh` as a compatibility wrapper, but move lifecycle state into a small
Python supervisor backed by SQLite.

Suggested artifact layout:

```text
runs/<run_id>/
├── manifest.json
├── state.sqlite
├── events.jsonl
├── reports/
└── tasks/<instance_id>/
    ├── selected_attempt.json
    └── attempts/<attempt_id>/
        ├── meta.json
        ├── result.json
        ├── problem_statement.txt
        ├── trajectory.jsonl
        ├── agent_output.txt
        ├── agent_stderr.txt
        ├── patch.diff
        ├── container_inspect.json
        └── checkpoints/
```

Every attempt directory is immutable after finalization. A new attempt always
gets a new ID. Aggregate reports derive from the manifest and database; they do
not scan arbitrary non-empty patch files.

### Run manifest

At minimum, record:

- run ID, creation time, operator, and runner Git commit;
- dataset name, revision/hash, split, instance IDs, and fixed order;
- agent, profile, model, provider, CLI version, and configuration hash;
- prompt hash and exact prompt template;
- container image tags and digests;
- CPU, memory, GPU, and concurrency allocation;
- model-call, command, no-progress, task, evaluation, and run-level budgets;
- retry policy and maximum attempts;
- network and web-search policy; and
- selected attempt per instance.

### Durable state machine

```text
planned -> preparing -> running -> checkpointing -> collected -> terminal
                               \-> interrupted/retryable
```

Use atomic leases so the supervisor can recover after WSL, Docker, terminal,
or host-process interruption without rerunning completed work.

Define these operations separately:

- **Queue continuation:** continue pending work in an interrupted run.
- **Infrastructure retry:** retry a prespecified transient failure without
  changing model/scaffold configuration.
- **Agent retry:** create another model attempt and count it accordingly.
- **Session continuation:** continue a persisted Codex session under a declared
  enhanced policy and the original total budget.

## Timeout and checkpoint design

Track independent budgets instead of relying on one outer hour:

1. model stream-idle timeout;
2. individual tool-command timeout;
3. soft no-progress window;
4. hard agent wall-clock limit;
5. official evaluator test timeout; and
6. whole-run token, time, request, and spend limits.

The container lifecycle should be:

1. Create and start the container under supervisor control.
2. Stream structured Codex events to the attempt directory.
3. Emit heartbeats containing the last model/tool activity and current usage.
4. On soft stop, ask the entrypoint supervisor to terminate Codex and run a
   single idempotent checkpoint routine.
5. Capture a binary-safe diff, result metadata, and session/thread identifiers.
6. Inspect Docker state, including exit code, OOM flag, error, and timestamps.
7. Escalate to hard kill only after the checkpoint grace period.
8. Preserve all partial artifacts before removing the container.

Baseline runs should not resume agent failures after the hard limit. Queue
continuation and prespecified infrastructure retries may continue without
changing the one-attempt model policy.

## Paid Codex provider design

Do not mount personal ChatGPT/Codex credentials or inject a broad API key into
the task container.

Use a host-side or sidecar Responses proxy:

- The proxy process owns the upstream credential.
- The task receives only a run-scoped proxy credential.
- Permit only the required Responses endpoint.
- Restrict the allowed model and reasoning effort.
- Enforce per-attempt request, token, time, and spend limits.
- Record metering without recording secrets.
- Put task containers on an isolated network that can reach the proxy but not
  the public internet.
- Keep the proxy on a separate egress-enabled network for upstream API access.

This follows the same credential-isolation pattern as OpenAI's Codex Responses
API proxy while adding the quota enforcement needed for benchmark tasks that
could otherwise consume arbitrary paid capacity.

## Loop analysis and guarded recovery

### Shadow mode first

Ingest the existing partial-run trajectories, classify the 12 DNFs, and run a
passive detector before enabling any intervention.

Candidate signals:

- repeated normalized command and observation hashes;
- repeated tool errors;
- alternating two-action cycles;
- consecutive agent monologues without tool progress;
- repeated context-window failures;
- unchanged Git diff hash over a rolling window;
- no newly inspected files or hypotheses;
- repeated identical failing-test signatures; and
- high token consumption without a new edit, test, or conclusion.

The shadow detector should emit evidence and `model_loop_suspected`, but must
not alter the baseline run.

### Guarded profile

After manually auditing triggers and estimating false positives:

1. Allow one structured recovery action, such as state summarization and
   replanning.
2. Continue within the original outer budget.
3. If the detector triggers again, checkpoint and terminate.
4. Record the trigger evidence, recovery action, added token/time cost, and
   whether recovery produced a valid or resolved patch.

Report loop-trigger rate, false-positive rate, and recovery yield separately
from resolution rate.

## Failure taxonomy

Use one mutually exclusive primary outcome plus optional secondary signals.

Suggested primary outcomes:

- `resolved`
- `agent_wrong_patch`
- `agent_no_patch`
- `agent_premature_exit`
- `agent_timeout_productive`
- `agent_timeout_loop_or_stall`
- `agent_tool_hang`
- `agent_context_step_or_cost_limit`
- `provider_authentication_error`
- `provider_rate_limit`
- `provider_transient_error`
- `container_oom`
- `container_runtime_error`
- `operator_cancelled`
- `patch_capture_error`
- `evaluation_harness_error`
- `benchmark_task_questionable`

Only prespecified infrastructure/provider-transient failures should be
automatically retryable in a pass@1 baseline. Ordinary agent failures remain
failures.

## Evaluation funnel for our usage

### 1. Smoke set

Run 8-12 diverse instances to validate:

- endpoint and model capability preflight;
- container lifecycle and signal handling;
- patch checkpointing;
- attempt isolation;
- gold-patch evaluation; and
- reporting completeness.

### 2. Frozen core pilot

Select 50 instances approximately proportional to the official difficulty
distribution and stratified across repositories. A practical target is about
20 easy, 26 medium, and 4 hard tasks, subject to repository balancing.

Store the selected IDs and sampling seed in source control. Do not select
alphabetically or change the pilot based on observed outcomes.

### 3. Separate stress slice

Maintain 12 long or loop-prone tasks for watchdog and failure-classifier
tuning. Do not combine this stress slice with the core pilot score.

### 4. Matched comparison

Run baseline and guarded profiles on identical instance IDs with identical
outer budgets, hardware, prompt, evaluator, and concurrency. Any retry or
recovery mechanism must be declared and its total cost counted.

### 5. Confirmation and full run

- Expand to a fixed 100-instance confirmation set only if the paired pilot
  shows a meaningful reliability or efficiency improvement.
- Run all 500 only after freezing the candidate configuration and only if the
  legacy full-set number is still valuable.

For paid Codex pilots, start with attended batches of five. Pause automatically
after a credential failure, two consecutive infrastructure failures, or a
configured run-budget threshold.

## Reporting

For a full run, the comparable headline is strict `resolved / 500`. Missing,
timed-out, and DNF tasks must not disappear from the denominator.

For pilots, report `resolved / planned` and clearly label the fixed subset.
Also report:

- completion and DNF rate by primary reason;
- results by repository and difficulty;
- p50 and p95 agent wall time;
- input, cached input, output, and reasoning tokens;
- provider requests, retries, and rate-limit events;
- total cost and cost per resolved task;
- infrastructure retry count;
- loop-trigger rate, false positives, and recovery yield; and
- strict lower/upper bounds while a run is incomplete.

Preserve all trajectories. If a profile uses multiple attempts or a selector,
retain every rollout and document how the selected patch was chosen without
using hidden SWE-bench tests or evaluation feedback.

## Implementation sequence

### P0 — Safety and artifact correctness: complete

Freeze the existing manifest-owned immutable attempts, digest checks,
checkpointing, exact cleanup, evaluation overlays, and signal-aware lifecycle.
Preserve the five Python artifact regressions and thirteen shell lifecycle
regressions as the floor for all later phases.

### Regression intake gate — immediate prerequisite

Before the P1A/P1B live cutover, adapt the reusable intent from upstream
`b846bea` to this branch. Prioritize no-Docker CLI/configuration/dataset tests,
then a manifest-aware fake-Docker lifecycle, then run/evaluation/reporting and
exact-cleanup integration cases. Preserve the four P1A smoke tests and add P1
state/lease/failure cases incrementally. Do not import legacy assertions for
flat outputs, `docker cp`, broad cleanup, mutable evaluation results, or
implicit retries, and do not report upstream's 165-test result as this branch's
coverage.

### P1 — Durable orchestration and bounded image lifecycle: first priority

#### P1A — SQLite supervisor and state reconciliation

1. Add `state.sqlite` with explicit, forward-only schema migrations.
2. Make SQLite transactions the scheduling authority while retaining manifests
   as durable, human-readable audit exports.
3. Model the lifecycle as `planned -> preparing -> running -> checkpointing ->
   collected -> terminal/retryable`.
4. Import and reconcile existing manifest-backed runs without mutating
   finalized attempts.
5. Keep the default worker count at one.

#### P1B — Leases, recovery, and retry semantics

1. Replace the global process lock with run- and attempt-scoped owner/expiry
   leases.
2. Reclaim expired `preparing` or `running` work after Docker, WSL, or
   supervisor failure.
3. Keep four operations distinct: queue continuation, infrastructure retry,
   agent retry, and Codex session continuation.
4. Complete image and storage preparation before allocating a model attempt.
   Preparation failures must not consume pass@1 or create a model DNF.
5. Make retry eligibility status-selective and auditable; do not automatically
   retry baseline agent failures.

#### P1C — Structured observability

1. Emit typed events and periodic heartbeats.
2. Record the last model event, tool event, diff change, provider usage, Docker
   resources, and elapsed budgets.
3. Record the exact source image reference, manifest media type, nullable
   source-index digest, selected platform-manifest digest, requested and actual
   OS/architecture/variant, official `TestSpec.env_image_key`, registry source,
   copy policy, and cache policy. For this benchmark the requested execution
   platform is explicitly `linux/amd64`.
4. Capture Docker usage before pull, after pull, after evaluation, and after
   eviction.
5. Derive status from SQLite instead of scanning artifacts.

#### P1D — Deterministic image and NAS-registry controller

1. Add a bounded Docker readiness preflight before storage or image work. It
   must retry while the engine is starting, prove API readiness with
   `docker version`/`docker info`, report the active context and server version,
   and distinguish an absent Desktop process, a starting engine, a missing or
   stale WSL socket, and an unreachable daemon. It must not start or restart
   Docker Desktop, shut down WSL, or allocate an attempt on the operator's
   behalf.
2. Require Docker 29.6 or newer before automating size decisions because
   recent Docker 29 releases corrected image-size accounting behavior.
3. Reconcile the active Docker context, image-store backend,
   `/var/lib/containerd`, all images, and daemon storage before scheduling.
4. Use a structured API, JSON output, and exact reference filters such as
   `docker image ls --all --filter "reference=swebench/sweb.*" --format json`.
   Never grep names or manipulate Docker/containerd internal files directly.
5. Resolve official SWE-bench references and immutable digests. Resolve and
   record both the source index and selected `linux/amd64` manifest when the
   source is multi-platform; do not mistake a host-selected pull for missing
   registry data.
6. Retire the per-image `docker save`/`docker load` tar cache. Those archives
   duplicate shared parent layers and do not eliminate local unpacked-image
   storage.
7. Add a configurable writable NAS OCI registry seeded by digest. An optional
   pull-through mirror may be a separate service, but a proxy registry cannot
   accept pushes and remains subject to Docker Hub fair-use limits.
8. Keep registry credentials on the host; never expose them to task
   containers.
9. Keep Docker Desktop's managed disk and native Docker/containerd active image
   stores on supported local storage. NFS is forbidden as Docker's
   `data-root`, including outside rootless mode; with the Docker 29 containerd
   image store, changing Docker's `data-root` does not relocate containerd
   content or snapshots. NAS capacity belongs behind the registry service, not
   underneath the runner's active Docker image store. Moving a Docker Desktop
   disk must use Docker Desktop's supported disk-location control and a local
   drive.
10. Start with this policy for a 250 GB local drive:
   - use SWE-bench's documented “at least 120 GB free” prerequisite as the
     initial scheduling floor;
   - target no more than about 100 GB of retained Docker images;
   - reserve projected next-image expansion plus evaluation scratch space;
   - allow at most three concurrent/cached Matplotlib instance images; and
   - reserve a separate, small quarantine budget for incomplete stopped
     containers.

Implement P1D in four reviewable subphases:

- **P1D-0 — Registry qualification gate:** qualify the registry independently
  of the runner before making it a scheduling dependency. For a new
  Distribution deployment, use the current `registry:3` release rather than
  starting new infrastructure on `registry:2`. Start with a fresh local named
  volume and a normal writable endpoint: no `proxy:` block and no maintenance
  read-only mode. Require all of the following:
  1. `/v2/` returns `200` or an intentional `401` challenge with the
     `Docker-Distribution-API-Version: registry/2.0` header;
  2. a tiny scratch image survives tag, push, digest verification, pull, and
     registry restart;
  3. Skopeo can copy registry-to-registry with preserved digests;
  4. a pinned multi-platform fixture preserves its index and child manifests
     with an explicit `--all` copy, while the normal benchmark path copies and
     verifies only its selected `linux/amd64` platform manifest;
  5. the single-platform Matplotlib 25311 canary completes the same round trip
     without being misclassified as a multi-platform index and without proxy,
     upload-size, timeout, capacity, or storage errors;
  6. TLS hostname/CA validation, authentication push scope, reverse-proxy
     `Host`/`X-Forwarded-*` handling, and registry logs are verified; and
  7. the tests pass again after moving from the local volume to the intended
     NAS backend.

  If the local-volume tests pass and the NAS-backed tests fail, classify the
  storage backend or mount as the fault rather than changing runner logic.
  Run the registry on storage supported by that registry deployment: prefer a
  filesystem local to the NAS service or a supported S3-compatible backend.
  A network-mounted registry filesystem, if contemplated, needs explicit
  vendor support plus permissions, atomic-operation, concurrency, restart, and
  large-upload qualification. This does not permit NFS as Docker's
  `data-root`. Keep the runner bound to a registry capability/configuration
  interface, not to Distribution itself, so Harbor or another OCI registry
  can replace it without changing scheduling.
- **P1D-1 — Host readiness and configurable digest-pinned NAS registry:**
  bounded Docker API preflight, configuration, credential handling,
  official-reference and platform resolution, digest-pinned pull, and
  structured inventory.
- **P1D-2 — Single-flight registry seeding on miss:** one owner seeds while
  other workers wait, with bounded backoff and no retry stampede.
- **P1D-3 — Destination-digest verification and source mapping:** verify the
  selected destination manifest digest and record the official-to-NAS mapping,
  source-index provenance, and requested/actual platform before scheduling.

The registry-miss flow is:

1. Resolve the official source object and determine whether it is a
   single-platform manifest or an image index.
2. Select `linux/amd64` and record the source-index digest, when present, plus
   the selected platform-manifest digest.
3. Check the NAS registry for the selected expected digest.
4. Acquire a single seeding lease.
5. Copy registry-to-registry with a tool such as Skopeo, avoiding a local
   Docker unpack during seeding.
6. Verify the destination platform-manifest digest and, only for an explicitly
   requested full-index copy, its index and child manifests.
7. Pull the NAS reference locally by the selected digest and verify the actual
   platform before scheduling.

#### P1E — Cohort execution, eviction, and circuit breakers

1. Group tasks by the official `TestSpec.env_image_key`, not by repository
   string.
2. Preserve this image lifecycle:

   `resolve digest -> reserve local space -> check/seed NAS -> pull from NAS
   by digest -> solve -> checkpoint/finalize -> evaluate -> persist immutable
   evaluation overlay -> remove container -> release image lease -> remove the
   exact local image -> remeasure storage`

3. Evaluate while the image is still local. Do not delete on solve-container
   shutdown because evaluation still needs the image.
4. Use the official evaluator's `--cache_level env --clean True` where its
   semantics apply, or perform explicit exact-digest removal after the durable
   overlay is written. Changing only to `cache_level=env` is insufficient:
   images pulled before evaluator startup are preexisting, and `clean=False`
   preserves them.
5. Never run `docker system prune -a` from the runner.
6. Add global circuit breakers for storage reservation/ENOSPC, Docker Hub 429,
   authentication failure, registry outage, digest mismatch, and Docker daemon
   loss. Pause scheduling with bounded global backoff instead of stampeding
   retries.
7. Inspect evaluator `error_ids` and per-instance logs. A zero evaluator exit
   code does not prove every task evaluated successfully.
8. Classify infrastructure results explicitly as:
   - `image_pull_rate_limited`;
   - `image_pull_authentication_error`;
   - `image_not_found`;
   - `registry_unavailable`;
   - `image_digest_mismatch`;
   - `image_storage_exhausted`;
   - `docker_daemon_unavailable`; or
   - `evaluation_harness_error`.

   None of these counts as a model DNF.
9. Delete only the exact local reference/digest, without force, after no
   running or retained stopped container and no worker/evaluator holds an image
   lease. Docker must retain shared layers still referenced by other images.
10. If an incomplete attempt retains a stopped container, or another worker or
    evaluator still leases the image, defer eviction and charge it to the
    quarantine budget.

Implement P1E in three reviewable subphases:

- **P1E-1 — Solve/evaluate image lease:** one lease spans both stages and
  protects the image until evaluation artifacts are durable.
- **P1E-2 — Exact post-evaluation local eviction:** non-force exact removal,
  storage remeasurement, and proof that unrelated images/shared layers remain.
- **P1E-3 — Quarantine and storage-pressure exceptions:** bounded retention for
  incomplete work and predictable behavior when reservations fail.

#### P1F — Completion gate

P1 is complete only after all of the following pass:

- crash recovery at every state transition;
- two-supervisor contention and lease-expiry recovery;
- queue continuation without rerunning completed work;
- status-selective infrastructure retry without automatic agent retry;
- simulated Docker Hub 429, ENOSPC, registry outage, and digest mismatch;
- Docker readiness behavior for an engine that is starting, a missing/stale
  WSL socket, daemon loss during a run, and recovery without allocating a
  model attempt;
- acceptance of the correct `swebench/sweb...` namespace and rejection of the
  misspelled `swebbench/...` namespace;
- a single-platform manifest round trip, a multi-platform full-index round
  trip, and an explicit `linux/amd64` selected-manifest round trip with all
  source and destination digests recorded;
- exact image eviction, shared-layer preservation, and no unrelated-image
  removal;
- proof that Docker/containerd active storage is not placed on NFS and that
  NAS capacity is consumed through the qualified registry boundary;
- enforcement of the stopped-container quarantine cap;
- a real canary using Matplotlib issue 25311 and its pinned digest;
- storage remaining inside budget across multiple image cohorts; and
- proof that SQLite, scoped leases, heartbeats, budgets, and circuit breakers
  exist and the global lock is gone.

### P2 — Provider profiles and credential isolation

#### P2A — Named profiles and preflight

Add named local, baseline Codex, and separately named guarded profiles plus a
provider/model capability preflight. Keep the pinned stable Codex CLI version
until a deliberate benchmark-version change is approved.

#### P2B — Isolated provider path

Add a host- or sidecar-hosted Responses proxy and an isolated Docker network.

#### P2C — Run-scoped quotas

Issue run-scoped proxy credentials and enforce request, token, wall-time, and
spend quotas before scheduling more work.

#### P2D — Security gate

Test that task processes cannot access upstream provider credentials or bypass
the metered proxy.

### P3 — Loop analysis and guarded experiments

#### P3A — Trajectory normalization and labels

Normalize existing JSONL trajectories into the typed event schema and label
the partial run's twelve DNFs with the new taxonomy.

#### P3B — Shadow-only detector

Implement loop/no-progress detection in shadow mode so it cannot change the
baseline outcome.

#### P3C — Contradiction fixture and deterministic control plane

Include the observed Qwen behavior—correctly identifying `swebench/` and then
grepping for `swebbench/`—as a detector fixture. Keep image discovery and all
other infrastructure operations outside the agent and deterministic.

#### P3D — Guarded recovery audit

Audit false positives and threshold stability before permitting one bounded,
explicitly named recovery action.

#### P3E — Frozen matched pilot

Run a paired frozen pilot comparing baseline and guarded profiles before
scaling.

### P4 — Bounded throughput

#### P4A — Separate worker pools

Add bounded solve and evaluation pools, initially one worker in each.

#### P4B — Resource tokens

Gate scheduling on CPU, memory, provider capacity, and projected image
expansion tokens. Pin capacity assumptions in the run manifest.

#### P4C — Staged scale-up

Scale from attended batches of five, to an 8–12 task smoke set, to a fixed
50-task pilot, and then a 100-task confirmation set.

#### P4D — Full-run gate

Run all 500 only after storage, retry, cost, isolation, reproducibility, and
full-reporting gates pass.

## Acceptance gates before a paid pilot

- Cleanup cannot remove data outside an exact run/attempt target.
- A forced timeout preserves a valid checkpoint patch and structured result.
- Exit 124, exit 137, OOM, provider failure, and operator cancel are distinct.
- A stale prior-attempt patch can never be selected implicitly.
- Restarting the supervisor does not rerun completed tasks.
- Baseline agent failures are not automatically retried.
- The proxy credential is run-scoped and the upstream key is unavailable to
  the task container.
- Global token/time/spend limits halt scheduling before the next task.
- The smoke set passes gold-patch evaluation.
- Status reports planned, pending, running, terminal, and DNF counts against a
  known denominator.

## Primary references

- [Why SWE-bench Verified no longer measures frontier coding capabilities](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)
- [Separating signal from noise in coding evaluations](https://openai.com/index/separating-signal-from-noise-coding-evaluations/)
- [Introducing SWE-bench Verified](https://openai.com/index/introducing-swe-bench-verified/)
- [SWE-bench Verified leaderboard and comparison guidance](https://www.swebench.com/verified.html)
- [SWE-bench evaluation harness](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/run_evaluation.py)
- [SWE-bench Docker setup guide](https://www.swebench.com/SWE-bench/guides/docker_setup/)
- [SWE-bench FAQ](https://www.swebench.com/SWE-bench/faq/)
- [SWE-bench Docker cleanup implementation](https://raw.githubusercontent.com/SWE-bench/SWE-bench/main/swebench/harness/docker_utils.py)
- [SWE-bench experiment checklist](https://github.com/SWE-bench/experiments/blob/main/checklist.md)
- [SWE-bench experiment artifacts and trajectory guidance](https://github.com/SWE-bench/experiments)
- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex Responses API proxy](https://github.com/openai/codex/blob/main/codex-rs/responses-api-proxy/README.md)
- [Codex Action security guidance](https://github.com/openai/codex-action/blob/main/docs/security.md)
- [AgentLens: Revealing the Lucky Pass Problem in SWE-Agent Evaluation](https://arxiv.org/abs/2605.12925)
- [Docker containerd image store](https://docs.docker.com/engine/storage/containerd/)
- [Docker daemon data directories and the separate containerd store](https://docs.docker.com/engine/daemon/)
- [Docker Desktop Resource Saver](https://docs.docker.com/desktop/use-desktop/resource-saver/)
- [Docker Desktop WSL 2 backend and managed disk location](https://docs.docker.com/desktop/features/wsl/)
- [Docker multi-platform image and manifest behavior](https://docs.docker.com/build/building/multi-platform/)
- [Docker NFS `data-root` limitation](https://docs.docker.com/engine/security/rootless/troubleshoot/)
- [Windows `WSAETIMEDOUT` error definition](https://learn.microsoft.com/en-us/windows/win32/winsock/windows-sockets-error-codes-2)
- [Docker Engine 29 release notes and 29.6 size-accounting fixes](https://docs.docker.com/engine/release-notes/29/)
- [Docker system disk-usage reporting](https://docs.docker.com/reference/cli/docker/system/df/)
- [Docker image listing and reference filters](https://docs.docker.com/reference/cli/docker/image/ls/)
- [Docker exact image removal](https://docs.docker.com/reference/cli/docker/image/rm/)
- [Docker Hub pull usage and limits](https://docs.docker.com/docker-hub/usage/pulls/)
- [Distribution registry configuration and proxy limitations](https://distribution.github.io/distribution/about/configuration/)
- [Distribution registry deployment and writable push examples](https://distribution.github.io/distribution/about/deploying/)
- [Distribution registry storage drivers](https://distribution.github.io/distribution/storage-drivers/)
- [Distribution registry releases](https://github.com/distribution/distribution/releases)
- [Distribution pull-through cache recipe](https://distribution.github.io/distribution/recipes/mirror/)
- [Skopeo registry-to-registry image copy and multi-architecture policy](https://github.com/containers/skopeo/blob/main/docs/skopeo-copy.1.md)
- [SQLite write-ahead logging requirements and 2026 WAL-reset advisory](https://www.sqlite.org/wal.html)
