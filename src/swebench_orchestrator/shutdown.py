"""Graceful shutdown and signal handling for the SWE-bench orchestrator.

Provides signal handlers for SIGINT (Ctrl+C) and SIGTERM that:
- Stop all running swe_* containers
- Release orphaned network bridge endpoints
- Log a clean shutdown message
- Exit with code 130 (standard for SIGINT)

This mirrors the bash trap behavior from run.sh:
    trap on_interrupt INT TERM
    trap stop_running_containers EXIT
"""

from __future__ import annotations

import logging
import signal
import sys
from typing import Any, Optional

from swebench_orchestrator.docker_ops import DockerOps

logger = logging.getLogger(__name__)

# Guard against double-cleanup (mirrors bash STOPPED flag)
_shutdown_complete = False


def stop_running_containers(
    docker_ops: Optional[DockerOps] = None,
) -> dict[str, int]:
    """Stop all running swe_* containers and release their network endpoints.

    This is the core cleanup function called by signal handlers.
    It is idempotent — safe to call multiple times.

    Args:
        docker_ops: DockerOps instance (creates one if not provided).

    Returns:
        Dict with 'containers_stopped', 'endpoints_released', and 'errors' counts.
    """
    global _shutdown_complete

    if _shutdown_complete:
        return {"containers_stopped": 0, "endpoints_released": 0, "errors": 0}

    if docker_ops is None:
        docker_ops = DockerOps()

    containers_stopped = 0
    endpoints_released = 0
    errors = 0

    # List all running containers with swe_ prefix
    container_names = docker_ops.list_running_containers(prefix="swe_")

    for name in container_names:
        # Stop the container
        if docker_ops.stop_container(name):
            containers_stopped += 1
            # Release network endpoint after stopping
            if docker_ops.disconnect_endpoint(name):
                endpoints_released += 1
        else:
            errors += 1

    _shutdown_complete = True

    return {
        "containers_stopped": containers_stopped,
        "endpoints_released": endpoints_released,
        "errors": errors,
    }


def shutdown_handler(
    signum: int,
    frame: Any,
    docker_ops: Optional[DockerOps] = None,
) -> None:
    """Signal handler for SIGINT and SIGTERM.

    Called when the user presses Ctrl+C or the process receives SIGTERM.
    Stops all running containers, releases endpoints, logs shutdown,
    and exits with code 130 (standard for SIGINT).

    Args:
        signum: The signal number received.
        frame: The current stack frame (unused but required by signal API).
        docker_ops: DockerOps instance (creates one if not provided).
    """
    global _shutdown_complete

    # Prevent double-handling
    if _shutdown_complete:
        sys.exit(130)

    sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
    print(
        f"\n{'='*78}",
        file=sys.stderr,
    )
    print(
        f"  {sig_name} received — shutting down...",
        file=sys.stderr,
    )
    print(
        f"{'='*78}",
        file=sys.stderr,
    )

    result = stop_running_containers(docker_ops=docker_ops)

    if result["containers_stopped"] > 0:
        print(
            f"  Stopped {result['containers_stopped']} container(s).",
            file=sys.stderr,
        )
    if result["endpoints_released"] > 0:
        print(
            f"  Released {result['endpoints_released']} network endpoint(s).",
            file=sys.stderr,
        )
    if result["errors"] > 0:
        print(
            f"  {result['errors']} error(s) during cleanup.",
            file=sys.stderr,
        )

    print(
        "  Cleanup complete. Goodbye.",
        file=sys.stderr,
    )
    print(
        f"{'='*78}",
        file=sys.stderr,
    )

    sys.exit(130)


def setup_signal_handlers() -> None:
    """Install signal handlers for SIGINT and SIGTERM.

    Should be called at CLI entry point (in main()) before any
    long-running operations begin.

    Registers:
    - SIGINT handler → shutdown_handler (exits 130)
    - SIGTERM handler → shutdown_handler (exits 130)
    """
    global _shutdown_complete
    _shutdown_complete = False

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
