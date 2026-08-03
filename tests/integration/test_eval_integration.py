"""Integration tests for eval operations.

Tests harness result folding, predictions.jsonl generation, and per-instance eval.
Mirrors T4_eval_and_integration.sh from the bash test suite.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from swebench_orchestrator.runner import summarize_results


class TestPredictionsGeneration:
    """Tests for predictions.jsonl generation."""

    def test_generates_predictions_from_patches(self, test_workspace):
        """Generate predictions.jsonl from instance patches."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Create instances with patches
        for iid in ["django__django-11039", "flask__flask-1000"]:
            instance_dir = output_dir / iid
            instance_dir.mkdir()
            (instance_dir / "patch.diff").write_text(f"diff --git a/{iid} b/{iid}\n+fix")
            (instance_dir / "result.json").write_text(
                '{"status": "patch_collected", "patch_bytes": 42}'
            )

        # Create an instance without a patch (should be skipped)
        no_patch_dir = output_dir / "requests__requests-1234"
        no_patch_dir.mkdir()
        (no_patch_dir / "result.json").write_text('{"status": "no_patch"}')

        # Generate predictions
        preds_file = output_dir / "predictions.jsonl"
        agent = "test-agent"

        # Collect instances with patches (mimicking do_eval logic)
        instance_ids = []
        for d in sorted(output_dir.iterdir()):
            if d.is_dir() and d.name not in ("eval", "logs"):
                patch_file = d / "patch.diff"
                if patch_file.exists() and patch_file.stat().st_size > 0:
                    instance_ids.append(d.name)

        assert len(instance_ids) == 2
        assert "django__django-11039" in instance_ids
        assert "flask__flask-1000" in instance_ids
        assert "requests__requests-1234" not in instance_ids

        # Write predictions
        with open(preds_file, "w") as f:
            for iid in instance_ids:
                patch_path = output_dir / iid / "patch.diff"
                patch_content = patch_path.read_text()
                f.write(json.dumps({
                    "instance_id": iid,
                    "model_name_or_path": agent,
                    "model_patch": patch_content,
                }) + "\n")

        # Verify predictions file
        assert preds_file.exists()
        lines = preds_file.read_text().strip().split("\n")
        assert len(lines) == 2

        for line in lines:
            pred = json.loads(line)
            assert "instance_id" in pred
            assert "model_name_or_path" in pred
            assert "model_patch" in pred
            assert pred["model_name_or_path"] == "test-agent"


