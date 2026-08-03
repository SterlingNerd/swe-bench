"""Integration tests for runner with mocked Docker operations.

Mirrors T2_docker_mocked.sh — tests do_run() logic paths:
- success: container exits 0, outputs copied correctly
- timeout: container exits 124, timed_out status recorded
- error: container exits non-zero, agent_error recorded
- cp_fail: container succeeds but copy fails
- oom: container OOM killed (exit 137)

Uses pytest-mock to mock DockerOps instead of a fake docker binary.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.docker_ops import ContainerResult, DockerOps
from swebench_orchestrator.runner import run_instance


class MockDockerOps(DockerOps):
    """DockerOps mock that simulates different container behaviors."""

    def __init__(self, mode: str = "success") -> None:
        super().__init__()
        self.mode = mode
        self._call_log: list[dict] = []

    def run_container(self, image_name, container_name, flags, command, timeout_seconds=3600):
        """Simulate container run based on mode."""
        self._call_log.append({"action": "run", "image": image_name, "container": container_name})

        if self.mode == "success":
            return ContainerResult(
                exit_code=0,
                status="success",
                elapsed_seconds=5,
                container_name=container_name,
            )
        elif self.mode == "timeout":
            return ContainerResult(
                exit_code=124,
                status="timed_out",
                elapsed_seconds=3600,
                container_name=container_name,
            )
        elif self.mode == "error":
            return ContainerResult(
                exit_code=1,
                status="error",
                elapsed_seconds=10,
                container_name=container_name,
            )
        elif self.mode == "oom":
            return ContainerResult(
                exit_code=137,
                status="error",
                elapsed_seconds=30,
                container_name=container_name,
            )
        elif self.mode == "cp_fail":
            # Container succeeds (exit 0) but copy will fail
            return ContainerResult(
                exit_code=0,
                status="success",
                elapsed_seconds=5,
                container_name=container_name,
            )
        else:
            return ContainerResult(
                exit_code=1,
                status="error",
                elapsed_seconds=0,
                container_name=container_name,
            )

    def copy_from_container(self, container_name, src_path, dest_path):
        """Simulate docker cp — may fail based on mode."""
        self._call_log.append({"action": "copy", "container": container_name})

        if self.mode == "cp_fail":
            return False

        # Create mock output files
        dest_path.mkdir(parents=True, exist_ok=True)
        instance_id = src_path.split("/")[-1]
        instance_dir = dest_path / instance_id
        instance_dir.mkdir(exist_ok=True)

        (instance_dir / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 42, "elapsed_seconds": 5}'
        )
        (instance_dir / "patch.diff").write_text("diff --git a/test b/test\n+hello")
        return True

    def inspect_container_state(self, container_name):
        """Return mock container state."""
        self._call_log.append({"action": "inspect", "container": container_name})
        return "running"

    def remove_container(self, container_name):
        """Mock container removal."""
        self._call_log.append({"action": "remove", "container": container_name})
        return True

    def release_container(self, container_name):
        """Mock container release."""
        self._call_log.append({"action": "release", "container": container_name})
        return True


class TestRunInstanceSuccess:
    """Test successful container run with output copying."""

    def test_success_writes_result_json(self, test_workspace, mock_agent, cache_file):
        """Container exits 0 → result.json written with patch_collected status."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "patch_collected"
        assert result["exit_code"] == 0

        # Verify result.json was written
        result_file = output_dir / "test-agent" / "django__django-11039" / "result.json"
        assert result_file.exists()
        import json
        data = json.loads(result_file.read_text())
        assert data["status"] == "patch_collected"

    def test_success_copies_patch(self, test_workspace, mock_agent, cache_file):
        """Container exits 0 → patch.diff copied to output."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        patch_file = output_dir / "test-agent" / "django__django-11039" / "patch.diff"
        assert patch_file.exists()
        assert len(patch_file.read_text()) > 0


class TestRunInstanceTimeout:
    """Test container timeout handling."""

    def test_timeout_writes_timed_out_status(self, test_workspace, mock_agent, cache_file):
        """Container exits 124 → timed_out status recorded."""
        docker_ops = MockDockerOps("timeout")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "timed_out"
        assert result["exit_code"] == 124

        # Verify result.json
        result_file = output_dir / "test-agent" / "django__django-11039" / "result.json"
        assert result_file.exists()
        import json
        data = json.loads(result_file.read_text())
        assert data["status"] == "timed_out"


class TestRunInstanceError:
    """Test container error handling."""

    def test_error_writes_container_error_status(self, test_workspace, mock_agent, cache_file):
        """Container exits non-zero → container_error status recorded."""
        docker_ops = MockDockerOps("error")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "container_error"
        assert result["exit_code"] == 1


class TestRunInstanceOOM:
    """Test OOM kill handling."""

    def test_oom_writes_container_error_status(self, test_workspace, mock_agent, cache_file):
        """Container exits 137 (OOM) → container_error status recorded."""
        docker_ops = MockDockerOps("oom")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "container_error"
        assert result["exit_code"] == 137


class TestRunInstanceCopyFailure:
    """Test copy failure handling."""

    def test_cp_fail_returns_copy_failed(self, test_workspace, mock_agent, cache_file):
        """Container succeeds but copy fails → copy_failed status."""
        docker_ops = MockDockerOps("cp_fail")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "copy_failed"
        assert result["exit_code"] == 1


class TestRunInstanceValidation:
    """Test input validation before Docker operations."""

    def test_invalid_agent_raises(self, test_workspace, cache_file):
        """Non-existent agent → ValueError."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        with pytest.raises(ValueError, match="not found"):
            run_instance(
                agents_dir=test_workspace / "agents",
                agent="nonexistent",
                instance_id="django__django-11039",
                output_dir=output_dir,
                timeout=3600,
                cache_file=cache_file,
                docker_ops=docker_ops,
            )

    def test_missing_bundle_raises(self, test_workspace, cache_file):
        """Agent without bundle → ValueError."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        # Create agent dir without bundle
        agent_dir = test_workspace / "agents" / "nobundle"
        agent_dir.mkdir(parents=True)

        with pytest.raises(ValueError, match="bundle not found"):
            run_instance(
                agents_dir=test_workspace / "agents",
                agent="nobundle",
                instance_id="django__django-11039",
                output_dir=output_dir,
                timeout=3600,
                cache_file=cache_file,
                docker_ops=docker_ops,
            )

    def test_missing_instance_raises(self, test_workspace, mock_agent, cache_file):
        """Non-existent instance → ValueError."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        with pytest.raises(ValueError, match="not found"):
            run_instance(
                agents_dir=test_workspace / "agents",
                agent="test-agent",
                instance_id="nonexistent__instance-999",
                output_dir=output_dir,
                timeout=3600,
                cache_file=cache_file,
                docker_ops=docker_ops,
            )


