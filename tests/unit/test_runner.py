"""Unit tests for the runner module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.config import Config
from swebench_orchestrator.runner import (
    Runner,
    generate_predictions,
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
                agents_dir=tmp_path / "harnesses",
                agent="nonexistent",
                instance_id="django__django-11039",
                output_dir=tmp_path / "outputs",
                timeout=3600,
            )

    def test_validates_bundle_exists(self, tmp_path: Path):
        agents_dir = tmp_path / "harnesses"
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
        agents_dir = tmp_path / "harnesses"
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
        agents_dir = tmp_path / "harnesses"
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
        agents_dir = tmp_path / "harnesses"
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
        agents_dir = tmp_path / "harnesses"
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
        assert summary["resolved"] == 0
        assert summary["failed"] == 0
        assert summary["failed_breakdown"] == {}
        assert summary["error"] == 0
        assert summary["error_breakdown"] == {}

    def test_final_status_uses_eval_when_present(self, tmp_path: Path):
        """If local_eval exists, it overrides work status."""
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        # Work status was patch_collected, but eval says failed
        (instance_dir / "result.json").write_text(
            '{"status": "patch_collected", "local_eval": "failed", "patch_bytes": 100, "elapsed_seconds": 120}'
        )

        summary = summarize_results(output_dir, agent="pi")
        assert summary["total"] == 1
        assert summary["resolved"] == 0
        assert summary["failed"] == 1  # Counted as failed (eval failed)
        assert summary["error"] == 0
        # The row should show final status as failed (from local_eval)
        assert summary["rows"][0]["status"] == "failed"

    def test_final_status_uses_work_when_no_eval(self, tmp_path: Path):
        """If no local_eval, use work status."""
        output_dir = tmp_path / "outputs" / "pi"
        for iid, status in [
            ("django__django-11039", "no_patch"),
            ("flask__flask-1000", "timed_out"),
            ("requests__requests-1234", "agent_error"),
        ]:
            instance_dir = output_dir / iid
            instance_dir.mkdir(parents=True)
            (instance_dir / "result.json").write_text(
                f'{{"status": "{status}", "patch_bytes": 0, "elapsed_seconds": 0}}'
            )

        summary = summarize_results(output_dir, agent="pi")
        assert summary["total"] == 3
        assert summary["resolved"] == 0
        assert summary["failed"] == 2  # no_patch + timed_out
        assert summary["error"] == 1   # agent_error
        assert summary["failed_breakdown"] == {"no_patch": 1, "timed_out": 1}
        assert summary["error_breakdown"] == {"agent_error": 1}

    def test_resolved_from_local_eval(self, tmp_path: Path):
        """Instances with local_eval=resolved are resolved."""
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text(
            '{"status": "patch_collected", "local_eval": "resolved", "patch_bytes": 100, "elapsed_seconds": 120}'
        )

        summary = summarize_results(output_dir, agent="pi")
        assert summary["total"] == 1
        assert summary["resolved"] == 1
        assert summary["failed"] == 0
        assert summary["error"] == 0
        assert summary["rows"][0]["status"] == "resolved"

    def test_failed_group_includes_eval_failed_no_patch_timed_out_pending(self, tmp_path: Path):
        """Failed group: eval failed, no_patch, timed_out, patch_collected (no eval yet)."""
        output_dir = tmp_path / "outputs" / "pi"
        for iid, status, local_eval in [
            ("django__django-11039", "patch_collected", "failed"),      # eval failed
            ("flask__flask-1000", "no_patch", None),                       # no patch
            ("requests__requests-1234", "timed_out", None),               # timed out
            ("pandas__pandas-5678", "patch_collected", None),             # pending eval
        ]:
            instance_dir = output_dir / iid
            instance_dir.mkdir(parents=True)
            data = {"status": status, "patch_bytes": 0, "elapsed_seconds": 0}
            if local_eval:
                data["local_eval"] = local_eval
            (instance_dir / "result.json").write_text(json.dumps(data))

        summary = summarize_results(output_dir, agent="pi")
        assert summary["total"] == 4
        assert summary["resolved"] == 0
        assert summary["failed"] == 4
        assert summary["error"] == 0
        assert summary["failed_breakdown"] == {
            "eval_failed": 1,
            "no_patch": 1,
            "timed_out": 1,
            "pending_eval": 1,
        }

    def test_error_group_includes_eval_error_agent_error_container_error(self, tmp_path: Path):
        """Error group: eval error, agent_error, container_error, copy_failed."""
        output_dir = tmp_path / "outputs" / "pi"
        for iid, status, local_eval in [
            ("django__django-11039", "patch_collected", "error"),      # eval error
            ("flask__flask-1000", "agent_error", None),                  # agent crashed
            ("requests__requests-1234", "container_error", None),        # container failed
            ("pandas__pandas-5678", "copy_failed", None),                # docker cp failed
        ]:
            instance_dir = output_dir / iid
            instance_dir.mkdir(parents=True)
            data = {"status": status, "patch_bytes": 0, "elapsed_seconds": 0}
            if local_eval:
                data["local_eval"] = local_eval
            (instance_dir / "result.json").write_text(json.dumps(data))

        summary = summarize_results(output_dir, agent="pi")
        assert summary["total"] == 4
        assert summary["resolved"] == 0
        assert summary["failed"] == 0
        assert summary["error"] == 4
        assert summary["error_breakdown"] == {
            "eval_error": 1,
            "agent_error": 1,
            "container_error": 1,
            "copy_failed": 1,
        }

    def test_mixed_all_categories(self, tmp_path: Path):
        """Test all categories together."""
        output_dir = tmp_path / "outputs" / "pi"
        test_cases = [
            ("i1", "patch_collected", "resolved"),    # resolved
            ("i2", "patch_collected", "failed"),       # failed: eval failed
            ("i3", "patch_collected", "error"),         # error: eval error
            ("i4", "no_patch", None),                     # failed: no patch
            ("i5", "timed_out", None),                    # failed: timeout
            ("i6", "patch_collected", None),              # failed: pending eval
            ("i7", "agent_error", None),                  # error: agent error
            ("i8", "container_error", None),              # error: container error
            ("i9", "copy_failed", None),                  # error: copy failed
        ]
        for iid, status, local_eval in test_cases:
            instance_dir = output_dir / iid
            instance_dir.mkdir(parents=True)
            data = {"status": status, "patch_bytes": 0, "elapsed_seconds": 0}
            if local_eval:
                data["local_eval"] = local_eval
            (instance_dir / "result.json").write_text(json.dumps(data))

        summary = summarize_results(output_dir, agent="pi")
        assert summary["total"] == 9
        assert summary["resolved"] == 1
        assert summary["failed"] == 4  # eval_failed + no_patch + timed_out + pending_eval
        assert summary["error"] == 4  # eval_error + agent_error + container_error + copy_failed
        assert summary["failed_breakdown"] == {
            "eval_failed": 1,
            "no_patch": 1,
            "timed_out": 1,
            "pending_eval": 1,
        }
        assert summary["error_breakdown"] == {
            "eval_error": 1,
            "agent_error": 1,
            "container_error": 1,
            "copy_failed": 1,
        }

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
        assert summary["failed"] == 1  # patch_collected with no eval = pending_eval
        assert summary["failed_breakdown"] == {"pending_eval": 1}
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
        assert summary["failed"] == 2  # eval_failed + timed_out
        assert summary["failed_breakdown"] == {"eval_failed": 1, "timed_out": 1}
        assert summary["error"] == 0
        assert "timed_out" not in summary  # Old key removed

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
        agents_dir = tmp_path / "harnesses"
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
            mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1, "attempt_id": "attempt-001"}
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
        agents_dir = tmp_path / "harnesses"
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
            mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1, "attempt_id": "attempt-001"}
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

        # Create output dir with patch (so eval doesn't return no_patch)
        output_dir = config.output_dir / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        # Create eval report so eval returns resolved
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        (eval_dir / "pi_django__django-11039.pi_django__django-11039.json").write_text(
            json.dumps({"resolved_ids": ["django__django-11039"], "unresolved_ids": [], "error_ids": []})
        )

        # Mock run_instance to avoid actual Docker calls
        with patch.object(runner, "run_instance") as mock_run, \
             patch.object(runner, "run_eval_instance") as mock_eval, \
             patch.object(runner.docker_ops, "remove_image") as mock_remove:
            mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1, "attempt_id": "attempt-001"}
            mock_eval.return_value = {"status": "completed", "local_eval": "resolved"}
            mock_remove.return_value = True
            result = runner.run_all("pi", timeout=3600)

            assert result["resolved"] == 1
            assert result["no_answer"] == 0
            assert result["timeout"] == 0
            assert result["error"] == 0


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

    def test_no_patches_found(self, tmp_path: Path):
        """When no patches exist, return no_patches status with 0 instances."""
        output_dir = tmp_path / "outputs" / "pi"
        output_dir.mkdir(parents=True)

        with patch("swebench_orchestrator.runner.generate_predictions") as mock_preds:
            mock_preds.return_value = (output_dir / "predictions.jsonl", [])
            result = run_eval(output_dir, "pi", swebench_py=tmp_path / "fake_swebench")

        assert result["status"] == "no_patches"
        assert result["instances"] == 0

    def test_harness_error(self, tmp_path: Path):
        """When harness subprocess fails (non-zero returncode), return harness_error."""
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        with patch("swebench_orchestrator.runner.generate_predictions") as mock_preds:
            mock_preds.return_value = (
                output_dir / "predictions.jsonl",
                ["django__django-11039"],
            )
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1)
                result = run_eval(output_dir, "pi", swebench_py=tmp_path / "fake_swebench")

        assert result["status"] == "harness_error"
        assert result["instances"] == 1

    def test_no_harness_report_returns_zero_folded(self, tmp_path: Path):
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

    def test_custom_dataset_name_passed(self, tmp_path: Path):
        """Custom dataset_name should be passed to subprocess command."""
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        with patch("swebench_orchestrator.runner.generate_predictions") as mock_preds:
            mock_preds.return_value = (
                output_dir / "predictions.jsonl",
                ["django__django-11039"],
            )
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                run_eval(
                    output_dir,
                    "pi",
                    dataset_name="custom/dataset",
                    swebench_py=tmp_path / "fake_swebench",
                )

            # Verify dataset_name was passed in the command
            call_args = mock_run.call_args
            cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("args", [])
            assert "--dataset_name" in cmd
            assert "custom/dataset" in cmd


class TestRunnerRunAllWaitForContainers:
    """Tests for run_all wait-for-container safety net (Issue #15)."""

    def test_run_all_waits_for_stale_containers(self, tmp_path: Path):
        """run_all should wait for stale containers before starting new instances."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
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

        # Create output dir with patch (so eval doesn't return no_patch)
        output_dir = config.output_dir / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        # Create eval report so eval returns resolved
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        (eval_dir / "pi_django__django-11039.pi_django__django-11039.json").write_text(
            json.dumps({"resolved_ids": ["django__django-11039"], "unresolved_ids": [], "error_ids": []})
        )

        # Mock wait_for_agent_containers to verify it's called
        with patch.object(runner.docker_ops, "wait_for_agent_containers") as mock_wait:
            mock_wait.return_value = True
            with patch.object(runner, "run_instance") as mock_run, \
                 patch.object(runner, "run_eval_instance") as mock_eval, \
                 patch.object(runner.docker_ops, "remove_image") as mock_remove:
                mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1, "attempt_id": "attempt-001"}
                mock_eval.return_value = {"status": "completed", "local_eval": "resolved"}
                mock_remove.return_value = True
                result = runner.run_all("pi", timeout=3600)

                # Verify wait was called before any run_instance calls
                mock_wait.assert_called_once_with("pi", timeout_seconds=3600)
                assert result["resolved"] == 1
                assert result["no_answer"] == 0
                assert result["timeout"] == 0
                assert result["error"] == 0

    def test_run_all_logs_wait_progress(self, tmp_path: Path):
        """run_all should log progress while waiting for containers."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        # Create output dir with patch (so eval doesn't return no_patch)
        output_dir = config.output_dir / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        # Create eval report so eval returns resolved
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        (eval_dir / "pi_django__django-11039.pi_django__django-11039.json").write_text(
            json.dumps({"resolved_ids": ["django__django-11039"], "unresolved_ids": [], "error_ids": []})
        )

        with patch.object(runner.docker_ops, "wait_for_agent_containers") as mock_wait:
            mock_wait.return_value = True
            with patch.object(runner, "run_instance") as mock_run, \
                 patch.object(runner, "run_eval_instance") as mock_eval, \
                 patch.object(runner.docker_ops, "remove_image") as mock_remove:
                mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1, "attempt_id": "attempt-001"}
                mock_eval.return_value = {"status": "completed", "local_eval": "resolved"}
                mock_remove.return_value = True
                with patch("swebench_orchestrator.runner.logger") as mock_logger:
                    result = runner.run_all("pi", timeout=3600)
                    # Should log that it's waiting for containers
                    mock_logger.info.assert_called()

    def test_run_all_continues_after_wait_timeout(self, tmp_path: Path):
        """run_all should continue even if wait times out (force-kills happened)."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        # Create output dir with patch (so eval doesn't return no_patch)
        output_dir = config.output_dir / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        # Create eval report so eval returns resolved
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        (eval_dir / "pi_django__django-11039.pi_django__django-11039.json").write_text(
            json.dumps({"resolved_ids": ["django__django-11039"], "unresolved_ids": [], "error_ids": []})
        )

        with patch.object(runner.docker_ops, "wait_for_agent_containers") as mock_wait:
            # Wait times out (returns False) but run should still proceed
            mock_wait.return_value = False
            with patch.object(runner, "run_instance") as mock_run, \
                 patch.object(runner, "run_eval_instance") as mock_eval, \
                 patch.object(runner.docker_ops, "remove_image") as mock_remove:
                mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1, "attempt_id": "attempt-001"}
                mock_eval.return_value = {"status": "completed", "local_eval": "resolved"}
                mock_remove.return_value = True
                result = runner.run_all("pi", timeout=3600)

                # Should still run the instance after force-killing stale containers
                assert result["resolved"] == 1
                assert result["no_answer"] == 0
                assert result["timeout"] == 0
                assert result["error"] == 0


class TestRunnerMethods:
    """Tests for Runner class methods (Issue #14 follow-up)."""

    def test_runner_run_instance_invalid_run_id(self, tmp_path: Path):
        """Runner.run_instance should raise ValueError for non-existent run_id."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        with pytest.raises(ValueError, match="not found"):
            runner.run_instance("pi", "django__django-11039", run_id="nonexistent-run")

    def test_runner_run_all_resume_skips_existing(self, tmp_path: Path):
        """run_all with resume=True should skip instances that already have results."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        # Create cache with 2 instances
        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }, {
                "instance_id": "flask__flask-1000",
                "repo": "pallets/flask",
                "base_commit": "def456",
                "problem_statement": "Fix another bug",
            }])
        )

        # Create existing result for first instance with local_eval=resolved
        output_dir = config.output_dir / "pi"
        (output_dir / "django__django-11039").mkdir(parents=True)
        (output_dir / "django__django-11039" / "result.json").write_text(
            '{"status": "patch_collected", "local_eval": "resolved"}'
        )

        # Create output dir with patch for flask (so eval doesn't return no_patch)
        (output_dir / "flask__flask-1000").mkdir(parents=True)
        (output_dir / "flask__flask-1000" / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        # Create eval report so eval returns resolved
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        (eval_dir / "pi_flask__flask-1000.pi_flask__flask-1000.json").write_text(
            json.dumps({"resolved_ids": ["flask__flask-1000"], "unresolved_ids": [], "error_ids": []})
        )

        with patch.object(runner, "run_instance") as mock_run, \
             patch.object(runner, "run_eval_instance") as mock_eval, \
             patch.object(runner.docker_ops, "remove_image") as mock_remove:
            mock_run.return_value = {"status": "patch_collected", "exit_code": 0, "elapsed_seconds": 1, "attempt_id": "attempt-001"}
            mock_eval.return_value = {"status": "completed", "local_eval": "resolved"}
            mock_remove.return_value = True
            result = runner.run_all("pi", timeout=3600, resume=True)

            # Should skip django (pre-existing), run flask (new)
            assert result["total"] == 2
            assert result["resolved"] == 2  # django pre-existing + flask new
            assert result["no_answer"] == 0
            # Only called once (for flask)
            mock_run.assert_called_once()

    def test_runner_run_all_exception_counts_as_failed(self, tmp_path: Path):
        """run_all should count exceptions as failures."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        with patch.object(runner, "run_instance") as mock_run:
            mock_run.side_effect = RuntimeError("Docker not available")
            result = runner.run_all("pi", timeout=3600)

            assert result["total"] == 1
            assert result["error"] == 1
            assert result["resolved"] == 0

    def test_runner_summarize(self, tmp_path: Path):
        """Runner.summarize should delegate to summarize_results."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        # Create output with instances (output_dir = workspace/outputs)
        output_dir = config.output_dir / "pi"
        for iid, local_eval in [("django__django-11039", "resolved"), ("flask__flask-1000", "failed")]:
            instance_dir = output_dir / iid
            instance_dir.mkdir(parents=True)
            (instance_dir / "result.json").write_text(
                f'{{"status": "patch_collected", "local_eval": "{local_eval}"}}'
            )

        summary = runner.summarize("pi")
        assert summary["agent"] == "pi"
        assert summary["total"] == 2
        assert summary["resolved"] == 1
        assert summary["failed"] == 1

    def test_runner_eval(self, tmp_path: Path):
        """Runner.eval should delegate to run_eval with config values."""
        swebench_py = tmp_path / ".venv/swebench/bin/python"
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
            hf_dataset="custom/dataset",
            swebench_venv=".venv/swebench",
        )
        # Create the swebench_py file so it exists
        swebench_py.parent.mkdir(parents=True, exist_ok=True)
        swebench_py.touch()

        runner = Runner(config)

        # Create output dir with patches (output_dir = workspace/outputs)
        output_dir = config.output_dir / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        with patch("swebench_orchestrator.runner.run_eval") as mock_eval:
            mock_eval.return_value = {"status": "completed", "instances": 1, "folded": 1}
            result = runner.eval("pi")

            # Verify run_eval was called with config values
            call_kwargs = mock_eval.call_args.kwargs if mock_eval.call_args.kwargs else {}
            assert call_kwargs["agent"] == "pi"
            assert call_kwargs["dataset_name"] == "custom/dataset"
            assert call_kwargs["swebench_py"] == swebench_py

    def test_run_all_interleaves_eval_and_image_removal(self, tmp_path: Path):
        """run_all should run eval + remove image after each instance's work phase."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        # Create cache with 1 instance
        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        # Create output dir with patch (so eval doesn't return no_patch)
        output_dir = config.output_dir / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        # Create eval report so eval returns resolved
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        (eval_dir / "pi_django__django-11039.pi_django__django-11039.json").write_text(
            json.dumps({"resolved_ids": ["django__django-11039"], "unresolved_ids": [], "error_ids": []})
        )

        with patch.object(runner, "run_instance") as mock_run_instance, \
             patch.object(runner, "run_eval_instance") as mock_run_eval, \
             patch.object(runner.docker_ops, "remove_image") as mock_remove:
            mock_run_instance.return_value = {"status": "patch_collected", "elapsed_seconds": 42, "attempt_id": "attempt-001"}
            mock_run_eval.return_value = {"status": "completed", "local_eval": "resolved"}
            mock_remove.return_value = True

            result = runner.run_all("pi", timeout=3600)

            # Should have run once
            assert result["total"] == 1
            assert result["resolved"] == 1
            assert result["no_answer"] == 0
            assert result["timeout"] == 0
            assert result["error"] == 0

            # Verify interleaved order: work → eval (image cleanup now happens inside run_instance)
            mock_run_instance.assert_called_once()
            mock_run_eval.assert_called_once()
            # Image cleanup is now handled by run_instance internally (cleanup_image=True by default)
            # run_all no longer calls remove_image directly

    def test_run_all_skips_eval_on_work_failure(self, tmp_path: Path):
        """run_all should skip eval when work phase fails."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        with patch.object(runner, "run_instance") as mock_run_instance, \
             patch.object(runner, "run_eval_instance") as mock_run_eval, \
             patch.object(runner.docker_ops, "remove_image") as mock_remove:
            # Work phase fails
            mock_run_instance.return_value = {"status": "timed_out", "elapsed_seconds": 3600, "attempt_id": "attempt-001"}

            result = runner.run_all("pi", timeout=3600)

            assert result["total"] == 1
            assert result["timeout"] == 1
            assert result["resolved"] == 0

            # Eval and image removal should NOT be called when work fails
            mock_run_eval.assert_not_called()
            mock_remove.assert_not_called()


