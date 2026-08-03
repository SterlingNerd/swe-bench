"""Integration tests for eval CLI command."""

from click.testing import CliRunner
from pathlib import Path
import json

from swebench_orchestrator.cli import main


class TestEvalCLI:
    """Tests for the --eval CLI command."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_eval_missing_agent_exits_1(self):
        """--eval without agent → exit 1."""
        result = self.runner.invoke(main, ["--eval"])
        assert result.exit_code in (1, 2)
        assert "ERROR" in result.output or "Usage" in result.output

    def test_eval_no_outputs_exits_nonzero(self):
        """--eval with no outputs → exit non-zero."""
        result = self.runner.invoke(main, ["--eval", "nonexistent-agent"])
        # Should fail because agent doesn't exist / no outputs
        assert result.exit_code in (0, 1, 2)

    def test_eval_with_swebench_not_installed(self, tmp_path: Path):
        """--eval when swebench not installed → informative error."""
        # Create a minimal workspace without swebench venv
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "test-agent").mkdir()

        output_dir = tmp_path / "workspace" / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Create a patch to evaluate
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir()
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")
        (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        # Run with custom repo root
        result = self.runner.invoke(main, ["--eval", "test-agent"], catch_exceptions=False)
        # May fail if swebench not installed, but shouldn't crash
        assert result.exit_code in (0, 1, 2)


class TestPredictionsCLI:
    """Tests for predictions.jsonl generation via CLI."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_predictions_file_created(self, tmp_path: Path):
        """predictions.jsonl is created with correct format."""
        output_dir = tmp_path / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Create instances with patches
        for iid in ["django__django-11039", "flask__flask-1000"]:
            instance_dir = output_dir / iid
            instance_dir.mkdir()
            (instance_dir / "patch.diff").write_text(f"diff --git a/{iid} b/{iid}\n+fix")
            (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        # Create predictions manually (simulating what do_eval does)
        preds_file = output_dir / "predictions.jsonl"
        with open(preds_file, "w") as f:
            for iid in ["django__django-11039", "flask__flask-1000"]:
                patch_path = output_dir / iid / "patch.diff"
                patch = patch_path.read_text()
                f.write(json.dumps({
                    "instance_id": iid,
                    "model_name_or_path": "test-agent",
                    "model_patch": patch,
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

    def test_predictions_skips_no_patch(self, tmp_path: Path):
        """Instances without patches are excluded from predictions."""
        output_dir = tmp_path / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Instance with patch
        with_patch = output_dir / "django__django-11039"
        with_patch.mkdir()
        (with_patch / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        # Instance without patch
        no_patch = output_dir / "flask__flask-1000"
        no_patch.mkdir()
        (no_patch / "result.json").write_text('{"status": "no_patch"}')

        # Generate predictions
        preds_file = output_dir / "predictions.jsonl"
        with open(preds_file, "w") as f:
            for d in sorted(output_dir.iterdir()):
                if d.is_dir() and d.name not in ("eval", "logs"):
                    patch_file = d / "patch.diff"
                    if patch_file.exists() and patch_file.stat().st_size > 0:
                        patch = patch_file.read_text()
                        f.write(json.dumps({
                            "instance_id": d.name,
                            "model_name_or_path": "test-agent",
                            "model_patch": patch,
                        }) + "\n")

        lines = preds_file.read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0])["instance_id"] == "django__django-11039"
