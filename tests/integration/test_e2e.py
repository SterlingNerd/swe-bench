"""End-to-end integration tests — full workflow simulation.

Tests complete workflows from run creation through result collection,
mirroring T3_e2e.sh from the bash test suite.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.config import Config
from swebench_orchestrator.docker_ops import ContainerResult, DockerOps
from swebench_orchestrator.manifest import RunManager
from swebench_orchestrator.runner import Runner, summarize_results


@pytest.fixture(autouse=True)
def cleanup_workspace_env():
    """Clean up SWE_WORKSPACE_DIR env var after each test."""
    yield
    # Restore original state
    if "SWE_WORKSPACE_DIR" in os.environ:
        del os.environ["SWE_WORKSPACE_DIR"]


class MockDockerOpsForE2E(DockerOps):
    """DockerOps mock for end-to-end tests."""

    def __init__(self) -> None:
        super().__init__()
        self._runs: list[dict] = []

    def run_container(self, image_name, container_name, flags, command, timeout_seconds=3600):
        """Simulate successful container run."""
        result = ContainerResult(
            exit_code=0,
            status="success",
            elapsed_seconds=10,
            container_name=container_name,
        )
        self._runs.append({
            "image": image_name,
            "container": container_name,
            "timeout": timeout_seconds,
        })
        return result

    def copy_from_container(self, container_name, src_path, dest_path):
        """Simulate successful copy."""
        dest_path.mkdir(parents=True, exist_ok=True)
        instance_id = src_path.split("/")[-1]
        instance_dir = dest_path / instance_id
        instance_dir.mkdir(exist_ok=True)

        (instance_dir / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 42, "elapsed_seconds": 10}'
        )
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+hello")
        return True

    def inspect_container_state(self, container_name):
        return "running"

    def remove_container(self, container_name):
        return True

    def release_container(self, container_name):
        return True

    def image_exists(self, image_name):
        return True  # Pretend image exists


def make_config(test_workspace, output_dir, runs_dir, cache_file):
    """Create a Config with overridden paths for testing."""
    os.environ["SWE_WORKSPACE_DIR"] = str(test_workspace / "workspace")
    config = Config(
        repo_root=test_workspace,
        cache_file=cache_file,
    )
    # Override computed dirs for test isolation
    object.__setattr__(config, "output_dir", output_dir)
    object.__setattr__(config, "runs_dir", runs_dir)
    return config


class TestEndToEndRunWorkflow:
    """Test complete run workflow from start to finish."""

    def test_single_instance_run(self, test_workspace, mock_agent, cache_file):
        """Test running a single instance end-to-end."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)

        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForE2E()

        result = runner.run_instance("test-agent", "django__django-11039")

        assert result["status"] == "patch_collected"
        assert result["exit_code"] == 0

        # Verify output files
        result_file = output_dir / "test-agent" / "django__django-11039" / "result.json"
        assert result_file.exists()
        data = json.loads(result_file.read_text())
        assert data["status"] == "patch_collected"

        patch_file = output_dir / "test-agent" / "django__django-11039" / "patch.diff"
        assert patch_file.exists()

    def test_run_creates_manifest(self, test_workspace, mock_agent, cache_file):
        """Test that running creates a manifest entry."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)

        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForE2E()

        runner.run_instance("test-agent", "django__django-11039")

        # Check manifest was created
        manifests = list(runs_dir.glob("run-*/manifest.json"))
        assert len(manifests) == 1

        data = json.loads(manifests[0].read_text())
        assert data["agent"] == "test-agent"

    def test_run_creates_attempt(self, test_workspace, mock_agent, cache_file):
        """Test that running creates an attempt directory."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)

        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForE2E()

        runner.run_instance("test-agent", "django__django-11039")

        # Check attempt directory was created
        run_dirs = list(runs_dir.glob("run-*"))
        assert len(run_dirs) == 1
        tasks_dir = run_dirs[0] / "tasks" / "django__django-11039"
        attempts = [d for d in tasks_dir.iterdir() if d.name.startswith("attempt-")]
        assert len(attempts) == 1

    def test_summarize_after_run(self, test_workspace, mock_agent, cache_file):
        """Test summarizing results after a run."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)

        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForE2E()

        # Run two instances
        runner.run_instance("test-agent", "django__django-11039")
        runner.run_instance("test-agent", "flask__flask-1000")

        # Summarize
        summary = runner.summarize("test-agent")
        assert summary["agent"] == "test-agent"
        assert summary["total"] == 2


class TestEndToEndMultipleRuns:
    """Test multiple runs for the same agent."""

    def test_rerun_creates_new_attempt(self, test_workspace, mock_agent, cache_file):
        """Rerunning an instance creates a new attempt, not overwriting."""
        output_dir = test_workspace / "outputs"
        runs_dir = test_workspace / "runs"

        config = make_config(test_workspace, output_dir, runs_dir, cache_file)

        runner = Runner(config)
        runner.docker_ops = MockDockerOpsForE2E()

        # First run
        runner.run_instance("test-agent", "django__django-11039")

        # Second run (rerun)
        runner.run_instance("test-agent", "django__django-11039")

        # Should have two runs
        run_dirs = list(runs_dir.glob("run-*"))
        assert len(run_dirs) == 2


class TestEndToEndSummarize:
    """Test summarize_results with various result states."""

    def test_summarize_mixed_results(self, test_workspace):
        """Test summarizing with mixed result statuses."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Create instances with different statuses
        for iid, status, local_eval in [
            ("django__django-11039", "patch_collected", "resolved"),
            ("flask__flask-1000", "patch_collected", "failed"),
            ("requests__requests-1234", "timed_out", None),
        ]:
            instance_dir = output_dir / iid
            instance_dir.mkdir()
            (instance_dir / "result.json").write_text(
                json.dumps({
                    "status": status,
                    "local_eval": local_eval,
                    "patch_bytes": 42 if local_eval else 0,
                    "elapsed_seconds": 10,
                })
            )

        summary = summarize_results(output_dir, agent="test-agent")
        assert summary["total"] == 3
        assert summary["resolved"] == 1
        assert summary["failed"] == 1
        assert summary["timed_out"] == 1

    def test_summarize_skips_special_dirs(self, test_workspace):
        """Test that eval/ and logs/ directories are skipped."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        (output_dir / "eval").mkdir()
        (output_dir / "logs").mkdir()

        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir()
        (instance_dir / "result.json").write_text('{"status": "patch_collected"}')

        summary = summarize_results(output_dir, agent="test-agent")
        assert summary["total"] == 1  # Only the instance, not eval/logs
