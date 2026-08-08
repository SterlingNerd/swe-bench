"""Unit tests for scripts/status utilities.

Tests for write_status.py, test_status_schema.py, and
test_status_agent_failures.py — the standalone evaluation
status generation and validation scripts.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
WRITE_STATUS_SCRIPT = SCRIPTS_DIR / "write_status.py"
TEST_SCHEMA_SCRIPT = SCRIPTS_DIR / "test_status_schema.py"
TEST_FAILURES_SCRIPT = SCRIPTS_DIR / "test_status_agent_failures.py"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def eval_dir(tmp_path):
    """Create a minimal eval directory structure mimicking swebench output.

    Structure:
        tmp_path/                          <- REPO_ROOT
        └── workspace/
            └── outputs/
                └── pi/                    <- EVAL_DIR (returned)
                    ├── eval/pi.pi.json   (report)
                    ├── status.json       (generated here by write_status.py)
                    ├── django__django-7530/result.json
                    └── ...
    """
    output_dir = tmp_path / "workspace" / "outputs" / "pi"
    eval_subdir = output_dir / "eval"
    eval_subdir.mkdir(parents=True)

    # Create a report file (what write_status.py reads from $EVAL_DIR/eval/)
    report = {
        "resolved_ids": ["django__django-7530", "astropy__astropy-7166"],
        "unresolved_ids": ["django__django-10097"],
        "error_ids": ["django__django-11066"],
    }
    (eval_subdir / "pi.pi.json").write_text(json.dumps(report))

    # Create instance directories with result.json files
    instances = {
        "django__django-7530": {"status": "resolved", "local_eval": "resolved", "elapsed_seconds": 120, "patch_bytes": 42},
        "astropy__astropy-7166": {"status": "resolved", "local_eval": "resolved", "elapsed_seconds": 95, "patch_bytes": 30},
        "django__django-10097": {"status": "failed", "local_eval": "failed", "elapsed_seconds": 200, "patch_bytes": 15},
        "django__django-11066": {"status": "container_error", "local_eval": None, "elapsed_seconds": 30, "patch_bytes": 0},
        "django__django-11141": {"status": "no_patch", "local_eval": None, "elapsed_seconds": 60, "patch_bytes": 0},
        "astropy__astropy-8707": {"status": "timed_out", "local_eval": None, "elapsed_seconds": 3600, "patch_bytes": 0},
    }
    for iid, data in instances.items():
        inst_dir = output_dir / iid
        inst_dir.mkdir()
        (inst_dir / "result.json").write_text(json.dumps(data))

    return output_dir  # This is EVAL_DIR


def _make_env(eval_dir):
    """Create environment dict for running scripts."""
    repo_root = eval_dir.parent.parent.parent  # tmp_path
    return {
        **os.environ,
        "EVAL_DIR": str(eval_dir),
        "AGENT_NAME": "pi",
        "PREDS": str(eval_dir.parent / "predictions.jsonl"),
        "REPO_ROOT": str(repo_root),
    }


def _run_write_status(eval_dir):
    """Helper to run write_status.py and return the process result."""
    env = _make_env(eval_dir)
    return subprocess.run(
        [sys.executable, str(WRITE_STATUS_SCRIPT)],
        env=env,
        cwd=str(eval_dir.parent.parent.parent),  # REPO_ROOT
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# write_status.py tests
# ---------------------------------------------------------------------------

class TestWriteStatus:
    """Tests for write_status.py."""

    def test_script_exists(self):
        """T4-23: write_status.py script exists on disk."""
        assert WRITE_STATUS_SCRIPT.exists(), "write_status.py should exist in scripts/"

    def test_generates_status_json(self, eval_dir):
        """T4-24: write_status.py generates status.json."""
        result = _run_write_status(eval_dir)
        assert result.returncode == 0, f"write_status.py failed: {result.stderr}"
        status_path = eval_dir / "status.json"
        assert status_path.exists(), "status.json should be created"

    def test_status_json_schema(self, eval_dir):
        """T4-26: Generated status.json has valid schema."""
        _run_write_status(eval_dir)
        status = json.loads((eval_dir / "status.json").read_text())

        required_keys = ["agent", "schema_version", "total_instances",
                         "resolved", "unresolved", "errors", "instances"]
        for key in required_keys:
            assert key in status, f"Missing required key: {key}"

    def test_status_json_schema_version(self, eval_dir):
        """Schema version should be 2."""
        _run_write_status(eval_dir)
        status = json.loads((eval_dir / "status.json").read_text())
        assert status["schema_version"] == 2

    def test_status_json_counts(self, eval_dir):
        """Resolved + unresolved + errors should match total."""
        _run_write_status(eval_dir)
        status = json.loads((eval_dir / "status.json").read_text())

        # total_instances counts all instance directories
        assert status["total_instances"] == 6

        # Counts from report
        assert status["resolved"] == 2
        assert status["unresolved"] == 1
        assert status["errors"] == 1

    def test_status_json_instance_details(self, eval_dir):
        """Each instance should have status, local_eval, elapsed_seconds, patch_bytes."""
        _run_write_status(eval_dir)
        status = json.loads((eval_dir / "status.json").read_text())

        for iid in ["django__django-7530", "astropy__astropy-7166"]:
            assert iid in status["instances"], f"Missing instance: {iid}"
            inst = status["instances"][iid]
            assert "status" in inst
            assert "local_eval" in inst
            assert "elapsed_seconds" in inst
            assert "patch_bytes" in inst

    def test_status_json_includes_non_harness_instances(self, eval_dir):
        """T4-25: status.json includes non-harness instances (no_patch, timed_out)."""
        _run_write_status(eval_dir)
        status = json.loads((eval_dir / "status.json").read_text())

        # no_patch and timed_out instances should be in the output
        assert "django__django-11141" in status["instances"]  # no_patch
        assert "astropy__astropy-8707" in status["instances"]  # timed_out

    def test_status_json_id_lists_sorted(self, eval_dir):
        """ID lists should be sorted."""
        _run_write_status(eval_dir)
        status = json.loads((eval_dir / "status.json").read_text())

        assert status["resolved_ids"] == sorted(status["resolved_ids"])
        assert status["unresolved_ids"] == sorted(status["unresolved_ids"])
        assert status["error_ids"] == sorted(status["error_ids"])

    def test_status_json_agent_name(self, eval_dir):
        """Agent name should match AGENT_NAME env var."""
        _run_write_status(eval_dir)
        status = json.loads((eval_dir / "status.json").read_text())
        assert status["agent"] == "pi"

    def test_status_json_custom_output_path(self, eval_dir):
        """OUT_JSON env var should override default output path."""
        custom_out = eval_dir / "custom_status.json"
        env = _make_env(eval_dir)
        env["OUT_JSON"] = str(custom_out)

        subprocess.run(
            [sys.executable, str(WRITE_STATUS_SCRIPT)],
            env=env,
            cwd=str(eval_dir.parent.parent.parent),
            capture_output=True,
            text=True,
        )
        assert custom_out.exists(), "Custom output path should be used"

    def test_status_json_no_report_skips_gracefully(self, tmp_path):
        """When no report file exists, script should exit 0 with a warning."""
        # Create minimal structure without a report file
        output_dir = tmp_path / "workspace" / "outputs" / "pi"
        eval_subdir = output_dir / "eval"
        eval_subdir.mkdir(parents=True)

        env = {
            **os.environ,
            "EVAL_DIR": str(output_dir),
            "AGENT_NAME": "pi",
            "PREDS": str(output_dir.parent / "predictions.jsonl"),
        }

        result = subprocess.run(
            [sys.executable, str(WRITE_STATUS_SCRIPT)],
            env=env,
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "WARNING" in result.stdout or "skipping" in result.stdout.lower()

    def test_status_json_fallback_to_latest_report(self, eval_dir):
        """When expected report doesn't exist, should fall back to latest .json."""
        env = _make_env(eval_dir)

        # Remove the expected report
        (eval_dir / "eval" / "pi.pi.json").unlink()

        # Create an alternative report
        (eval_dir / "eval" / "other.other.json").write_text(json.dumps({
            "resolved_ids": ["django__django-7530"],
            "unresolved_ids": [],
            "error_ids": [],
        }))

        result = subprocess.run(
            [sys.executable, str(WRITE_STATUS_SCRIPT)],
            env=env,
            cwd=str(eval_dir.parent.parent.parent),
            capture_output=True,
            text=True,
        )
        # Should succeed by falling back to the alternative report
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# test_status_schema.py tests
# ---------------------------------------------------------------------------

