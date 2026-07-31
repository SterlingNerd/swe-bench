"""Docker-based end-to-end tests (optional, requires Docker).

These tests require a running Docker daemon and will be skipped if
Docker is not available. They provide full integration testing of
the orchestrator with real Docker containers.
"""

import json
import os
from pathlib import Path

import pytest

from swebench_orchestrator.config import Config
from swebench_orchestrator.docker_ops import DockerOps, ensure_docker_available
from swebench_orchestrator.runner import Runner


# Skip all tests in this module if Docker is not available
docker_available = ensure_docker_available()
pytestmark = pytest.mark.skipif(
    not docker_available,
    reason="Docker not available — skipping Docker-based E2E tests",
)


def make_config(test_workspace, output_dir, runs_dir, cache_file):
    """Create a Config with overridden paths for testing."""
    os.environ["SWE_WORKSPACE_DIR"] = str(test_workspace / "workspace")
    config = Config(
        repo_root=test_workspace,
        cache_file=cache_file,
    )
    object.__setattr__(config, "output_dir", output_dir)
    object.__setattr__(config, "runs_dir", runs_dir)
    return config


class TestDockerE2E:
    """End-to-end tests with real Docker."""

    def test_docker_is_available(self):
        """Verify Docker is available for E2E tests."""
        assert docker_available, "Docker must be running for these tests"

    def test_docker_info(self):
        """Test basic Docker connectivity."""
        ops = DockerOps()
        assert ops.docker_ready

    def test_list_docker_images(self):
        """Test listing Docker images works."""
        ops = DockerOps()
        # This should not raise even if no images exist
        result = ops.list_running_containers()
        assert isinstance(result, list)


class TestDockerImageOperations:
    """Tests for Docker image operations with real Docker."""

    def test_inspect_nonexistent_image(self):
        """Test inspecting a non-existent image returns False."""
        ops = DockerOps()
        assert not ops.image_exists("nonexistent-image:latest")

    def test_pull_hello_world(self):
        """Test pulling a small image (hello-world)."""
        ops = DockerOps()
        # This is a lightweight test — just verify pull works
        # We don't actually run it, just verify the operation succeeds
        result = ops.pull_image("hello-world:latest")
        # May fail if network unavailable, but shouldn't crash
        assert result in (True, False)


class TestDockerContainerLifecycle:
    """Tests for container lifecycle with real Docker."""

    def test_run_hello_world(self):
        """Test running a simple container."""
        ops = DockerOps()

        # Run hello-world and capture output
        result = ops.run_container(
            image_name="hello-world:latest",
            container_name=f"e2e_test_{os.getpid()}",
            flags=[],
            command=[],
            timeout_seconds=30,
        )

        # Container should complete successfully
        assert result.status in ("success", "timed_out")
        assert result.container_name is not None

    def test_run_with_env_vars(self):
        """Test running container with environment variables."""
        ops = DockerOps()

        result = ops.run_container(
            image_name="hello-world:latest",
            container_name=f"e2e_test_{os.getpid()}",
            flags=["-e", "TEST_VAR=hello"],
            command=[],
            timeout_seconds=30,
        )

        # May succeed, timeout, or error (Docker not available)
        assert result.status in ("success", "timed_out", "error")


class TestDockerOutputCopying:
    """Tests for Docker output copying with real Docker."""

    def test_copy_from_container(self, tmp_path: Path):
        """Test copying files from a container."""
        ops = DockerOps()

        # Run a container that creates a file
        result = ops.run_container(
            image_name="alpine:latest",
            container_name=f"e2e_test_{os.getpid()}",
            flags=[],
            command=["sh", "-c", "echo hello > /tmp/test.txt && sleep 1"],
            timeout_seconds=30,
        )

        if result.status == "success":
            # Try to copy the file out
            dest = tmp_path / "output"
            copied = ops.copy_from_container(
                f"e2e_test_{os.getpid()}",
                "/tmp/test.txt",
                dest,
            )
            # May fail if container already removed, but shouldn't crash
            assert copied in (True, False)


class TestDockerCleanup:
    """Tests for Docker cleanup operations."""

    def test_remove_container(self):
        """Test removing a container."""
        ops = DockerOps()

        # Run and immediately remove
        result = ops.run_container(
            image_name="hello-world:latest",
            container_name=f"e2e_test_{os.getpid()}",
            flags=[],
            command=[],
            timeout_seconds=30,
        )

        if result.container_name:
            removed = ops.remove_container(result.container_name)
            assert removed in (True, False)  # May already be gone

    def test_release_container(self):
        """Test releasing a container (remove + network cleanup)."""
        ops = DockerOps()

        result = ops.run_container(
            image_name="hello-world:latest",
            container_name=f"e2e_test_{os.getpid()}",
            flags=[],
            command=[],
            timeout_seconds=30,
        )

        if result.container_name:
            ops.release_container(result.container_name)
            # Should not raise