class TestHarnessResultFolding:
    """Tests for folding swebench harness results into result.json."""

    def test_folds_resolved_status(self, test_workspace):
        """Resolved instances get local_eval=resolved and status=resolved."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Create instance with initial result
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir()
        (instance_dir / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 42, "elapsed_seconds": 10}'
        )

        # Simulate harness report
        report = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        report_file = output_dir / "report.json"
        report_file.write_text(json.dumps(report))

        # Fold results (mimicking the bash inline Python)
        resolved = set(report.get("resolved_ids", []))
        errored = set(report.get("error_ids", []))

        result_file = instance_dir / "result.json"
        meta = json.loads(result_file.read_text())

        if "django__django-11039" in resolved:
            meta["local_eval"] = "resolved"
            meta["status"] = "resolved"
        elif "django__django-11039" in errored:
            meta["local_eval"] = "error"
            meta["status"] = "error"

        result_file.write_text(json.dumps(meta, indent=2))

        # Verify
        final = json.loads(result_file.read_text())
        assert final["local_eval"] == "resolved"
        assert final["status"] == "resolved"
        assert final["patch_bytes"] == 42  # Original data preserved

    def test_folds_failed_status(self, test_workspace):
        """Unresolved instances get local_eval=failed."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        instance_dir = output_dir / "flask__flask-1000"
        instance_dir.mkdir()
        (instance_dir / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 30}'
        )

        # Simulate harness report with this instance unresolved
        report = {
            "resolved_ids": [],
            "unresolved_ids": ["flask__flask-1000"],
            "error_ids": [],
        }

        result_file = instance_dir / "result.json"
        meta = json.loads(result_file.read_text())

        resolved = set(report.get("resolved_ids", []))
        errored = set(report.get("error_ids", []))
        unresolved = set(report.get("unresolved_ids", []))

        if "flask__flask-1000" in resolved:
            meta["local_eval"] = "resolved"
            meta["status"] = "resolved"
        elif "flask__flask-1000" in errored:
            meta["local_eval"] = "error"
            meta["status"] = "error"
        elif "flask__flask-1000" in unresolved:
            meta["local_eval"] = "failed"
            meta["status"] = "failed"

        result_file.write_text(json.dumps(meta, indent=2))

        final = json.loads(result_file.read_text())
        assert final["local_eval"] == "failed"
        assert final["status"] == "failed"

    def test_folds_error_status(self, test_workspace):
        """Error instances get local_eval=error."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        instance_dir = output_dir / "requests__requests-1234"
        instance_dir.mkdir()
        (instance_dir / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 50}'
        )

        report = {
            "resolved_ids": [],
            "unresolved_ids": [],
            "error_ids": ["requests__requests-1234"],
        }

        result_file = instance_dir / "result.json"
        meta = json.loads(result_file.read_text())

        resolved = set(report.get("resolved_ids", []))
        errored = set(report.get("error_ids", []))

        if "requests__requests-1234" in resolved:
            meta["local_eval"] = "resolved"
            meta["status"] = "resolved"
        elif "requests__requests-1234" in errored:
            meta["local_eval"] = "error"
            meta["status"] = "error"

        result_file.write_text(json.dumps(meta, indent=2))

        final = json.loads(result_file.read_text())
        assert final["local_eval"] == "error"
        assert final["status"] == "error"


class TestSummarizeWithEval:
    """Tests for summarizing results after eval folding."""

    def test_summarize_after_folding(self, test_workspace):
        """Summarize correctly counts resolved/failed/error after folding."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Create instances with different eval results
        for iid, local_eval in [
            ("django__django-11039", "resolved"),
            ("flask__flask-1000", "failed"),
            ("requests__requests-1234", "error"),
        ]:
            instance_dir = output_dir / iid
            instance_dir.mkdir()
            (instance_dir / "result.json").write_text(
                json.dumps({
                    "status": "patch_collected",
                    "local_eval": local_eval,
                    "patch_bytes": 42,
                    "elapsed_seconds": 10,
                })
            )

        summary = summarize_results(output_dir, agent="test-agent")
        assert summary["total"] == 3
        assert summary["resolved"] == 1
        assert summary["failed"] == 1
        assert summary["errored"] == 1

    def test_summarize_no_local_eval(self, test_workspace):
        """Summarize handles instances without local_eval."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir()
        (instance_dir / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 42}'
        )

        summary = summarize_results(output_dir, agent="test-agent")
        assert summary["total"] == 1
        assert summary["resolved"] == 0  # No local_eval means not resolved


class TestPerInstanceEval:
    """Tests for per-instance eval (evaluate immediately after agent run)."""

    def test_eval_folds_into_attempt_result(self, test_workspace):
        """Per-instance eval folds harness result into attempt's result.json."""
        runs_dir = test_workspace / "runs"
        runs_dir.mkdir(exist_ok=True)

        # Create a run with an attempt
        from swebench_orchestrator.manifest import RunManager
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="test-agent")
        attempt = manager.create_attempt(run.run_id, "django__django-11039")

        # Set initial result
        manager.update_attempt_result(
            run.run_id,
            attempt.attempt_id,
            status="patch_collected",
            patch_bytes=42,
        )

        # Simulate harness resolving this instance
        resolved_ids = ["django__django-11039"]

        # Find and update the attempt result
        result_data = manager.get_attempt_result(run.run_id, attempt.attempt_id)
        if result_data:
            if "django__django-11039" in resolved_ids:
                result_data["local_eval"] = "resolved"
                result_data["status"] = "resolved"

            # Write back
            attempt_dir = runs_dir / run.run_id / "tasks" / "django__django-11039" / attempt.attempt_id
            (attempt_dir / "result.json").write_text(json.dumps(result_data, indent=2))

        # Verify
        final = manager.get_attempt_result(run.run_id, attempt.attempt_id)
        assert final is not None
        assert final["local_eval"] == "resolved"
        assert final["status"] == "resolved"
        assert final["patch_bytes"] == 42
