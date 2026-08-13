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

    def __init__(self, mode: str = "success", pull_needed: bool = True) -> None:
        super().__init__()
        self.mode = mode
        self._call_log: list[dict] = []
        self._pull_needed = pull_needed

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
        """Simulate docker cp — may fail based on mode.

        Docker cp creates a single-nested structure: dest/instance_id/files
        (the basename of the source path becomes the subdirectory).
        """
        self._call_log.append({"action": "copy", "container": container_name})

        if self.mode == "cp_fail":
            return False

        # Create mock output files with single-nested structure (like real docker cp)
        dest_path.mkdir(parents=True, exist_ok=True)
        instance_id = src_path.split("/")[-1]
        # Single nesting: dest/instance_id/result.json
        nested_dir = dest_path / instance_id
        nested_dir.mkdir(parents=True, exist_ok=True)

        (nested_dir / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 42, "elapsed_seconds": 5}'
        )
        (nested_dir / "patch.diff").write_text("diff --git a test b/test\n+hello")
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

    def image_exists(self, image_name: str) -> bool:
        """Mock image existence check."""
        if self._pull_needed:
            return False
        return True

    def pull_image(self, image_name: str) -> bool:
        """Mock image pull."""
        self._call_log.append({"action": "pull", "image": image_name})
        return True


class TestRunInstanceSuccess:
    """Test successful container run with output copying."""

    def test_success_writes_result_json(self, test_workspace, mock_agent, cache_file):
        """Container exits 0 → result.json written with patch_collected status."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "harnesses",
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
            agents_dir=test_workspace / "harnesses",
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
            agents_dir=test_workspace / "harnesses",
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
            agents_dir=test_workspace / "harnesses",
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
            agents_dir=test_workspace / "harnesses",
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
            agents_dir=test_workspace / "harnesses",
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
                agents_dir=test_workspace / "harnesses",
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
        agent_dir = test_workspace / "harnesses" / "nobundle"
        agent_dir.mkdir(parents=True)

        with pytest.raises(ValueError, match="bundle not found"):
            run_instance(
                agents_dir=test_workspace / "harnesses",
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
                agents_dir=test_workspace / "harnesses",
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
            agents_dir=test_workspace / "harnesses",
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
            agents_dir=test_workspace / "harnesses",
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
            agents_dir=test_workspace / "harnesses",
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
            agents_dir=test_workspace / "harnesses",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "container_error"
        assert result["exit_code"] == 1


class TestRunInstanceImagePull:
    """Test image pull logic."""

    def test_pulls_image_when_not_exists(self, test_workspace, mock_agent, cache_file):
        """When image doesn't exist, should pull it."""
        docker_ops = MockDockerOps("success", pull_needed=True)
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "harnesses",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "patch_collected"
        # Verify pull_image was called (check if any action is "pull")
        pull_actions = [c for c in docker_ops._call_log if c["action"] == "pull"]
        assert len(pull_actions) == 1

    def test_skips_pull_when_image_exists(self, test_workspace, mock_agent, cache_file):
        """When image exists, should not pull."""
        docker_ops = MockDockerOps("success", pull_needed=False)
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "harnesses",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "patch_collected"
        # Verify pull_image was NOT called
        pull_actions = [c for c in docker_ops._call_log if c["action"] == "pull"]
        assert len(pull_actions) == 0


class MockDockerOpsDirect(MockDockerOps):
    """MockDockerOps that creates direct directory structure (no nesting, like some docker versions).

    In the non-nested case, files are placed directly in dest_path without any instance_id subdirectory.
    The runner's else branch handles this by iterating over cp_tmp directly.
    """

    def copy_from_container(self, container_name, src_path, dest_path):
        """Simulate docker cp with direct structure (no nesting at all)."""
        self._call_log.append({"action": "copy", "container": container_name})

        dest_path.mkdir(parents=True, exist_ok=True)
        instance_id = src_path.split("/")[-1]
        # Direct structure: files directly in dest_path (no instance_id subdirectory)
        (dest_path / "result.json").write_text(
            '{"status": "patch_collected", "patch_bytes": 42}'
        )
        (dest_path / "patch.diff").write_text("diff --git a/test b/test\n+hello")
        return True


