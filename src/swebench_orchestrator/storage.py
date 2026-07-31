"""Storage management — disk usage checking and Docker image pruning."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def get_disk_usage_pct(path: str | Path) -> float:
    """Get disk usage percentage for the filesystem containing path.

    Returns 0.0 on failure.
    """
    try:
        result = subprocess.run(
            ["df", "--output=pcent", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return 0.0
        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return 0.0
        # Skip header line, parse percentage
        pct_str = lines[-1].strip().replace("%", "").strip()
        return float(pct_str)
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        return 0.0


def check_storage(
    path: str | Path,
    threshold_pct: float = 80.0,
) -> dict[str, Any]:
    """Check disk usage and return status.

    Args:
        path: Path to check disk usage for.
        threshold_pct: Warning threshold percentage.

    Returns:
        Dict with usage_pct, threshold_pct, is_warning, is_critical.
    """
    usage_pct = get_disk_usage_pct(path)
    is_warning = usage_pct >= threshold_pct
    is_critical = usage_pct >= 90.0
    return {
        "usage_pct": usage_pct,
        "threshold_pct": threshold_pct,
        "is_warning": is_warning,
        "is_critical": is_critical,
    }


def get_swebench_images() -> list[dict[str, str]]:
    """List all swebench Docker images.

    Returns:
        List of dicts with 'name' and 'id' keys.
    """
    try:
        result = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}} {{.ID}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []

        images = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("swebench/"):
                images.append({"name": parts[0], "id": parts[1]})
        return images
    except (subprocess.TimeoutExpired, IndexError):
        return []


def prune_docker_images() -> dict[str, int]:
    """Remove all swebench Docker images.

    Returns:
        Dict with 'removed' count.
    """
    images = get_swebench_images()
    removed = 0
    for img in images:
        try:
            result = subprocess.run(
                ["docker", "rmi", "--force", img["name"]],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                removed += 1
                logger.info("Removed image: %s", img["name"])
        except subprocess.TimeoutExpired:
            logger.error("Timeout removing image: %s", img["name"])
    return {"removed": removed}


def cleanup_docker_containers() -> dict[str, int]:
    """Remove all swe_* Docker containers and orphaned network endpoints.

    Returns:
        Dict with 'containers_removed' and 'endpoints_released' counts.
    """
    containers_removed = 0
    endpoints_released = 0

    # Remove swe_* containers
    try:
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=^/swe_"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            container_ids = result.stdout.strip().split("\n")
            if container_ids:
                subprocess.run(
                    ["docker", "rm", "-f"] + container_ids,
                    capture_output=True,
                    timeout=120,
                )
                containers_removed = len(container_ids)
    except subprocess.TimeoutExpired:
        logger.error("Timeout removing containers")

    # Release orphaned bridge network endpoints
    try:
        result = subprocess.run(
            ["docker", "network", "inspect", "bridge",
             "-f", '{{range .Containers}}{{.Name}}{{println}}{{end}}'],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for name in result.stdout.strip().split("\n"):
                name = name.strip()
                if name.startswith("swe_"):
                    subprocess.run(
                        ["docker", "network", "disconnect", "-f", "bridge", name],
                        capture_output=True,
                        timeout=10,
                    )
                    endpoints_released += 1
    except subprocess.TimeoutExpired:
        logger.error("Timeout releasing network endpoints")

    return {
        "containers_removed": containers_removed,
        "endpoints_released": endpoints_released,
    }


def cleanup_stopped_containers() -> int:
    """Remove all stopped (exited) Docker containers.

    Returns:
        Number of containers removed.
    """
    try:
        result = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "status=exited"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0

        container_ids = result.stdout.strip().split("\n")
        if not container_ids:
            return 0

        subprocess.run(
            ["docker", "rm"] + container_ids,
            capture_output=True,
            timeout=120,
        )
        return len(container_ids)
    except (subprocess.TimeoutExpired, IndexError):
        return 0