class TestStatusSchema:
    """Tests for test_status_schema.py (T4-26)."""

    def test_script_exists(self):
        """test_status_schema.py script exists on disk."""
        assert TEST_SCHEMA_SCRIPT.exists()

    def test_valid_status_passes(self, eval_dir):
        """Valid status.json should pass validation."""
        _run_write_status(eval_dir)

        env = _make_env(eval_dir)
        result = subprocess.run(
            [sys.executable, str(TEST_SCHEMA_SCRIPT)],
            env=env,
            cwd=str(eval_dir.parent.parent.parent),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.startswith("OK:")

    def test_missing_fields_fails(self, eval_dir):
        """status.json with missing fields should fail validation."""
        _run_write_status(eval_dir)

        # Remove required fields
        status_path = eval_dir / "status.json"
        status = json.loads(status_path.read_text())
        del status["resolved"]
        del status["errors"]
        status_path.write_text(json.dumps(status))

        env = _make_env(eval_dir)
        result = subprocess.run(
            [sys.executable, str(TEST_SCHEMA_SCRIPT)],
            env=env,
            cwd=str(eval_dir.parent.parent.parent),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert result.stdout.startswith("MISSING:")


# ---------------------------------------------------------------------------
# test_status_agent_failures.py tests
# ---------------------------------------------------------------------------

class TestStatusAgentFailures:
    """Tests for test_status_agent_failures.py (T4-27)."""

    def test_script_exists(self):
        """test_status_agent_failures.py script exists on disk."""
        assert TEST_FAILURES_SCRIPT.exists()

    def test_all_failure_types_pass(self, eval_dir):
        """status.json with all failure types should pass."""
        _run_write_status(eval_dir)

        env = _make_env(eval_dir)
        result = subprocess.run(
            [sys.executable, str(TEST_FAILURES_SCRIPT)],
            env=env,
            cwd=str(eval_dir.parent.parent.parent),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.startswith("OK:")

    def test_missing_failure_type_fails(self, eval_dir):
        """status.json missing a failure type should fail."""
        _run_write_status(eval_dir)

        # Remove no_patch instance from status.json
        status_path = eval_dir / "status.json"
        status = json.loads(status_path.read_text())
        del status["instances"]["django__django-11141"]  # no_patch
        status["total_instances"] -= 1
        status_path.write_text(json.dumps(status))

        env = _make_env(eval_dir)
        result = subprocess.run(
            [sys.executable, str(TEST_FAILURES_SCRIPT)],
            env=env,
            cwd=str(eval_dir.parent.parent.parent),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert result.stdout.startswith("MISSING:")

    def test_no_failures_fails(self, tmp_path):
        """status.json with no failure types should fail."""
        output_dir = tmp_path / "workspace" / "outputs" / "pi"
        eval_subdir = output_dir / "eval"
        eval_subdir.mkdir(parents=True)

        # Create a report with only resolved instances
        (eval_subdir / "pi.pi.json").write_text(json.dumps({
            "resolved_ids": ["django__django-7530"],
            "unresolved_ids": [],
            "error_ids": [],
        }))

        # Create only resolved instance
        inst_dir = output_dir / "django__django-7530"
        inst_dir.mkdir()
        (inst_dir / "result.json").write_text(json.dumps({
            "status": "resolved",
            "local_eval": "resolved",
            "elapsed_seconds": 120,
            "patch_bytes": 42,
        }))

        env = {
            **os.environ,
            "EVAL_DIR": str(output_dir),
            "AGENT_NAME": "pi",
            "PREDS": str(output_dir.parent / "predictions.jsonl"),
            "REPO_ROOT": str(tmp_path),
        }

        subprocess.run(
            [sys.executable, str(WRITE_STATUS_SCRIPT)],
            env=env,
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )

        result = subprocess.run(
            [sys.executable, str(TEST_FAILURES_SCRIPT)],
            env=env,
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert result.stdout.startswith("MISSING:")