class TestGeneratePredictions:
    """Tests for generate_predictions function."""

    def test_generates_from_patches(self, tmp_path: Path):
        """generate_predictions should create predictions.jsonl from patch files."""
        output_dir = tmp_path / "outputs" / "pi"
        for iid in ["django__django-11039", "flask__flask-1000"]:
            instance_dir = output_dir / iid
            instance_dir.mkdir(parents=True)
            (instance_dir / "patch.diff").write_text(f"diff --git a/{iid} b/{iid}\n+fix")

        # Create instance without patch (should be skipped)
        no_patch_dir = output_dir / "requests__requests-1234"
        no_patch_dir.mkdir(parents=True)

        preds_file, instance_ids = generate_predictions(output_dir, "pi")

        assert preds_file == output_dir / "predictions.jsonl"
        assert len(instance_ids) == 2
        assert "django__django-11039" in instance_ids
        assert "flask__flask-1000" in instance_ids
        assert "requests__requests-1234" not in instance_ids

        # Verify predictions file content
        lines = preds_file.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            pred = json.loads(line)
            assert pred["model_name_or_path"] == "pi"
            assert "model_patch" in pred

    def test_skips_empty_patches(self, tmp_path: Path):
        """generate_predictions should skip instances with empty patch files."""
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        # Empty patch file
        (instance_dir / "patch.diff").write_text("")

        preds_file, instance_ids = generate_predictions(output_dir, "pi")

        assert len(instance_ids) == 0
        # predictions.jsonl should still be created but empty
        assert preds_file.exists()
        assert preds_file.read_text().strip() == ""

    def test_skips_eval_and_logs_dirs(self, tmp_path: Path):
        """generate_predictions should skip eval and logs directories."""
        output_dir = tmp_path / "outputs" / "pi"
        (output_dir / "eval").mkdir(parents=True)
        (output_dir / "logs").mkdir(parents=True)

        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        preds_file, instance_ids = generate_predictions(output_dir, "pi")

        assert len(instance_ids) == 1
        assert "eval" not in instance_ids
        assert "logs" not in instance_ids


