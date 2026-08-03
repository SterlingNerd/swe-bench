"""Unit tests for the runner module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.runner import (
    Runner,
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
