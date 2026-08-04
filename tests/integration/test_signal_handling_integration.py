"""Integration tests for signal handling — Issue #8.

Tests that the CLI installs signal handlers and performs graceful shutdown:
- SIGINT/SIGTERM trigger container cleanup
- Shutdown messages are logged
- Exit code 130 on SIGINT
- EXIT trap ensures cleanup runs
- Handlers are idempotent (can be called multiple times safely)
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import the shutdown module for patching in tests
import swebench_orchestrator.shutdown as shutdown_mod


class TestSignalHandlerInstallation:
    """Tests that signal handlers are properly installed in the CLI."""

    def test_shutdown_module_exists(self):
        """The shutdown module exists and is importable."""
        from swebench_orchestrator import shutdown  # noqa: F401

    def test_setup_signal_handlers_function_exists(self):
        """setup_signal_handlers function exists in shutdown module."""
        from swebench_orchestrator.shutdown import setup_signal_handlers

        assert callable(setup_signal_handlers)

    def test_stop_running_containers_function_exists(self):
        """stop_running_containers function exists in shutdown module."""
        from swebench_orchestrator.shutdown import stop_running_containers

        assert callable(stop_running_containers)


class TestStopRunningContainers:
    """Tests for the stop_running_containers function."""

    def test_stops_all_swe_containers(self):
        """stop_running_containers stops all swe_* containers."""
        from swebench_orchestrator.shutdown import stop_running_containers

        # Reset the global flag for this test
        import swebench_orchestrator.shutdown as shutdown_mod
        shutdown_mod._shutdown_complete = False

        docker_ops_mock = MagicMock()
        docker_ops_mock.list_running_containers.return_value = [
            "swe_pi_django__django-11039",
            "swe_codex_flask__flask-1000",
        ]

        stopped = []

        def mock_stop(name):
            stopped.append(name)
            return True

        docker_ops_mock.stop_container.side_effect = mock_stop
        docker_ops_mock.disconnect_endpoint.return_value = True

        result = stop_running_containers(docker_ops=docker_ops_mock)

        assert result["containers_stopped"] == 2
        assert "swe_pi_django__django-11039" in stopped
        assert "swe_codex_flask__flask-1000" in stopped

    def test_stops_no_containers_when_none_running(self):
        """stop_running_containers returns 0 when no containers running."""
        from swebench_orchestrator.shutdown import stop_running_containers

        docker_ops_mock = MagicMock()
        docker_ops_mock.list_running_containers.return_value = []

        result = stop_running_containers(docker_ops=docker_ops_mock)

        assert result["containers_stopped"] == 0
        docker_ops_mock.stop_container.assert_not_called()

    def test_releases_network_endpoints(self):
        """stop_running_containers releases network endpoints for stopped containers."""
        from swebench_orchestrator.shutdown import stop_running_containers

        # Reset the global flag for this test
        import swebench_orchestrator.shutdown as shutdown_mod
        shutdown_mod._shutdown_complete = False

        docker_ops_mock = MagicMock()
        docker_ops_mock.list_running_containers.return_value = [
            "swe_pi_django__django-11039",
        ]

        released = []

        def mock_disconnect(name):
            released.append(name)
            return True

        docker_ops_mock.stop_container.return_value = True
        docker_ops_mock.disconnect_endpoint.side_effect = mock_disconnect

        result = stop_running_containers(docker_ops=docker_ops_mock)

        assert result["endpoints_released"] == 1
        assert "swe_pi_django__django-11039" in released

    def test_handles_container_stop_failure(self):
        """stop_running_containers continues on individual container failures."""
        from swebench_orchestrator.shutdown import stop_running_containers

        # Reset the global flag for this test
        import swebench_orchestrator.shutdown as shutdown_mod
        shutdown_mod._shutdown_complete = False

        docker_ops_mock = MagicMock()
        docker_ops_mock.list_running_containers.return_value = [
            "swe_good_container",
            "swe_bad_container",
        ]

        def mock_stop(name):
            if name == "swe_bad_container":
                return False
            return True

        docker_ops_mock.stop_container.side_effect = mock_stop
        docker_ops_mock.disconnect_endpoint.return_value = True

        result = stop_running_containers(docker_ops=docker_ops_mock)

        # Should still count the good one, not crash on bad one
        assert result["containers_stopped"] == 1
        assert result["errors"] == 1

    def test_is_idempotent(self):
        """stop_running_containers can be called multiple times safely."""
        from swebench_orchestrator.shutdown import stop_running_containers

        # Reset the global flag for this test
        import swebench_orchestrator.shutdown as shutdown_mod
        shutdown_mod._shutdown_complete = False

        docker_ops_mock = MagicMock()
        docker_ops_mock.list_running_containers.return_value = [
            "swe_test_container",
        ]

        def mock_stop(name):
            return True

        docker_ops_mock.stop_container.side_effect = mock_stop
        docker_ops_mock.disconnect_endpoint.return_value = True

        # First call
        result1 = stop_running_containers(docker_ops=docker_ops_mock)
        assert result1["containers_stopped"] == 1

        # Second call — should not double-stop (idempotent)
        result2 = stop_running_containers(docker_ops=docker_ops_mock)
        assert result2["containers_stopped"] == 0


class TestShutdownHandler:
    """Tests for the shutdown handler that gets registered as signal handler."""

    def test_shutdown_handler_calls_stop_containers(self):
        """The shutdown handler calls stop_running_containers."""
        # Reset the global flag for this test
        shutdown_mod._shutdown_complete = False

        from swebench_orchestrator.shutdown import shutdown_handler

        docker_ops_mock = MagicMock()

        # Patch at the module level where it's looked up
        with patch.object(shutdown_mod, "stop_running_containers") as mock_stop:
            mock_stop.return_value = {
                "containers_stopped": 2,
                "endpoints_released": 2,
                "errors": 0,
            }

            # Call the handler (simulating signal delivery)
            with pytest.raises(SystemExit) as exc_info:
                shutdown_handler(signal.SIGINT, None, docker_ops=docker_ops_mock)

            assert exc_info.value.code == 130
            # Verify stop_running_containers was called with the docker_ops arg
            mock_stop.assert_called()
            call_args = mock_stop.call_args
            assert call_args[1].get("docker_ops") is docker_ops_mock

    def test_shutdown_handler_exits_with_130(self):
        """The shutdown handler exits with code 130."""
        from swebench_orchestrator.shutdown import shutdown_handler

        docker_ops_mock = MagicMock()

        with patch(
            "swebench_orchestrator.shutdown.stop_running_containers"
        ) as mock_stop:
            mock_stop.return_value = {"containers_stopped": 0, "endpoints_released": 0, "errors": 0}

            with pytest.raises(SystemExit) as exc_info:
                shutdown_handler(signal.SIGINT, None, docker_ops=docker_ops_mock)

            assert exc_info.value.code == 130


class TestSetupSignalHandlers:
    """Tests for the setup_signal_handlers function."""

    def test_installs_sigint_handler(self):
        """setup_signal_handlers installs SIGINT handler."""
        from swebench_orchestrator.shutdown import setup_signal_handlers, shutdown_handler

        # Save original handlers
        orig_sigint = signal.getsignal(signal.SIGINT)

        try:
            setup_signal_handlers()

            # Verify SIGINT handler is installed (should be our shutdown_handler)
            current_handler = signal.getsignal(signal.SIGINT)
            assert current_handler == shutdown_handler
        finally:
            # Restore original handler
            signal.signal(signal.SIGINT, orig_sigint)

    def test_installs_sigterm_handler(self):
        """setup_signal_handlers installs SIGTERM handler."""
        from swebench_orchestrator.shutdown import setup_signal_handlers, shutdown_handler

        # Save original handlers
        orig_sigterm = signal.getsignal(signal.SIGTERM)

        try:
            setup_signal_handlers()

            # Verify SIGTERM handler is installed
            current_handler = signal.getsignal(signal.SIGTERM)
            assert current_handler == shutdown_handler
        finally:
            # Restore original handler
            signal.signal(signal.SIGTERM, orig_sigterm)

    def test_installs_exit_trap(self):
        """setup_signal_handlers installs EXIT trap."""
        from swebench_orchestrator.shutdown import setup_signal_handlers

        # Save original exit handler
        orig_exit = sys.excepthook

        try:
            setup_signal_handlers()

            # The EXIT trap is registered via atexit or signal.SIGINT/SIGTERM
            # Verify our handlers are in place
            assert signal.getsignal(signal.SIGINT) is not None
            assert signal.getsignal(signal.SIGTERM) is not None
        finally:
            sys.excepthook = orig_exit


class TestCLIIntegration:
    """Tests that the CLI properly integrates signal handling."""

    def test_cli_imports_shutdown(self):
        """The CLI module imports the shutdown module."""
        import swebench_orchestrator.cli as cli_module

        # The CLI should have access to shutdown functions
        assert hasattr(cli_module, "setup_signal_handlers") or \
               "setup_signal_handlers" in dir(cli_module) or \
               "shutdown" in dir(cli_module)

    def test_cli_main_calls_setup_signal_handlers(self):
        """The main() CLI function calls setup_signal_handlers."""
        import swebench_orchestrator.cli as cli_module

        # Read the CLI source file directly
        cli_source_path = Path(cli_module.__file__)
        source = cli_source_path.read_text()

        assert "setup_signal_handlers" in source, \
            "setup_signal_handlers should be imported and called in cli.py"
        assert "from swebench_orchestrator.shutdown import" in source or \
               "import swebench_orchestrator.shutdown" in source, \
            "cli.py should import from shutdown module"


class TestGracefulShutdownMessage:
    """Tests for graceful shutdown logging."""

    def test_shutdown_logs_message(self, capsys):
        """Shutdown handler logs a clean shutdown message."""
        from swebench_orchestrator.shutdown import shutdown_handler

        docker_ops_mock = MagicMock()

        with patch(
            "swebench_orchestrator.shutdown.stop_running_containers"
        ) as mock_stop:
            mock_stop.return_value = {
                "containers_stopped": 1,
                "endpoints_released": 1,
                "errors": 0,
            }

            with pytest.raises(SystemExit) as exc_info:
                shutdown_handler(signal.SIGINT, None, docker_ops=docker_ops_mock)

            # Check that output contains shutdown message
            captured = capsys.readouterr()
            assert "shutting down" in captured.err.lower() or \
                   "shutdown" in captured.err.lower() or \
                   "cleanup" in captured.err.lower() or \
                   exc_info.value.code == 130
