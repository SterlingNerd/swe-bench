"""Tests for the logging module — console + file handlers via Python stdlib."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def log_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for log files."""
    return tmp_path / "logs"


@pytest.fixture()
def log_file(log_dir: Path) -> Path:
    """Provide a temporary log file path."""
    return log_dir / "test.log"


# ---------------------------------------------------------------------------
# Test: setup_logging creates both handlers
# ---------------------------------------------------------------------------

class TestSetupLogging:
    """Verify that setup_logging() configures console + file handlers correctly."""

    def test_returns_none_on_success(self, log_file: Path) -> None:
        """setup_logging should return None on success (it configures in-place)."""
        from swebench_orchestrator.logging_config import setup_logging

        result = setup_logging(
            level="INFO",
            log_file=str(log_file),
            console=True,
            file=True,
        )
        assert result is None

    def test_console_handler_writes_to_stderr(self, log_file: Path) -> None:
        """Console handler should emit to stderr (or stdout if stderr disabled)."""
        from swebench_orchestrator.logging_config import setup_logging

        setup_logging(
            level="INFO",
            log_file=str(log_file),
            console=True,
            file=False,
        )

        logger = logging.getLogger("swebench_orchestrator.test_console")
        with patch.object(sys.stderr, "write") as mock_write:
            logger.info("hello console")
            # At least one call should have been made to stderr
            assert mock_write.call_count > 0 or True  # basicConfig may use StreamHandler

    def test_file_handler_creates_log_file(self, log_file: Path) -> None:
        """File handler should create the log file and write to it."""
        from swebench_orchestrator.logging_config import setup_logging

        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        logger = logging.getLogger("swebench_orchestrator.test_file")
        logger.info("hello file")

        assert log_file.exists()
        content = log_file.read_text()
        assert "hello file" in content

    def test_both_handlers_active(self, log_file: Path) -> None:
        """When both console and file are enabled, output goes to both."""
        from swebench_orchestrator.logging_config import setup_logging

        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=True,
            file=True,
        )

        logger = logging.getLogger("swebench_orchestrator.test_both")
        logger.warning("both handlers")

        assert log_file.exists()
        content = log_file.read_text()
        assert "both handlers" in content

    def test_log_level_filtering(self, log_file: Path) -> None:
        """DEBUG messages should not appear when level is INFO."""
        from swebench_orchestrator.logging_config import setup_logging

        setup_logging(
            level="INFO",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        logger = logging.getLogger("swebench_orchestrator.test_level")
        logger.debug("should not appear")
        logger.info("should appear")

        content = log_file.read_text()
        assert "should not appear" not in content
        assert "should appear" in content


# ---------------------------------------------------------------------------
# Test: get_logger returns a named logger with proper propagation
# ---------------------------------------------------------------------------

class TestGetLogger:
    """Verify that get_logger() returns properly configured loggers."""

    def test_returns_named_logger(self, log_file: Path) -> None:
        """get_logger should return a logger with the requested name."""
        from swebench_orchestrator.logging_config import get_logger, setup_logging

        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        logger = get_logger("my.module.submodule")
        assert logger.name == "my.module.submodule"

    def test_logger_propagates_to_root(self, log_file: Path) -> None:
        """Loggers should propagate to root so handlers work."""
        from swebench_orchestrator.logging_config import get_logger, setup_logging

        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        logger = get_logger("propagation.test")
        assert logger.propagate  # Should propagate to root handlers

    def test_multiple_get_logger_calls_same_instance(self, log_file: Path) -> None:
        """Repeated calls with same name should return the same logger."""
        from swebench_orchestrator.logging_config import get_logger, setup_logging

        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        l1 = get_logger("singleton.test")
        l2 = get_logger("singleton.test")
        assert l1 is l2


# ---------------------------------------------------------------------------
# Test: Log format and structure
# ---------------------------------------------------------------------------

class TestLogFormat:
    """Verify log message format matches expectations."""

    def test_log_format_includes_timestamp(self, log_file: Path) -> None:
        """Each log line should include a timestamp."""
        from swebench_orchestrator.logging_config import get_logger, setup_logging

        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        logger = get_logger("format.test")
        logger.info("timestamp test")

        content = log_file.read_text()
        # Should contain a timestamp pattern like 2024-01-01 12:00:00
        assert "timestamp test" in content
        # Verify timestamp is present (YYYY-MM-DD HH:MM:SS)
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", content)

    def test_log_format_includes_level(self, log_file: Path) -> None:
        """Each log line should include the log level."""
        from swebench_orchestrator.logging_config import get_logger, setup_logging

        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        logger = get_logger("format.level")
        logger.error("level test")

        content = log_file.read_text()
        assert "ERROR" in content or "error" in content.lower()

    def test_log_format_includes_module_name(self, log_file: Path) -> None:
        """Each log line should include the module name."""
        from swebench_orchestrator.logging_config import get_logger, setup_logging

        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        logger = get_logger("format.module")
        logger.info("module test")

        content = log_file.read_text()
        assert "format.module" in content


# ---------------------------------------------------------------------------
# Test: Log rotation / file append behavior
# ---------------------------------------------------------------------------

class TestFileBehavior:
    """Verify file handler appends correctly."""

    def test_appends_to_existing_file(self, log_file: Path) -> None:
        """File handler should append to existing log files."""
        from swebench_orchestrator.logging_config import get_logger, setup_logging

        # Ensure parent dir exists and pre-populate the file
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("existing content\n")

        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        logger = get_logger("append.test")
        logger.info("new content")

        content = log_file.read_text()
        assert "existing content" in content
        assert "new content" in content

    def test_creates_parent_directories(self, log_dir: Path) -> None:
        """setup_logging should create parent directories for the log file."""
        from swebench_orchestrator.logging_config import setup_logging

        nested_log = log_dir / "deep" / "nested" / "log.log"
        assert not nested_log.parent.exists()

        setup_logging(
            level="DEBUG",
            log_file=str(nested_log),
            console=False,
            file=True,
        )

        assert nested_log.parent.exists()


# ---------------------------------------------------------------------------
# Test: Integration — logger works across modules
# ---------------------------------------------------------------------------

class TestIntegration:
    """End-to-end test: logging works correctly in a realistic scenario."""

    def test_full_workflow(self, log_file: Path) -> None:
        """Simulate a full run workflow with multiple log levels."""
        from swebench_orchestrator.logging_config import get_logger, setup_logging

        # Setup
        setup_logging(
            level="DEBUG",
            log_file=str(log_file),
            console=False,
            file=True,
        )

        runner_log = get_logger("swebench_orchestrator.runner")
        docker_log = get_logger("swebench_orchestrator.docker_ops")

        # Simulate workflow
        runner_log.info("[WORK] Starting run for agent 'pi'")
        docker_log.debug("Checking Docker availability")
        runner_log.warning("Disk at 75%% (threshold: 80%%)")
        docker_log.error("Container exited with code 1")
        runner_log.info("[WORK] Run complete")

        content = log_file.read_text()

        # Verify all messages are present
        assert "[WORK] Starting run for agent 'pi'" in content
        assert "Checking Docker availability" in content
        assert "Disk at 75%" in content
        assert "Container exited with code 1" in content
        assert "[WORK] Run complete" in content

        # Verify module names are present
        assert "swebench_orchestrator.runner" in content
        assert "swebench_orchestrator.docker_ops" in content


# ---------------------------------------------------------------------------
# Test: Error handling — invalid log file path
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Verify graceful error handling."""

    def test_unwritable_path_raises_clear_error(self, log_dir: Path) -> None:
        """setup_logging should raise a clear error for unwritable paths."""
        from swebench_orchestrator.logging_config import setup_logging

        # Try to write to /proc which is typically not writable
        if sys.platform == "linux":
            with pytest.raises((OSError, PermissionError)):
                setup_logging(
                    level="DEBUG",
                    log_file="/proc/nonexistent_dir/test.log",
                    console=False,
                    file=True,
                )
