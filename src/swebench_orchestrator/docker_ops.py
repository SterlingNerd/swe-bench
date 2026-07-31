"""Docker operations — container lifecycle, image management, and output copying."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ContainerResult:
    """Result of running a Docker container."""

    exit_code: int
    status: str  # success, timed_out, error
    elapsed_seconds: int
    output_copied: bool = False
    container_name: Optional[str] = None


def ensure_docker_available() -> bool:
    """Check if Docker is available and running.

    Returns:
        True if Docker is available, False otherwise.
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


class DockerOps:
    """Operations for managing Docker containers and images.

    Provides a clean interface for container lifecycle management,
    output copying, and image operations.
    """

    def __init__(self) -> None:
        self._docker_ready: Optional[bool] = None

    @property
    def docker_ready(self) -> bool:
        """Check if Docker is available (cached)."""
        if self._docker_ready is None:
            self._docker_ready = ensure_docker_available()
        return self._docker_ready

    def require_docker(self) -> bool:
        """Require Docker to be available.

        Returns:
            True if Docker is available.

        Raises:
            RuntimeError: If Docker is not available.
        """
        if not self.docker_ready:
            raise RuntimeError(
                "Docker is unavailable. Restart Docker Desktop and WSL, "
                "then verify: docker run --rm hello-world"
            )
        return True

    def image_exists(self, image_name: str) -> bool:
        """Check if a Docker image exists locally.

        Args:
            image_name: Full image name (e.g., swebench/sweb.eval.x86_64.django_1776_django-11039:latest)

        Returns:
            True if the image exists.
        """
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def pull_image(self, image_name: str) -> bool:
        """Pull a Docker image.

        Args:
            image_name: Full image name to pull.

        Returns:
            True if pull succeeded.
        """
        try:
            result = subprocess.run(
                ["docker", "pull", image_name],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode == 0:
                logger.info("Pulled image: %s", image_name)
                return True
            else:
                logger.error("Failed to pull %s: %s", image_name, result.stderr.strip())
                return False
        except subprocess.TimeoutExpired:
            logger.error("Timeout pulling image: %s", image_name)
            return False

    def load_image_from_tar(self, tar_path: Path) -> bool:
        """Load a Docker image from a tar archive.

        Args:
            tar_path: Path to the tar file.

        Returns:
            True if load succeeded.
        """
        try:
            result = subprocess.run(
                ["docker", "load", "-i", str(tar_path)],
                capture_output=True,
                text=True,
                timeout=600,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def save_image_to_tar(self, image_name: str, tar_path: Path) -> bool:
        """Save a Docker image to a tar archive.

        Args:
            image_name: Full image name to save.
            tar_path: Destination path for the tar file.

        Returns:
            True if save succeeded.
        """
        try:
            tar_path.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["docker", "save", image_name, "-o", str(tar_path)],
                capture_output=True,
                text=True,
                timeout=600,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def run_container(
        self,
        image_name: str,
        container_name: str,
        flags: list[str],
        command: list[str],
        timeout_seconds: int = 3600,
    ) -> ContainerResult:
        """Run a Docker container with the given configuration.

        Args:
            image_name: Docker image to run.
            container_name: Name for the container.
            flags: Additional docker run flags.
            command: Command to run inside the container.
            timeout_seconds: Maximum runtime in seconds (0 = no timeout).

        Returns:
            ContainerResult with exit code, status, and timing.
        """
        import datetime

        started_at = datetime.datetime.now(datetime.timezone.utc)

        docker_cmd = ["docker", "run", "--name", container_name] + flags + [image_name] + command

        try:
            if timeout_seconds > 0:
                result = subprocess.run(
                    docker_cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds + 30,  # Extra time for --kill-after
                )
                exit_code = result.returncode
            else:
                result = subprocess.run(docker_cmd, capture_output=True, text=True)
                exit_code = result.returncode

            elapsed = int(
                (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds()
            )

            # Docker timeout returns 124
            if exit_code == 124:
                return ContainerResult(
                    exit_code=exit_code,
                    status="timed_out",
                    elapsed_seconds=elapsed,
                    container_name=container_name,
                )
            elif exit_code != 0:
                return ContainerResult(
                    exit_code=exit_code,
                    status="error",
                    elapsed_seconds=elapsed,
                    container_name=container_name,
                )
            else:
                return ContainerResult(
                    exit_code=0,
                    status="success",
                    elapsed_seconds=elapsed,
                    container_name=container_name,
                )

        except subprocess.TimeoutExpired:
            elapsed = int(
                (datetime.datetime.now(datetime.timezone.utc) - started_at).total_seconds()
            )
            return ContainerResult(
                exit_code=124,
                status="timed_out",
                elapsed_seconds=elapsed,
                container_name=container_name,
            )

    def remove_container(self, container_name: str) -> bool:
        """Remove a Docker container (force if needed).

        Also releases any orphaned network endpoints.

        Args:
            container_name: Name of the container to remove.

        Returns:
            True if removal succeeded or container didn't exist.
        """
        # Remove the container
        try:
            result = subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Return True if successful OR if container doesn't exist (already cleaned)
            return result.returncode == 0 or "No such container" in result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def disconnect_endpoint(self, container_name: str) -> bool:
        """Disconnect an orphaned network endpoint.

        Args:
            container_name: Name of the container whose endpoint to release.

        Returns:
            True if disconnection succeeded or endpoint didn't exist.
        """
        try:
            result = subprocess.run(
                ["docker", "network", "disconnect", "-f", "bridge", container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0 or "No such network" in result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def release_container(self, container_name: str) -> None:
        """Remove a container and release its network endpoint.

        This is the proper order per Docker docs: disconnect first, then remove.
        However, we try remove first (which may fail if endpoint is orphaned),
        then disconnect to clean up any orphaned endpoints.
        """
        self.remove_container(container_name)
        self.disconnect_endpoint(container_name)

    def copy_from_container(
        self,
        container_name: str,
        src_path: str,
        dest_path: Path,
    ) -> bool:
        """Copy files from a container to the host.

        Args:
            container_name: Name of the container.
            src_path: Source path inside the container.
            dest_path: Destination path on the host.

        Returns:
            True if copy succeeded.
        """
        try:
            dest_path.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["docker", "cp", f"{container_name}:{src_path}", str(dest_path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def inspect_container_state(self, container_name: str) -> Optional[str]:
        """Get the state of a container.

        Args:
            container_name: Name of the container.

        Returns:
            State string (running, exited, dead, error) or None if not found.
        """
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Status}}", container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def list_running_containers(self, prefix: Optional[str] = None) -> list[str]:
        """List running container names, optionally filtered by prefix.

        Args:
            prefix: If provided, only return containers starting with this prefix.

        Returns:
            List of container name strings.
        """
        try:
            cmd = ["docker", "ps", "--format", "{{.Names}}"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return []

            names = result.stdout.strip().split("\n")
            if prefix:
                names = [n for n in names if n.startswith(prefix)]
            return [n for n in names if n]  # Filter empty strings
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

    def wait_for_container(
        self,
        container_name: str,
        timeout_seconds: int = 3600,
        check_interval: int = 1,
    ) -> bool:
        """Wait for a container to stop.

        Args:
            container_name: Name of the container to wait for.
            timeout_seconds: Maximum time to wait.
            check_interval: Seconds between checks.

        Returns:
            True if container stopped within timeout, False if timed out.
        """
        import signal

        started_at = time.time()
        while time.time() - started_at < timeout_seconds:
            containers = self.list_running_containers(prefix=container_name)
            if not containers:
                return True
            time.sleep(check_interval)
        return False
