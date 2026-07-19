# SWE-bench Harness Revamp Plan

- **Status:** P0 safety and correctness implemented; P1-P4 remain planned.
- **Target:** `agent/codex-swebench-runner`
- **Last researched:** 2026-07-18
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

## Phase 1 implementation log — P0 safety and correctness

Implemented on `agent/codex-swebench-runner` on 2026-07-19. This phase keeps
`run.sh` as the compatibility wrapper and introduces a JSON-manifest artifact
layer. The SQLite supervisor, leases, richer failure classifier, paid-provider
proxy, loop detector, and bounded concurrency remain deferred to P1-P4.

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

### Phase 1 verification

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

### Deferred from the larger plan

- SQLite state machine, crash-recovery leases, and status-selective retries;
- structured heartbeats and no-progress/tool/model-stream budgets;
- full failure taxonomy and automated provider/infrastructure classification;
- run-scoped paid-provider proxy and network isolation;
- shadow loop analysis and guarded recovery profiles; and
- bounded parallel workers and global spend/resource circuit breakers.

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

## Current-harness risks to address first

### Destructive cleanup scope

`do_cleanup_partial` in [`run.sh`](run.sh) still assumes the old flat output
layout. Under the current `outputs/<agent>/<instance_id>/` layout, it can see an
agent root as incomplete and remove the entire agent output tree.

Do not use `--cleanup-partial` until this is corrected and covered by tests.
The replacement should be dry-run by default and resolve exact attempt paths
from a run manifest rather than discovering deletion targets with broad globs.

### Timeout artifact loss

The host wraps attached `docker run` with GNU `timeout`. On timeout it removes
the container immediately. The Codex entrypoint extracts `patch.diff` only
after `codex exec` returns, so a killed run loses the in-progress working-tree
patch even though some JSONL and stderr output may already have reached the
bind mount.

Timeout and operator-cancel paths must checkpoint the working tree, finalize
structured metadata, inspect the container, and only then remove it.

### Resume is only skip

The current `--resume` option skips any instance that already has
`result.json`, including `timed_out`, `agent_error`, `invalid_result`, and
`no_patch`. It does not resume a durable queue or a Codex session, and it cannot
select retryable statuses.

### Stale attempt contamination

Reruns reuse the same instance directory. Old patches and result fields can
survive a failed rerun, and evaluation currently selects any non-empty patch.
Attempts must be immutable, and evaluation must consume only an explicitly
selected attempt recorded in the run manifest.

### Insufficient failure attribution

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

### Credential exposure

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

### P0: Safety and correctness

1. Fix cleanup scope and add dry-run behavior.
2. Add immutable run/attempt paths and a manifest.
3. Make evaluation select an explicit eligible attempt.
4. Add timeout/cancel checkpointing and Docker-state capture.
5. Add tests for destructive cleanup, stale artifacts, timeout patch recovery,
   signal handling, OOM, and interrupted collection.

### P1: Durable orchestration and observability

1. Add the Python/SQLite supervisor and state machine.
2. Replace the global process lock with run- and attempt-scoped leases.
3. Implement queue continuation and status-selective retry.
4. Add per-attempt structured events, heartbeats, and resource/usage metrics.
5. Add batch-size, global-budget, and circuit-breaker controls.

### P2: Provider profiles and credential isolation

1. Add named local, Codex baseline, and guarded profiles.
2. Add provider/model capability preflight.
3. Add the metered Responses proxy and isolated Docker network.
4. Verify that task processes cannot access upstream credentials.
5. Keep the currently pinned stable Codex CLI version until a deliberate
   benchmark-version change is approved.

### P3: Loop analysis and experiments

1. Normalize existing JSONL trajectories into the new event schema.
2. Label the partial run's DNFs with the new taxonomy.
3. Implement shadow-only loop detection.
4. Audit detector triggers and set thresholds.
5. Add the separately named guarded recovery profile.
6. Run the frozen matched pilot before scaling.

### P4: Bounded throughput

1. Add a bounded worker pool with separate inference and evaluation limits.
2. Pin worker count and provider-capacity assumptions in the run manifest.
3. Use resource tokens for memory, CPU, GPU, and provider rate limits.
4. Add task-specific logs and run-level aggregation safe for concurrency.
5. Keep default concurrency at one until isolation and reproducibility tests
   pass.

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
- [SWE-bench experiment checklist](https://github.com/SWE-bench/experiments/blob/main/checklist.md)
- [SWE-bench experiment artifacts and trajectory guidance](https://github.com/SWE-bench/experiments)
- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex Responses API proxy](https://github.com/openai/codex/blob/main/codex-rs/responses-api-proxy/README.md)
- [Codex Action security guidance](https://github.com/openai/codex-action/blob/main/docs/security.md)
- [AgentLens: Revealing the Lucky Pass Problem in SWE-Agent Evaluation](https://arxiv.org/abs/2605.12925)
