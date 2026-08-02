"""Tests for eval command result folding — verifies harness results are written back to result.json."""

from click.testing import CliRunner
from pathlib import Path
import json
import os
from unittest.mock import patch, MagicMock


class TestEvalResultFolding:
    """Verify that --eval folds swebench harness results into per-instance result.json files."""

    def setup_method(self):
        self.runner = CliRunner()
        self._orig_env = os.environ.copy()

    def _setup_eval_env(self, tmp_path: Path):
        """Create a minimal eval environment with patches and a fake harness report.

        The workspace dir is set to tmp_path so output_dir = tmp_path/outputs.
        """
        # Create output structure: tmp_path/outputs/test-agent/{instance}/
        output_dir = tmp_path / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Create instances with patches
        instances = {
            "django__django-11039": {"status": "patch_collected"},
            "flask__flask-2000": {"status": "patch_collected"},
            "requests__requests-3000": {"status": "patch_collected"},
        }

        for iid, meta in instances.items():
            instance_dir = output_dir / iid
            instance_dir.mkdir()
            (instance_dir / "patch.diff").write_text(f"diff --git a/{iid} b/{iid}\n+fix")
            (instance_dir / "result.json").write_text(json.dumps(meta))

        # Create eval directory with a fake harness report
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        report = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": ["flask__flask-2000"],
            "error_ids": ["requests__requests-3000"],
        }
        (eval_dir / f"test-agent.test-agent.json").write_text(json.dumps(report))

        return output_dir, instances

    def test_eval_folds_resolved_status(self, tmp_path: Path):
        """After --eval, resolved instances get local_eval='resolved' and status='resolved'."""
        output_dir, instances = self._setup_eval_env(tmp_path)

        # Set workspace so config.output_dir = tmp_path/outputs
        os.environ["SWE_WORKSPACE_DIR"] = str(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        try:
            with patch("swebench_orchestrator.cli.subprocess.run", return_value=mock_result):
                result = self.runner.invoke(
                    main,
                    ["eval", "test-agent"],
                    catch_exceptions=False,
                )

            # Verify harness was called and results were folded
            assert result.exit_code == 0
            assert "Folded" in result.output

            # Check each instance's result.json was updated
            resolved = output_dir / "django__django-11039" / "result.json"
            flask = output_dir / "flask__flask-2000" / "result.json"
            requests = output_dir / "requests__requests-3000" / "result.json"

            resolved_data = json.loads(resolved.read_text())
            assert resolved_data["local_eval"] == "resolved"
            assert resolved_data["status"] == "resolved"

            flask_data = json.loads(flask.read_text())
            assert flask_data["local_eval"] == "failed"
            assert flask_data["status"] == "failed"

            requests_data = json.loads(requests.read_text())
            assert requests_data["local_eval"] == "error"
            assert requests_data["status"] == "error"
        finally:
            os.environ.pop("SWE_WORKSPACE_DIR", None)

    def test_eval_folds_when_no_report_found(self, tmp_path: Path):
        """When harness report is missing, --eval warns but doesn't crash."""
        output_dir = tmp_path / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir()
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")
        (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        # Create eval dir but NO report file
        (output_dir / "eval").mkdir()

        os.environ["SWE_WORKSPACE_DIR"] = str(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        try:
            with patch("swebench_orchestrator.cli.subprocess.run", return_value=mock_result):
                result = self.runner.invoke(
                    main,
                    ["eval", "test-agent"],
                    catch_exceptions=False,
                )

            assert result.exit_code == 0
            assert "WARNING" in result.output
            assert "No harness report found" in result.output

            # result.json should be unchanged (no local_eval added)
            data = json.loads((instance_dir / "result.json").read_text())
            assert "local_eval" not in data
        finally:
            os.environ.pop("SWE_WORKSPACE_DIR", None)

    def test_summarize_shows_folded_results(self, tmp_path: Path):
        """After eval+fold, --summarize shows correct resolved/failed counts."""
        output_dir, instances = self._setup_eval_env(tmp_path)

        # Simulate what happens after --eval folds results
        for iid in ["django__django-11039"]:
            rf = output_dir / iid / "result.json"
            data = json.loads(rf.read_text())
            data["local_eval"] = "resolved"
            data["status"] = "resolved"
            rf.write_text(json.dumps(data))

        for iid in ["flask__flask-2000"]:
            rf = output_dir / iid / "result.json"
            data = json.loads(rf.read_text())
            data["local_eval"] = "failed"
            data["status"] = "failed"
            rf.write_text(json.dumps(data))

        for iid in ["requests__requests-3000"]:
            rf = output_dir / iid / "result.json"
            data = json.loads(rf.read_text())
            data["local_eval"] = "error"
            data["status"] = "error"
            rf.write_text(json.dumps(data))

        os.environ["SWE_WORKSPACE_DIR"] = str(tmp_path)
        try:
            result = self.runner.invoke(main, ["summarize", "test-agent"])
            assert result.exit_code == 0
            assert "resolved: 1" in result.output
            assert "failed: 1" in result.output
            assert "error: 1" in result.output
        finally:
            os.environ.pop("SWE_WORKSPACE_DIR", None)


# Import main at module level for the tests
from swebench_orchestrator.cli import main
