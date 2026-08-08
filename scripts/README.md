# Scripts — Evaluation Utilities

Standalone utilities for generating and validating evaluation status reports
from SWE-bench harness results. These scripts are **not** part of the
orchestrator package; they are custom tooling for post-run analysis and CI
validation.

## Scripts

### `write_status.py`

Generates a `status.json` summary from swebench harness evaluation results.

**Purpose:** Produce a machine-readable status report that captures all
instances — including those that never reached the harness (e.g.,
`no_patch`, `timed_out`, `container_error`).

**Usage:**
```bash
EVAL_DIR=/path/to/workspace/outputs/pi AGENT_NAME=pi PREDS=predictions.jsonl python scripts/write_status.py
```

**Environment Variables:**

| Variable | Required | Description |
|---|---|---|
| `EVAL_DIR` | Yes | Root output directory (e.g., `workspace/outputs/pi`) |
| `AGENT_NAME` | Yes | Agent identifier used in report filenames |
| `PREDS` | Yes | Path to predictions file |
| `OUT_JSON` | No | Output path (defaults to `$EVAL_DIR/status.json`) |

**Output format (`status.json`):**
```json
{
  "agent": "pi",
  "schema_version": 2,
  "total_instances": 261,
  "resolved": 45,
  "unresolved": 180,
  "errors": 36,
  "resolved_ids": ["django__django-7530", ...],
  "unresolved_ids": [...],
  "error_ids": [...],
  "instances": {
    "django__django-7530": {
      "status": "resolved",
      "local_eval": "resolved",
      "elapsed_seconds": 120,
      "patch_bytes": 42
    },
    ...
  }
}
```

**How it works:**
1. Reads the swebench report JSON from `$EVAL_DIR/eval/<agent>.<agent>.json`
2. Collects all instance directories (including edge cases that never reached harness)
3. Reads per-instance `result.json` files for detailed metadata
4. Writes structured `status.json` with aggregate counts and per-instance details

---

### `test_status_schema.py`

Validates that `status.json` has the required top-level fields.

**Purpose:** CI test **T4-26** — ensures status reports conform to the expected schema.

**Usage:**
```bash
REPO_ROOT=/path/to/swe-bench python scripts/test_status_schema.py
```

**Environment Variables:**

| Variable | Required | Description |
|---|---|---|
| `REPO_ROOT` | No | Repository root (defaults to `.`) |

**Output:**
- `OK:<count>:instances` — validation passed
- `MISSING:<keys>` — one or more required keys are absent (exits 1)
- `ERROR:<message>` — unexpected error (exits 1)

**Required fields:** `agent`, `schema_version`, `total_instances`, `resolved`,
`unresolved`, `errors`, `instances`

---

### `test_status_agent_failures.py`

Validates that `status.json` includes agent-failed instances.

**Purpose:** CI test **T4-27** — ensures the status report captures all failure
modes, not just harness-resolved instances.

**Usage:**
```bash
REPO_ROOT=/path/to/swe-bench python scripts/test_status_agent_failures.py
```

**Environment Variables:**

| Variable | Required | Description |
|---|---|---|
| `REPO_ROOT` | No | Repository root (defaults to `.`) |

**Output:**
- `OK:<counts>` — all three failure types present (exits 0)
- `MISSING:<counts>` — one or more failure types absent (exits 1)
- `ERROR:<message>` — unexpected error (exits 1)

**Required failure types:** `no_patch`, `timed_out`, `container_error`

---

## Test Coverage

These scripts are validated by the T4 test suite in `tests/t4_eval_and_integration.log`:

| Test | Script | What it checks |
|---|---|---|
| T4-23 | — | `write_status.py` exists on disk |
| T4-24 | — | `do_eval()` calls `write_status.py` |
| T4-25 | — | `status.json` includes non-harness instances |
| T4-26 | `test_status_schema.py` | Schema has all required fields |
| T4-27 | `test_status_agent_failures.py` | All failure types are captured |

## Design Notes

- These scripts are **intentionally standalone** — they don't import from the
  orchestrator package. This makes them easy to run independently and keeps
  the orchestrator dependency-free for CI environments.
- The `status.json` output is a runtime artifact (gitignored via `workspace/`)
  but these scripts themselves are source code that should be committed.
- Schema version is tracked in `status.json` (`schema_version: 2`) to allow
  future backward-compatible changes.
