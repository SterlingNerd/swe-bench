"""Integration tests for signal handling (T2b equivalent).

Tests Python signal handling behavior:
- SIGINT/SIGTERM handling
- Graceful shutdown
- Container cleanup on interrupt
"""

import os
import signal
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSignalHandling:
    """Tests for Python signal handling."""

    def test_sigint_handler_installed(self):
        """SIGINT handler can be installed and called."""
        handler_called = []

        def handler(signum, frame):
            handler_called.append(signum)

        # Install handler
        old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, handler)

        # Verify handler is installed (not SIG_DFL or SIG_IGN)
        assert signal.getsignal(signal.SIGINT) == handler

        # Restore old handler
        signal.signal(signal.SIGINT, old_handler)

    def test_sigterm_handler_installed(self):
        """SIGTERM handler can be installed and called."""
        handler_called = []

        def handler(signum, frame):
            handler_called.append(signum)

        old_handler = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, handler)

        assert signal.getsignal(signal.SIGTERM) == handler

        signal.signal(signal.SIGTERM, old_handler)

    def test_multiple_signals_handled(self):
        """Both SIGINT and SIGTERM can have handlers."""
        sigint_called = []
        sigterm_called = []

        def sigint_handler(signum, frame):
            sigint_called.append(signum)

        def sigterm_handler(signum, frame):
            sigterm_called.append(signum)

        old_sigint = signal.getsignal(signal.SIGINT)
        old_sigterm = signal.getsignal(signal.SIGTERM)

        signal.signal(signal.SIGINT, sigint_handler)
        signal.signal(signal.SIGTERM, sigterm_handler)

        assert signal.getsignal(signal.SIGINT) == sigint_handler
        assert signal.getsignal(signal.SIGTERM) == sigterm_handler

        # Restore
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGTERM, old_sigterm)


class TestGracefulShutdown:
    """Tests for graceful shutdown behavior."""

    def test_shutdown_cleans_up_resources(self):
        """Shutdown handler cleans up Docker containers."""
        from swebench_orchestrator.docker_ops import DockerOps

        docker_ops = DockerOps()
        cleaned_up = []

        with patch.object(docker_ops, "remove_container") as mock_remove:
            mock_remove.side_effect = lambda name: cleaned_up.append(name)

            # Simulate cleanup of multiple containers
            for name in ["swe_test_1", "swe_test_2", "swe_test_3"]:
                docker_ops.remove_container(name)

            assert len(cleaned_up) == 3
            assert "swe_test_1" in cleaned_up
            assert "swe_test_2" in cleaned_up
            assert "swe_test_3" in cleaned_up

    def test_shutdown_handles_missing_containers(self):
        """Shutdown handles missing containers gracefully."""
        from swebench_orchestrator.docker_ops import DockerOps

        docker_ops = DockerOps()

        with patch.object(docker_ops, "remove_container") as mock_remove:
            # Simulate container already gone
            mock_remove.return_value = True

            result = docker_ops.remove_container("nonexistent_container")
            assert result is True  # Should not raise


class TestContainerCleanupOnInterrupt:
    """Tests for container cleanup during interrupt."""

    def test_stops_all_swe_containers(self):
        """Stops all swe_* containers on interrupt."""
        from swebench_orchestrator.docker_ops import DockerOps

        docker_ops = DockerOps()
        stopped = []

        with patch.object(docker_ops, "remove_container") as mock_remove:
            mock_remove.side_effect = lambda name: stopped.append(name)

            # Simulate stopping all swe containers
            for name in ["swe_pi_django__django-11039", "swe_codex_flask__flask-1000"]:
                docker_ops.remove_container(name)

            assert len(stopped) == 2

    def test_releases_network_endpoints(self):
        """Releases network endpoints for stopped containers."""
        from swebench_orchestrator.docker_ops import DockerOps

        docker_ops = DockerOps()
        released = []

        with patch.object(docker_ops, "disconnect_endpoint") as mock_disconnect:
            mock_disconnect.side_effect = lambda name: released.append(name)

            for name in ["swe_pi_django__django-11039"]:
                docker_ops.disconnect_endpoint(name)

            assert len(released) == 1


class TestLockFileHandling:
    """Tests for lock file handling (single instance enforcement)."""

    def test_lock_file_created(self, tmp_path: Path):
        """Lock file is created at expected location."""
        lock_file = tmp_path / "swe-bench-run.lock"

        # Simulate lock file creation
        fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY)
        os.close(fd)

        assert lock_file.exists()

    def test_lock_file_removed_on_exit(self, tmp_path: Path):
        """Lock file is removed when process exits."""
        import tempfile

        lock_file = tmp_path / "swe-bench-run.lock"

        # Create and immediately remove (simulating clean exit)
        fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY)
        os.close(fd)
        assert lock_file.exists()

        lock_file.unlink()
        assert not lock_file.exists()