class TestSummarizeResultsAutoAgent:
    """Tests for summarize_results auto-detection of agent name."""

    def test_auto_detect_agent_name(self, tmp_path: Path):
        """When agent=None, should use parent directory name as agent."""
        output_dir = tmp_path / "outputs" / "pi"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        summary = summarize_results(output_dir)  # agent=None

        # output_dir.parent is tmp_path/outputs, so agent = "outputs"
        assert summary["agent"] == "outputs"
        assert summary["total"] == 1


class TestFoldHarnessResultsLogging:
    """Tests for fold_harness_results logging."""

    def test_logs_folded_count(self, tmp_path: Path):
        """fold_harness_results should log the number of folded instances."""
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

        with patch("swebench_orchestrator.runner.logger") as mock_logger:
            folded = fold_harness_results(output_dir, report_data)
            assert folded == 2
            # Verify info log was called with the count
            mock_logger.info.assert_called()
            call_args = mock_logger.info.call_args
            assert "2" in str(call_args)


class TestRunAllStats:
    """Tests for run_all stats initialization and return values."""

    def test_run_all_initializes_stats_from_existing_results(self, tmp_path: Path):
        """run_all should read existing results and initialize stats at startup."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        # Create cache with 3 instances
        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }, {
                "instance_id": "flask__flask-1000",
                "repo": "pallets/flask",
                "base_commit": "def456",
                "problem_statement": "Fix another bug",
            }, {
                "instance_id": "requests__requests-1234",
                "repo": "psf/requests",
                "base_commit": "ghi789",
                "problem_statement": "Fix yet another bug",
            }])
        )

        # Create existing results with different statuses
        output_dir = config.output_dir / "pi"
        
        # Resolved instance
        resolved_dir = output_dir / "django__django-11039"
        resolved_dir.mkdir(parents=True)
        (resolved_dir / "result.json").write_text(
            '{"status": "patch_collected", "local_eval": "resolved"}'
        )
        
        # Failed (no_answer) instance
        failed_dir = output_dir / "flask__flask-1000"
        failed_dir.mkdir(parents=True)
        (failed_dir / "result.json").write_text(
            '{"status": "patch_collected", "local_eval": "failed"}'
        )
        
        # Timed out instance
        timeout_dir = output_dir / "requests__requests-1234"
        timeout_dir.mkdir(parents=True)
        (timeout_dir / "result.json").write_text(
            '{"status": "timed_out"}'
        )

        # With resume=True, should skip all pre-existing and return just their stats
        with patch.object(runner, "run_instance") as mock_run, \
             patch.object(runner, "run_eval_instance") as mock_eval, \
             patch.object(runner.docker_ops, "remove_image") as mock_remove:
            mock_run.return_value = {"status": "patch_collected", "elapsed_seconds": 42, "attempt_id": "attempt-001"}
            mock_eval.return_value = {"status": "completed", "local_eval": "resolved"}
            mock_remove.return_value = True

            result = runner.run_all("pi", timeout=3600, resume=True)

            # Should skip all 3 pre-existing, run 0 new
            assert result["total"] == 3
            assert result["resolved"] == 1
            assert result["no_answer"] == 1
            assert result["timeout"] == 1
            assert result["error"] == 0
            mock_run.assert_not_called()  # No new runs
            mock_eval.assert_not_called()  # No evals

    def test_run_all_stats_update_on_new_runs(self, tmp_path: Path):
        """run_all should update stats as new instances complete."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }, {
                "instance_id": "flask__flask-1000",
                "repo": "pallets/flask",
                "base_commit": "def456",
                "problem_statement": "Fix another bug",
            }, {
                "instance_id": "requests__requests-1234",
                "repo": "psf/requests",
                "base_commit": "ghi789",
                "problem_statement": "Fix yet another bug",
            }])
        )

        # Pre-existing: 1 resolved, 1 no_answer
        output_dir = config.output_dir / "pi"
        resolved_dir = output_dir / "django__django-11039"
        resolved_dir.mkdir(parents=True)
        (resolved_dir / "result.json").write_text(
            '{"status": "patch_collected", "local_eval": "resolved"}'
        )
        failed_dir = output_dir / "flask__flask-1000"
        failed_dir.mkdir(parents=True)
        (failed_dir / "result.json").write_text(
            '{"status": "patch_collected", "local_eval": "failed"}'
        )

        # Create eval report for new instance
        new_dir = output_dir / "requests__requests-1234"
        new_dir.mkdir(parents=True)
        (new_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        (eval_dir / "pi_requests__requests-1234.pi_requests__requests-1234.json").write_text(
            json.dumps({"resolved_ids": ["requests__requests-1234"], "unresolved_ids": [], "error_ids": []})
        )

        with patch.object(runner, "run_instance") as mock_run, \
             patch.object(runner, "run_eval_instance") as mock_eval, \
             patch.object(runner.docker_ops, "remove_image") as mock_remove:
            # Return different eval results for different instances
            def eval_side_effect(agent, instance_id, output_dir, dataset_name, swebench_py):
                if instance_id == "requests__requests-1234":
                    return {"status": "completed", "local_eval": "resolved"}
                return {"status": "completed", "local_eval": "failed"}
            
            mock_run.return_value = {"status": "patch_collected", "elapsed_seconds": 42, "attempt_id": "attempt-001"}
            mock_eval.side_effect = eval_side_effect
            mock_remove.return_value = True

            # With resume=True, should skip pre-existing and only run the new instance
            result = runner.run_all("pi", timeout=3600, resume=True)

            # 2 pre-existing + 1 new resolved
            assert result["total"] == 3
            assert result["resolved"] == 2  # django (pre-existing) + requests (new)
            assert result["no_answer"] == 1  # flask (pre-existing)
            assert result["timeout"] == 0
            assert result["error"] == 0
            # Only called once (for requests)
            assert mock_run.call_count == 1

    def test_run_all_counts_work_phase_timeout(self, tmp_path: Path):
        """run_all should count timed_out work phase as timeout."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        with patch.object(runner, "run_instance") as mock_run:
            mock_run.return_value = {"status": "timed_out", "elapsed_seconds": 3600, "attempt_id": "attempt-001"}

            result = runner.run_all("pi", timeout=3600)

            assert result["total"] == 1
            assert result["timeout"] == 1
            assert result["resolved"] == 0

    def test_run_all_counts_work_phase_container_error(self, tmp_path: Path):
        """run_all should count container_error work phase as error."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        with patch.object(runner, "run_instance") as mock_run:
            mock_run.return_value = {"status": "container_error", "elapsed_seconds": 10, "attempt_id": "attempt-001"}

            result = runner.run_all("pi", timeout=3600)

            assert result["total"] == 1
            assert result["error"] == 1
            assert result["resolved"] == 0

    def test_run_all_counts_exception_as_error(self, tmp_path: Path):
        """run_all should count exceptions as error."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        with patch.object(runner, "run_instance") as mock_run:
            mock_run.side_effect = RuntimeError("Docker not available")

            result = runner.run_all("pi", timeout=3600)

            assert result["total"] == 1
            assert result["error"] == 1
            assert result["resolved"] == 0

    def test_run_all_counts_eval_error(self, tmp_path: Path):
        """run_all should count eval error as error."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        output_dir = config.output_dir / "pi"
        new_dir = output_dir / "django__django-11039"
        new_dir.mkdir(parents=True)
        (new_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")

        with patch.object(runner, "run_instance") as mock_run, \
             patch.object(runner, "run_eval_instance") as mock_eval, \
             patch.object(runner.docker_ops, "remove_image") as mock_remove:
            mock_run.return_value = {"status": "patch_collected", "elapsed_seconds": 42, "attempt_id": "attempt-001"}
            mock_eval.return_value = {"status": "completed", "local_eval": "error"}
            mock_remove.return_value = True

            result = runner.run_all("pi", timeout=3600)

            assert result["total"] == 1
            assert result["error"] == 1
            assert result["resolved"] == 0

    def test_run_all_logs_stats_at_start_of_each_instance(self, tmp_path: Path):
        """run_all should log stats at the start of each instance."""
        config = Config(
            repo_root=tmp_path,
            cache_file=tmp_path / "cache.json",
        )
        runner = Runner(config)

        config.cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }, {
                "instance_id": "flask__flask-1000",
                "repo": "pallets/flask",
                "base_commit": "def456",
                "problem_statement": "Fix another bug",
            }])
        )

        # Pre-existing: 1 resolved
        output_dir = config.output_dir / "pi"
        resolved_dir = output_dir / "django__django-11039"
        resolved_dir.mkdir(parents=True)
        (resolved_dir / "result.json").write_text(
            '{"status": "patch_collected", "local_eval": "resolved"}'
        )

        with patch.object(runner, "run_instance") as mock_run, \
             patch.object(runner, "run_eval_instance") as mock_eval, \
             patch.object(runner.docker_ops, "remove_image") as mock_remove, \
             patch("swebench_orchestrator.runner.logger") as mock_logger:
            mock_run.return_value = {"status": "patch_collected", "elapsed_seconds": 42, "attempt_id": "attempt-001"}
            mock_eval.return_value = {"status": "completed", "local_eval": "resolved"}
            mock_remove.return_value = True

            runner.run_all("pi", timeout=3600)

            # Verify logging was called with stats format
            # The mock calls contain the format string as args[0], then values
            info_calls = mock_logger.info.call_args_list
            # Should have logged stats for each instance
            stats_logs = [call for call in info_calls if "completed:" in str(call)]
            assert len(stats_logs) == 2  # 2 instances
            # First instance (pre-existing resolved) should show completed: 1, resolved: 1
            # Check the args passed to the logger (args[0] is format string)
            args = stats_logs[0].args
            assert args[4] == 1  # completed count (args[1]=idx, args[2]=total, args[3]=iid, args[4]=completed)
            assert args[5] == 1  # resolved count


class TestNormalizeLocalEval:
    """Tests for _normalize_local_eval helper."""

    def test_string_resolved(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval("resolved") == "resolved"

    def test_string_failed(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval("failed") == "failed"

    def test_string_error(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval("error") == "error"

    def test_dict_resolved_true(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval({"resolved": True}) == "resolved"

    def test_dict_resolved_false(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval({"resolved": False}) == "failed"

    def test_dict_error_true(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval({"resolved": False, "error": True}) == "error"

    def test_dict_error_false(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval({"resolved": False, "error": False}) == "failed"

    def test_none_returns_none(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval(None) is None

    def test_empty_dict_returns_failed(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval({}) == "failed"

    def test_unknown_string_returns_as_is(self):
        from swebench_orchestrator.runner import _normalize_local_eval
        assert _normalize_local_eval("unknown") == "unknown"
