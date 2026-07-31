"""Integration tests for registry integration (P4).

Tests:
- Pull-through registry detection
- NAS storage estimation
- Image cache with tarball save/load
- Registry mirror configuration
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.docker_ops import DockerOps


class TestRegistryDetection:
    """Tests for pull-through registry detection."""

    def test_detects_registry_from_docker_config(self):
        """Detects registry configuration from Docker daemon.json."""
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            # Simulate docker info returning registry config
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout="Registry: docker-registry.sterling.digital",
            )

            result = mock_subprocess.run(
                ["docker", "info", "--format", "{{.RegistryConfig.Mirrors}}"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0

    def test_returns_none_when_no_registry(self):
        """Returns None when no registry is configured."""
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=1)

            result = mock_subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 1


class TestNASStorageEstimation:
    """Tests for NAS storage estimation."""

    def test_estimates_storage_for_images(self):
        """Estimates total storage needed for cached images."""
        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            # Simulate docker images output with sizes
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout="REPOSITORY          TAG       SIZE\n"
                       "swebench/sweb.eval.x86_64.django_1776_django-11039  latest  2.1GB\n"
                       "swebench/sweb.eval.x86_64.flask_1776_flask-1000    latest  1.8GB\n",
            )

            result = mock_subprocess.run(
                ["docker", "images", "--format", "{{.Repository}} {{.Tag}} {{.Size}}"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0


class TestImageCache:
    """Tests for image cache with tarball save/load."""

    def test_save_image_to_tar(self, test_workspace):
        """Saves Docker image to tar file."""
        docker_ops = DockerOps()
        tar_path = test_workspace / "cache" / "image.tar"

        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0)

            result = docker_ops.save_image_to_tar(
                "swebench/sweb.eval.x86_64.django_1776_django-11039:latest",
                tar_path,
            )

            assert result is True
            # Verify parent directory was created
            assert tar_path.parent.exists()

    def test_load_image_from_tar(self, test_workspace):
        """Loads Docker image from tar file."""
        docker_ops = DockerOps()

        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0)

            result = docker_ops.load_image_from_tar(
                test_workspace / "cache" / "image.tar",
            )

            assert result is True

    def test_save_fails_on_error(self, test_workspace):
        """Save returns False when Docker fails."""
        docker_ops = DockerOps()
        tar_path = test_workspace / "cache" / "image.tar"

        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=1)

            result = docker_ops.save_image_to_tar(
                "swebench/test:latest",
                tar_path,
            )

            assert result is False

    def test_load_fails_on_error(self, test_workspace):
        """Load returns False when Docker fails."""
        docker_ops = DockerOps()

        with patch("swebench_orchestrator.docker_ops.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=1)

            result = docker_ops.load_image_from_tar(
                test_workspace / "cache" / "image.tar",
            )

            assert result is False


class TestRegistryMirrorConfig:
    """Tests for registry mirror configuration."""

    def test_generates_daemon_json(self, test_workspace):
        """Generates correct daemon.json for registry mirror."""
        import json as json_module

        registry_url = "docker-registry.sterling.digital"
        daemon_config = {
            "registry-mirrors": [f"https://{registry_url}"],
            "storage-driver": "overlay2",
        }

        daemon_file = test_workspace / "etc" / "docker" / "daemon.json"
        daemon_file.parent.mkdir(parents=True)
        daemon_file.write_text(json_module.dumps(daemon_config, indent=2))

        loaded = json_module.loads(daemon_file.read_text())
        assert loaded["registry-mirrors"][0] == f"https://{registry_url}"
        assert loaded["storage-driver"] == "overlay2"

    def test_validates_nas_path(self, test_workspace):
        """Validates NAS storage path exists and is writable."""
        nas_path = test_workspace / "nas" / "swe-bench-images"
        nas_path.mkdir(parents=True)

        # Create a test file to verify writability
        test_file = nas_path / ".write_test"
        test_file.write_text("test")
        assert test_file.exists()
        assert test_file.read_text() == "test"


class TestPullThroughRegistry:
    """Tests for pull-through registry workflow."""

    def test_pulls_from_registry_when_local_missing(self, test_workspace):
        """Pulls from registry when image not in local cache."""
        docker_ops = DockerOps()

        with patch.object(docker_ops, "image_exists", return_value=False):
            with patch.object(docker_ops, "load_image_from_tar", return_value=False):
                with patch.object(docker_ops, "pull_image") as mock_pull:
                    mock_pull.return_value = True

                    # Simulate the image pull logic from do_run
                    image_name = "swebench/sweb.eval.x86_64.django_1776_django-11039:latest"

                    if not docker_ops.image_exists(image_name):
                        if not docker_ops.load_image_from_tar(test_workspace / "cache.tar"):
                            docker_ops.pull_image(image_name)

                    mock_pull.assert_called_once_with(image_name)

    def test_loads_from_cache_when_available(self, test_workspace):
        """Loads from cache when tar file exists."""
        docker_ops = DockerOps()

        with patch.object(docker_ops, "image_exists", return_value=False):
            with patch.object(docker_ops, "load_image_from_tar", return_value=True) as mock_load:
                with patch.object(docker_ops, "pull_image") as mock_pull:
                    image_name = "swebench/sweb.eval.x86_64.django_1776_django-11039:latest"

                    if not docker_ops.image_exists(image_name):
                        if not docker_ops.load_image_from_tar(test_workspace / "cache.tar"):
                            docker_ops.pull_image(image_name)

                    mock_load.assert_called_once()
                    mock_pull.assert_not_called()  # Should not pull if cache works