class TestRunInstanceNestedCopy:
    """Test nested vs non-nested copy paths."""

    def test_nested_copy_path(self, test_workspace, mock_agent, cache_file):
        """When docker cp nests the instance dir, should flatten it."""
        # Default MockDockerOps creates nested structure (like real docker cp)
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "harnesses",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "patch_collected"
        # Verify files are at the right level (not nested)
        result_file = output_dir / "test-agent" / "django__django-11039" / "result.json"
        assert result_file.exists()
        import json
        data = json.loads(result_file.read_text())
        assert data["status"] == "patch_collected"

    def test_non_nested_copy_path(self, test_workspace, mock_agent, cache_file):
        """When docker cp does not nest the instance dir, should copy directly."""
        docker_ops = MockDockerOpsDirect("success")
        output_dir = test_workspace / "outputs"

        result = run_instance(
            agents_dir=test_workspace / "harnesses",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        assert result["status"] == "patch_collected"
        result_file = output_dir / "test-agent" / "django__django-11039" / "result.json"
        assert result_file.exists()


class TestRunInstanceEdgeCases:
    """Test edge cases in run_instance."""

    def test_os_chown_oserror_handled(self, test_workspace, mock_agent, cache_file):
        """os.chown OSError should be silently ignored."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        with patch("os.chown", side_effect=OSError("Permission denied")):
            result = run_instance(
                agents_dir=test_workspace / "harnesses",
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
                timeout=3600,
                cache_file=cache_file,
                docker_ops=docker_ops,
            )

        # Should still succeed despite chown failure
        assert result["status"] == "patch_collected"

    def test_storage_warning_logged(self, test_workspace, mock_agent, cache_file):
        """Storage warning should be logged when disk usage is high."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        with patch("swebench_orchestrator.runner.check_storage") as mock_check:
            mock_check.return_value = {"is_warning": True, "usage_pct": 92.5, "threshold_pct": 90.0}
            with patch("swebench_orchestrator.runner.logger") as mock_logger:
                result = run_instance(
                    agents_dir=test_workspace / "harnesses",
                    agent="test-agent",
                    instance_id="django__django-11039",
                    output_dir=output_dir,
                    timeout=3600,
                    cache_file=cache_file,
                    docker_ops=docker_ops,
                )

                assert result["status"] == "patch_collected"
                # Verify warning was logged
                mock_logger.warning.assert_called()
                assert "92.5" in str(mock_logger.warning.call_args)

    def test_final_status_from_result_json(self, test_workspace, mock_agent, cache_file):
        """Final status should be read from result.json if it exists."""
        # Use MockDockerOpsDirect which creates direct structure (no nesting)
        docker_ops = MockDockerOpsDirect("success")
        output_dir = test_workspace / "outputs"

        # Override copy to write a result.json with specific status
        def custom_copy(container_name, src_path, dest_path):
            dest_path.mkdir(parents=True, exist_ok=True)
            # Direct structure: files directly in dest_path
            (dest_path / "result.json").write_text(
                '{"status": "resolved", "patch_bytes": 42}'
            )
            return True

        docker_ops.copy_from_container = custom_copy

        result = run_instance(
            agents_dir=test_workspace / "harnesses",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # Status should come from result.json, not default "patch_collected"
        assert result["status"] == "resolved"

    def test_invalid_result_json_uses_default_status(self, test_workspace, mock_agent, cache_file):
        """Invalid result.json should fall back to default status."""
        docker_ops = MockDockerOps("success")
        output_dir = test_workspace / "outputs"

        def custom_copy(container_name, src_path, dest_path):
            dest_path.mkdir(parents=True, exist_ok=True)
            instance_id = src_path.split("/")[-1]
            instance_dir = dest_path / instance_id
            instance_dir.mkdir(exist_ok=True)
            (instance_dir / "result.json").write_text("not valid json")
            return True

        docker_ops.copy_from_container = custom_copy

        result = run_instance(
            agents_dir=test_workspace / "harnesses",
            agent="test-agent",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # Should fall back to default "patch_collected"
        assert result["status"] == "patch_collected"
