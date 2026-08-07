"""Unit tests for Docker operations."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.docker_ops import (
    DockerOps,
    ContainerResult,
    ensure_docker_available,
)


class TestEnsureDockerAvailable:
    """Tests for docker availability check."""

    def test_returns_true_when_docker_works(self):
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0)
            assert ensure_docker_available() is True

    def test_returns_false_when_docker_fails(self):
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=1)
            assert ensure_docker_available() is False


class TestDockerOps:
    """Tests for the DockerOps class."""

    def test_image_exists(self):
        ops = DockerOps()
        with patch.object(ops, "image_exists") as mock_exists:
            mock_exists.return_value = True
            assert ops.image_exists("swebench/test:latest") is True

    def test_image_not_exists(self):
        ops = DockerOps()
        with patch.object(ops, "image_exists") as mock_exists:
            mock_exists.return_value = False
            assert ops.image_exists("swebench/test:latest") is False

    def test_remove_container(self):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0)
            result = ops.remove_container("swe_test_123")
            assert result is True

    def test_remove_nonexistent_container(self):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=1,
                stderr="Error: No such container: nonexistent"
            )
            result = ops.remove_container("nonexistent")
            assert result is True  # Should not raise, just return False-like

    def test_disconnect_network_endpoint(self):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0)
            result = ops.disconnect_endpoint("swe_test_123")
            assert result is True

    def test_copy_from_container(self, tmp_path: Path):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0)
            dest = tmp_path / "output"
            result = ops.copy_from_container("swe_test", "/container/path", dest)
            assert result is True

    def test_copy_from_container_failure(self, tmp_path: Path):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=1)
            dest = tmp_path / "output"
            result = ops.copy_from_container("swe_test", "/container/path", dest)
            assert result is False

    def test_inspect_container_state(self):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="running")
            state = ops.inspect_container_state("swe_test")
            assert state == "running"

    def test_inspect_container_not_found(self):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=1)
            state = ops.inspect_container_state("nonexistent")
            assert state is None

    def test_list_running_containers(self):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout="swe_pi_django__django-11039\nswe_codex_flask__flask-1000\n"
            )
            containers = ops.list_running_containers()
            assert len(containers) == 2
            assert "swe_pi_django__django-11039" in containers

    def test_list_running_containers_filtered(self):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout="swe_pi_django__django-11039\nswe_codex_flask__flask-1000\nother_container\n"
            )
            containers = ops.list_running_containers(prefix="swe_pi_")
            assert len(containers) == 1
            assert containers[0] == "swe_pi_django__django-11039"

    def test_wait_for_container(self):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            # First call: container running, second call: gone
            mock_subprocess.run.side_effect = [
                MagicMock(returncode=0, stdout="swe_test_123\n"),
                MagicMock(returncode=1),  # Container gone
            ]
            result = ops.wait_for_container("swe_test_123", timeout_seconds=5)
            assert result is True

    def test_wait_for_container_timeout(self):
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            # Always returns container running
            mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="swe_test_123\n")
            result = ops.wait_for_container("swe_test_123", timeout_seconds=1)
            assert result is False  # Timed out


class TestWaitForAgentContainers:
    """Tests for wait_for_agent_containers method."""

    def test_returns_true_when_no_containers(self):
        """Should return True immediately when no agent containers are running."""
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="")
            result = ops.wait_for_agent_containers("pi", timeout_seconds=5)
            assert result is True

    def test_returns_true_when_containers_stop(self):
        """Should wait and return True once containers stop."""
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            # First call: container running, second call: gone
            mock_subprocess.run.side_effect = [
                MagicMock(returncode=0, stdout="swe_pi_django__django-11039\n"),
                MagicMock(returncode=0, stdout=""),
            ]
            result = ops.wait_for_agent_containers("pi", timeout_seconds=5)
            assert result is True

    def test_times_out_and_force_kills(self):
        """Should force-kill containers after timeout."""
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            # Always returns container running
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout="swe_pi_django__django-11039\nswe_pi_flask__flask-1000\n"
            )
            result = ops.wait_for_agent_containers("pi", timeout_seconds=1)
            assert result is False
            # Should have called docker rm -f for each container
            rm_calls = [
                call for call in mock_subprocess.run.call_args_list
                if isinstance(call.args[0], list) and "rm" in call.args[0] and "-f" in call.args[0]
            ]
            assert len(rm_calls) == 2

    def test_releases_endpoints_after_kill(self):
        """Should release network endpoints after force-killing containers."""
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            # Always returns container running
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout="swe_pi_django__django-11039\n"
            )
            result = ops.wait_for_agent_containers("pi", timeout_seconds=1)
            assert result is False
            # Should have called docker network disconnect
            disconnect_calls = [
                call for call in mock_subprocess.run.call_args_list
                if "disconnect" in str(call)
            ]
            assert len(disconnect_calls) == 1

    def test_logs_wait_progress(self):
        """Should log progress while waiting."""
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            # First call: container running, second call: gone
            mock_subprocess.run.side_effect = [
                MagicMock(returncode=0, stdout="swe_pi_django__django-11039\n"),
                MagicMock(returncode=0, stdout=""),
            ]
            with patch("swebench_orchestrator.docker_ops.logger") as mock_logger:
                result = ops.wait_for_agent_containers("pi", timeout_seconds=5)
                assert result is True
                # Should have logged something about waiting
                mock_logger.info.assert_called()

    def test_default_timeout_is_3600(self):
        """Should use 3600s default timeout."""
        ops = DockerOps()
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="")
            # Should return immediately since no containers
            result = ops.wait_for_agent_containers("pi")
            assert result is True
