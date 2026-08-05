"""Logging configuration for the SWE-bench orchestrator.

Provides a centralized logging setup with both console (stderr) and file handlers,
replacing the crude ``exec > >(tee -a "$LOG_FILE") 2>&1`` pattern from run.sh.

Usage::

    from swebench_orchestrator.logging_config import setup_logging, get_logger

    setup_logging(level="INFO", log_file="/path/to/run.log", console=True, file=True)
    logger = get_logger(__name__)
    logger.info("Running agent pi against django__django-7530")

Log format::

    2024-01-15 10:30:45 [INFO] swebench_orchestrator.runner: [WORK] Starting run...

This matches the style used throughout the codebase and provides structured,
filterable output to both console and file simultaneously.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_LEVEL = "INFO"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(
    level: str = DEFAULT_LOG_LEVEL,
    log_file: Optional[str] = None,
    console: bool = True,
    file: bool = True,
) -> None:
    """Configure the root logger with console and/or file handlers.

    This function configures the *root* logger so that all child loggers
    (created via ``get_logger`` or ``logging.getLogger``) inherit the handlers.

    Args:
        level: Log level string (e.g., "DEBUG", "INFO", "WARNING", "ERROR").
        log_file: Path to the log file. If None, no file handler is added.
        console: Whether to add a stderr console handler.
        file: Whether to add a file handler (requires ``log_file``).

    Raises:
        OSError: If the log file cannot be created or written to.
        ValueError: If ``file=True`` but ``log_file`` is None.

    Example::

        setup_logging(level="DEBUG", log_file="workspace/run.log", console=True, file=True)
    """
    # Validate arguments
    if file and not log_file:
        raise ValueError("file=True requires log_file to be specified")

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(logging.getLevelName(level.upper()))

    formatter = logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT)

    # Console handler (stderr)
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.getLevelName(level.upper()))
        root.addHandler(console_handler)

    # File handler
    if file and log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            file_handler = logging.FileHandler(str(log_path), mode="a")
        except OSError as e:
            raise OSError(f"Cannot create log file at {log_path}: {e}") from e

        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.getLevelName(level.upper()))
        root.addHandler(file_handler)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a named logger that propagates to the root handlers.

    This is the preferred way to obtain loggers in the codebase. It returns
    a standard ``logging.Logger`` instance configured to propagate to the
    root logger's handlers (console + file).

    Args:
        name: Logger name, typically ``__name__`` from the calling module.
              If None, returns the root logger.

    Returns:
        A ``logging.Logger`` instance.

    Example::

        logger = get_logger(__name__)
        logger.info("Processing instance %s", instance_id)
    """
    return logging.getLogger(name or "")


# ---------------------------------------------------------------------------
# Convenience: CLI-style setup (mirrors cli.py setup_logging signature)
# ---------------------------------------------------------------------------

def setup_logging_from_cli(verbose: bool = False, log_file: Optional[str] = None) -> None:
    """Configure logging for CLI usage.

    This is a convenience wrapper that maps the ``--verbose`` flag to DEBUG level
    and enables both console and file handlers.

    Args:
        verbose: If True, set level to DEBUG; otherwise INFO.
        log_file: Path to log file (falls back to workspace/run.log if None).
    """
    level = "DEBUG" if verbose else "INFO"

    # Default log file location (same as Config.log_file)
    if log_file is None:
        from pathlib import Path
        try:
            from swebench_orchestrator.config import Config
            config = Config.from_env()
            log_file = str(config.log_file)
        except Exception:
            # Fallback: current directory
            log_file = "run.log"

    setup_logging(level=level, log_file=log_file, console=True, file=True)