class TestRunInstanceElapsedTime:
    """Test elapsed time tracking."""

    def test_elapsed_seconds_recorded(self, test_workspace, mock_agent, cache_file):
        """Elapsed seconds are recorded in result.json."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # Elapsed seconds should be recorded (may be 0 in fast tests)
        assert "elapsed_seconds" in result

        # Verify in result.json
        result_file = output_dir / "test-agent" / "django__django-11039" / "result.json"
        import json
        data = json.loads(result_file.read_text())
        assert "elapsed_seconds" in data


class TestRunInstanceReturnCodes:
    """Test return code behavior matching bash version."""

    def test_patch_collected_returns_0(self, test_workspace, mock_agent, cache_file):
        """patch_collected status → exit 0 (success)."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # patch_collected is not a failure status
        assert result["status"] == "patch_collected"
        assert result["exit_code"] == 0

    def test_timed_out_returns_nonzero(self, test_workspace, mock_agent, cache_file):
        """timed_out status → non-zero exit."""
        docker_ops = MockDockerOps("timeout")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "timed_out"
        assert result["exit_code"] == 124

    def test_container_error_returns_nonzero(self, test_workspace, mock_agent, cache_file):
        """container_error status → non-zero exit."""
        docker_ops = MockDockerOps("error")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "agents",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "container_error"
        assert result["exit_code"] == 1
