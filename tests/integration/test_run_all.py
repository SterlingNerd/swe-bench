"""Integration tests for run_all with resume and per-instance eval."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.config import Config
from swebench_orchestrator.docker_ops import ContainerResult, DockerOps
from swebench_orchestrator.runner import Runner


@pytest.fixture(autouse=True)
def cleanup_workspace_env():
    """Clean up SWE_WORKSPACE_DIR env var after each test."""
    yield
    if "SWE_WORKSPACE_DIR" in os.environ:
        del os.environ["SWE_WORKSPACE_DIR"]


class MockDockerOpsForRunAll(DockerOps):
    """DockerOps mock for run_all tests."""

    def __init__(self) -> None:
        super().__init__()
        self.run_count = 0

    def run_container(self, image_name, container_name, flags, command, timeout_seconds=3600):
        self.run_count += 1
        return ContainerResult(
            exit_code=0,
            status="success",
            elapsed_seconds=5,
            container_name=container_name,
        )

    def copy_from_container(self, container_name, src_path, dest_path):
        """Simulate docker cp with nested structure."""
        dest_path.mkdir(parents=True, exist_ok=True)
        instance_id = src_path.split("/")[-1]
        # Nested: dest/instance_id/instance_id/result.json (like real docker cp)
        nested_dir = dest_path / instance_id / instance_id
        nested_dir.mkdir(parents=True, exist_ok=True)
        (nested_dir / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 42, "elapsed_seconds": 5}'
        )
        (nested_dir / "patch.diff").write_text("diff --git a/test b/test\n+fix")
        return True

    def inspect_container_state(self, container_name):
        return "running"

    def remove_container(self, container_name):
        return True

    def release_container(self, container_name):
        return True

    def image_exists(self, image_name):
        return True


def make_config(test_workspace, output_dir, runs_dir, cache_file):
    """Create a Config with overridden paths for testing."""
    import os
    os.environ["SWE_WORKSPACE_DIR"] = str(test_workspace / "workspace")
    config = Config(
        repo_root=test_workspace,
        cache_file=cache_file,
    )
    object.__setattr__(config, "output_dir", output_dir)
    object.__setattr__(config, "runs_dir", runs_dir)
    return config


class TestRunAllWithResume:
    """Tests for run_all with --resume flag."""

    def test_resume_skips_completed(self, test_workspace, mock_agent, cache_file):
        """--resume skips instances that already have result.json."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)
        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForRunAll()

        # Pre-create a completed instance
        completed_dir = output_dir / "test-agent" / "django__django-11039"
        completed_dir.mkdir(parents=True)
        (completed_dir / "result.json").write_text('{"status": "patch_collected"}')

        # Run with resume
        result = runner.run_all("test-agent", resume=True)

        # Should skip the completed instance, only run the incomplete one
        assert result["skipped"] == 1
        assert result["run"] == 1  # Only flask instance

    def test_resume_without_completed_runs_all(self, test_workspace, mock_agent, cache_file):
        """Without completed instances, resume runs all."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)
        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForRunAll()

        result = runner.run_all("test-agent", resume=True)

        # Both instances should be run (neither has result.json yet)
        assert result["skipped"] == 0
        assert result["run"] == 2

    def test_no_resume_runs_all(self, test_workspace, mock_agent, cache_file):
        """Without --resume, all instances are processed."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)
        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForRunAll()

        # Pre-create a completed instance
        completed_dir = output_dir / "test-agent" / "django__django-11039"
        completed_dir.mkdir(parents=True)
        (completed_dir / "result.json").write_text('{"status": "patch_collected"}')

        result = runner.run_all("test-agent", resume=False)

        # Both instances should be run (no skipping)
        assert result["skipped"] == 0
        assert result["run"] == 2


class TestPerInstanceEval:
    """Tests for per-instance eval (evaluate immediately after agent run)."""

    def test_eval_folds_into_result(self, test_workspace, mock_agent, cache_file):
        """Per-instance eval folds harness result into attempt's result.json."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)
        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForRunAll()

        # Run instance
        result = runner.run_instance("test-agent", "django__django-11039")
        assert result["status"] == "patch_collected"

        # Verify output files exist
        result_file = output_dir / "test-agent" / "django__django-11039" / "result.json"
        assert result_file.exists()

        patch_file = output_dir / "test-agent" / "django__django-11039" / "patch.diff"
        assert patch_file.exists()


class TestRunAllErrorHandling:
    """Tests for run_all error handling."""

    def test_handles_failed_instances(self, test_workspace, mock_agent, cache_file):
        """run_all tracks failed instances correctly."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)

        # Create a DockerOps that fails on the second instance
        class FailingDockerOps(DockerOps):
            def __init__(self):
                super().__init__()
                self.call_count = 0

            def run_container(self, image_name, container_name, flags, command, timeout_seconds=3600):
                self.call_count += 1
                if self.call_count == 2:
                    return ContainerResult(
                        exit_code=124,
                        status="timed_out",
                        elapsed_seconds=3600,
                        container_name=container_name,
                    )
                return ContainerResult(
                    exit_code=0,
                    status="success",
                    elapsed_seconds=5,
                    container_name=container_name,
                )

            def copy_from_container(self, container_name, src_path, dest_path):
                dest_path.mkdir(parents=True, exist_ok=True)
                instance_id = src_path.split("/")[-1]
                instance_dir = dest_path / instance_id
                instance_dir.mkdir(exist_ok=True)
                (instance_dir / "result.json").write_text(
                    '{"status": "patch_collected", "patch_bytes": 42}'
                )
                (instance_dir / "patch.diff").write_text("diff")
                return True

            def inspect_container_state(self, container_name):
                return "running"

            def remove_container(self, container_name):
                return True

            def release_container(self, container_name):
                return True

            def image_exists(self, image_name):
                return True

        runner = Runner(config)
        runner.docker_ops = FailingDockerOps()

        result = runner.run_all("test-agent")

        # First instance succeeds, second times out
        assert result["run"] == 2
        assert result["failed"] == 1  # One timed out


class TestRunAllStatistics:
    """Tests for run_all statistics tracking."""

    def test_tracks_run_skipped_failed_counts(self, test_workspace, mock_agent, cache_file):
        """run_all returns accurate run/skipped/failed counts."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)

        # Pre-create one completed instance
        completed_dir = output_dir / "test-agent" / "django__django-11039"
        completed_dir.mkdir(parents=True)
        (completed_dir / "result.json").write_text('{"status": "patch_collected"}')

        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForRunAll()

        result = runner.run_all("test-agent", resume=True)

        assert result["run"] == 1  # Only flask instance run
        assert result["skipped"] == 1  # django skipped
        assert result["failed"] == 0  # No failures
