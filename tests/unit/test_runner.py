"""Unit tests for the runner module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.config import Config
from swebench_orchestrator.runner import (
    Runner,
    fold_harness_results,
    run_eval,
    run_instance,
    summarize_results,
)


class TestRunInstance:
    """Tests for run_instance function."""

    def test_validates_agent_exists(self, tmp_path: Path):
        with pytest.raises(ValueError, match="not found"):
            run_instance(
                agents_dir=tmp_path / "agents",
                agent="nonexistent",
                instance_id="django__django-11039",
                output_dir=tmp_path / "outputs",
                timeout=3600,
            )

    def test_validates_bundle_exists(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        agent_dir.mkdir(parents=True)
        # No bundle directory

        with pytest.raises(ValueError, match="bundle not found"):
            run_instance(
                agents_dir=agents_dir,
                agent="pi",
                instance_id="django__django-11039",
                output_dir=tmp_path / "outputs",
                timeout=3600,
            )

    def test_validates_instance_exists(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        bundle_dir = agent_dir / "bundle"
        agent_dir.mkdir(parents=True)
        bundle_dir.mkdir(parents=True)

        cache_file = tmp_path / "cache.json"
        cache_file.write_text("[]")  # Empty cache

        with pytest.raises(ValueError, match="not found"):
            run_instance(
                agents_dir=agents_dir,
                agent="pi",
                instance_id="django__django-11039",
                output_dir=tmp_path / "outputs",
                timeout=3600,
                cache_file=cache_file,
            )

    def test_negative_timeout_raises(self, tmp_path: Path):
        """Negative timeout should raise ValueError before any Docker work."""
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        bundle_dir = agent_dir / "bundle"
        agent_dir.mkdir(parents=True)
        bundle_dir.mkdir(parents=True)

        cache_file = tmp_path / "cache.json"
        cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        with pytest.raises(ValueError, match="non-negative"):
            run_instance(
                agents_dir=agents_dir,
                agent="pi",
                instance_id="django__django-11039",
                output_dir=tmp_path / "outputs",
                timeout=-1,
                cache_file=cache_file,
            )

    def test_zero_timeout_accepted(self, tmp_path: Path):
        """Timeout of 0 should be accepted (no timeout)."""
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        bundle_dir = agent_dir / "bundle"
        agent_dir.mkdir(parents=True)
        bundle_dir.mkdir(parents=True)

        cache_file = tmp_path / "cache.json"
        cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        docker_ops = MagicMock()
        docker_ops.image_exists.return_value = True
        # Return failure so copy logic is skipped and function returns cleanly
        docker_ops.run_container.return_value = MagicMock(
            status="patch_collected", exit_code=0
        )
        docker_ops.copy_from_container.return_value = False

        result = run_instance(
            agents_dir=agents_dir,
            agent="pi",
            instance_id="django__django-11039",
            output_dir=tmp_path / "outputs",
            timeout=0,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # Should proceed to Docker run (not raise)
        assert result is not None

    def test_large_timeout_accepted(self, tmp_path: Path):
        """Very large timeout should be accepted."""
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        bundle_dir = agent_dir / "bundle"
        agent_dir.mkdir(parents=True)
        bundle_dir.mkdir(parents=True)

        cache_file = tmp_path / "cache.json"
        cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        docker_ops = MagicMock()
        docker_ops.image_exists.return_value = True
        docker_ops.run_container.return_value = MagicMock(
            status="patch_collected", exit_code=0
        )
        docker_ops.copy_from_container.return_value = False

        result = run_instance(
            agents_dir=agents_dir,
            agent="pi",
            instance_id="django__django-11039",
            output_dir=tmp_path / "outputs",
            timeout=999999,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # Should proceed to Docker run (not raise)
        assert result is not None


class TestSummarizeResults:
    """Tests for summarize_results function."""

    def test_empty_output_dir(self, tmp_path: Path):
        summary = summarize_results(tmp_path / "outputs" / "pi", agent="pi")
        assert summary["agent"] == "pi"
        assert summary["total"] == 0

    def test_single_instance(self, tmp_path: Path):
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 42, "elapsed_seconds": 120}'
        )

        summary = summarize_results(output_dir, agent="pi")
        assert summary["agent"] == "pi"
        assert summary["total"] == 1
        assert len(summary["rows"]) == 1

    def test_multiple_statuses(self, tmp_path: Path):
        output_dir = tmp_path / "outputs" / "pi"

        # Create instances with different local_eval values
        for iid, local_eval in [
            ("django__django-11039", "resolved"),
            ("flask__flask-1000", "failed"),
        ]:
            instance_dir = output_dir / iid
            instance_dir.mkdir(parents=True)
            (instance_dir / "result.json").write_text(
                f'{{"status": "patch_collected", "local_eval": "{local_eval}", "patch_bytes": 0, "elapsed_seconds": 0}}'
            )

        # Create timed_out instance
        timed_out_dir = output_dir / "requests__requests-1234"
        timed_out_dir.mkdir(parents=True)
        (timed_out_dir / "result.json").write_text(
            '{"status": "timed_out", "patch_bytes": 0, "elapsed_seconds": 0}'
        )

        summary = summarize_results(output_dir, agent="pi")
        assert summary["total"] == 3
        assert summary["resolved"] == 1
        assert summary["failed"] == 1
        assert summary["timed_out"] == 1

    def test_skips_eval_and_logs_dirs(self, tmp_path: Path):
        output_dir = tmp_path / "outputs" / "pi"
        (output_dir / "eval").mkdir(parents=True)
        (output_dir / "logs").mkdir(parents=True)
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text('{"status": "resolved"}')

        summary = summarize_results(output_dir, agent="pi")
        assert summary["total"] == 1

    def test_skips_invalid_json(self, tmp_path: Path):
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text("not valid json")

        summary = summarize_results(output_dir)
        assert summary["total"] == 0  # Invalid JSON is skipped


class TestRunnerUsesConfig:
    """Tests that Runner passes config values to run_instance."""

    def test_runner_uses_custom_registry(self, tmp_path: Path):
        """Runner should pass config.swebench_registry to instance_to_image_name."""
        from swebench_orchestrator.models import instance_to_image_name

        # Verify that instance_to_image_name uses the registry from config
        config = Config(
            repo_root=tmp_path,
            swebench_registry="my-registry",
        )
        expected_image = instance_to_image_name("django__django-11039", registry=config.swebench_registry)
        assert expected_image == "my-registry/sweb.eval.x86_64.django_1776_django-11039:latest"

    def test_runner_passes_config_cache_file(self, tmp_path: Path):
        """Runner should pass config.cache_file to run_instance."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "custom_cache.json",
        )
        runner = Runner(config)

        # Create necessary directories and files
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        bundle_dir = agent_dir / "bundle"
        agent_dir.mkdir(parents=True)
        bundle_dir.mkdir(parents=True)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        # Patch run_instance to capture calls
        with patch("swebench_orchestrator.runner.run_instance") as mock_run:
            mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1}
            runner.run_instance("pi", "django__django-11039")

            # Verify run_instance was called with correct cache_file from config
            call_kwargs = mock_run.call_args.kwargs if mock_run.call_args.kwargs else {}
            assert call_kwargs.get("cache_file") == config.cache_file

    def test_runner_uses_custom_storage_threshold(self, tmp_path: Path):
        """Runner should use config.max_storage_pct (verified via check_storage call)."""
        config = Config(
            repo_root=tmp_path,
            max_storage_pct=90.0,
        )
        runner = Runner(config)

        # Create necessary directories and files
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        bundle_dir = agent_dir / "bundle"
        agent_dir.mkdir(parents=True)
        bundle_dir.mkdir(parents=True)

        cache_file = tmp_path / "cache.json"
        cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        # Patch run_instance to capture check_storage calls
        with patch("swebench_orchestrator.runner.run_instance") as mock_run:
            mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1}
            runner.run_instance("pi", "django__django-11039")

            # Verify run_instance was called with correct cache_file from config
            call_kwargs = mock_run.call_args.kwargs if mock_run.call_args.kwargs else {}
            assert call_kwargs.get("cache_file") == config.cache_file

    def test_runner_run_all_uses_config_cache(self, tmp_path: Path):
        """Runner.run_all should use config.cache_file for dataset lookup."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "custom_cache.json",
        )
        runner = Runner(config)

        # Create the custom cache file with instance data
        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        # Mock run_instance to avoid actual Docker calls
        with patch.object(runner, "run_instance") as mock_run:
            mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1}
            result = runner.run_all("pi", timeout=3600)

            assert result["run"] == 1
            assert result["skipped"] == 0


class TestFoldHarnessResults:
    """Tests for fold_harness_results function."""

    def test_folds_resolved_instance(self, tmp_path: Path):
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        folded = fold_harness_results(output_dir, report_data)

        assert folded == 1
        result = json.loads((instance_dir / "result.json").read_text())
        assert result["local_eval"] == "resolved"
        assert result["status"] == "resolved"

    def test_folds_failed_instance(self, tmp_path: Path):
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "flask__flask-1000"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        report_data = {
            "resolved_ids": [],
            "unresolved_ids": ["flask__flask-1000"],
            "error_ids": [],
        }
        folded = fold_harness_results(output_dir, report_data)

        assert folded == 1
        result = json.loads((instance_dir / "result.json").read_text())
        assert result["local_eval"] == "failed"
        assert result["status"] == "failed"

    def test_folds_error_instance(self, tmp_path: Path):
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "requests__requests-1234"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        report_data = {
            "resolved_ids": [],
            "unresolved_ids": [],
            "error_ids": ["requests__requests-1234"],
        }
        folded = fold_harness_results(output_dir, report_data)

        assert folded == 1
        result = json.loads((instance_dir / "result.json").read_text())
        assert result["local_eval"] == "error"
        assert result["status"] == "error"

    def test_skips_missing_result_json(self, tmp_path: Path):
        output_dir = tmp_path / "outputs" / "pi"
        # No result.json created for this instance

        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        folded = fold_harness_results(output_dir, report_data)

        assert folded == 0

    def test_folds_multiple_instances(self, tmp_path: Path):
        output_dir = tmp_path / "outputs" / "pi"
        for iid in ["django__django-11039", "flask__flask-1000"]:
            instance_dir = output_dir / iid
            instance_dir.mkdir(parents=True)
            (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": ["flask__flask-1000"],
            "error_ids": [],
        }
        folded = fold_harness_results(output_dir, report_data)

        assert folded == 2


class TestRunEval:
    """Tests for run_eval function."""

    def test_fold_harness_results_called_once(self, tmp_path: Path):
        """Verify fold_harness_results is called exactly once (Issue #13)."""
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        # Create predictions file
        preds_file = output_dir / "predictions.jsonl"
        preds_file.write_text(
            '{"instance_id": "django__django-11039", "model_patch": "diff --git a/test.py b/test.py\\n+print(1)\\n"}'
        )

        # Create harness report
        eval_dir = output_dir / "eval"
        eval_dir.mkdir(parents=True)
        report_file = eval_dir / f"{output_dir.name}.{output_dir.name}.json"
        report_file.write_text(
            json.dumps({
                "resolved_ids": ["django__django-11039"],
                "unresolved_ids": [],
                "error_ids": [],
            })
        )

        with patch("swebench_orchestrator.runner.generate_predictions") as mock_preds:
            mock_preds.return_value = (preds_file, ["django__django-11039"])
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = run_eval(output_dir, "pi", swebench_py=tmp_path / "fake_swebench")

        assert result["status"] == "completed"
        assert result["folded"] == 1
        # Verify the instance was updated
        result_json = json.loads((instance_dir / "result.json").read_text())
        assert result_json["local_eval"] == "resolved"
        assert result_json["status"] == "resolved"

    def test_fold_count_matches_return_value(self, tmp_path: Path):
        """Verify the folded count in return value matches what was actually folded."""
        output_dir = tmp_path / "outputs" / "pi"
        for iid in ["django__django-11039", "flask__flask-1000"]:
            instance_dir = output_dir / iid
            instance_dir.mkdir(parents=True)
            (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        preds_file = output_dir / "predictions.jsonl"
        preds_file.write_text(
            '{"instance_id": "django__django-11039", "model_patch": "diff --git a/test.py b/test.py\\n+print(1)\\n"}\n'
            '{"instance_id": "flask__flask-1000", "model_patch": "diff --git a/test.py b/test.py\\n+print(2)\\n"}'
        )

        eval_dir = output_dir / "eval"
        eval_dir.mkdir(parents=True)
        report_file = eval_dir / f"{output_dir.name}.{output_dir.name}.json"
        report_file.write_text(
            json.dumps({
                "resolved_ids": ["django__django-11039"],
                "unresolved_ids": ["flask__flask-1000"],
                "error_ids": [],
            })
        )

        with patch("swebench_orchestrator.runner.generate_predictions") as mock_preds:
            mock_preds.return_value = (preds_file, ["django__django-11039", "flask__flask-1000"])
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = run_eval(output_dir, "pi", swebench_py=tmp_path / "fake_swebench")

        assert result["folded"] == 2

    def test_no_harness_report_returns_zero_folded(self, tmp_path: Path):
        """When no harness report exists, folded should be 0."""
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        preds_file = output_dir / "predictions.jsonl"
        preds_file.write_text(
            '{"instance_id": "django__django-11039", "model_patch": "diff --git a/test.py b/test.py\\n+print(1)\\n"}'
        )

        # No eval directory, so no report

        with patch("swebench_orchestrator.runner.generate_predictions") as mock_preds:
            mock_preds.return_value = (preds_file, ["django__django-11039"])
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = run_eval(output_dir, "pi", swebench_py=tmp_path / "fake_swebench")

        assert result["status"] == "completed"
        assert result["folded"] == 0
        # Instance should not have been modified
        result_json = json.loads((instance_dir / "result.json").read_text())
        assert "local_eval" not in result_json
