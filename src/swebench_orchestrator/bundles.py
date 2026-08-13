"""Agent bundle operations — discover, build, and manage agent bundles."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


class AgentBundle:
    """Represents an agent's bundle directory.

    An agent is a directory under harnesses/<name>/ containing:
    - build_bundle.sh  — script to create the bundle
    - bundle/          — the built bundle (created by build_bundle.sh)
    """

    def __init__(self, agent_dir: Path) -> None:
        self.agent_dir = agent_dir.resolve()

    @property
    def name(self) -> str:
        return self.agent_dir.name

    @property
    def bundle_dir(self) -> Path:
        return self.agent_dir / "bundle"

    @property
    def build_script(self) -> Path:
        return self.agent_dir / "build_bundle.sh"

    @property
    def exists(self) -> bool:
        return self.bundle_dir.is_dir() and any(self.bundle_dir.iterdir())

    @property
    def has_build_script(self) -> bool:
        return self.build_script.is_file()

    @property
    def size_human(self) -> str:
        """Return human-readable bundle size."""
        if not self.exists:
            return "N/A"
        total = sum(f.stat().st_size for f in self.bundle_dir.rglob("*") if f.is_file())
        return _human_size(total)


def _human_size(bytes_val: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(bytes_val) < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TiB"


def discover_agents(agents_dir: Path) -> Iterator[AgentBundle]:
    """Discover all agent bundles under agents_dir.

    Excludes the 'base' agent directory.
    """
    if not agents_dir.is_dir():
        return
    for entry in sorted(agents_dir.iterdir()):
        if entry.is_dir() and entry.name != "base":
            yield AgentBundle(entry)


class BundleBuilder:
    """Builds and manages agent bundles."""

    def __init__(self, agents_dir: Path) -> None:
        self.agents_dir = Path(agents_dir).resolve()

    @property
    def available_agents(self) -> list[str]:
        """List available agent names (excluding 'base')."""
        return [a.name for a in discover_agents(self.agents_dir)]

    def build_agent(self, agent_name: str) -> bool:
        """Build a single agent bundle.

        Args:
            agent_name: Name of the agent (directory under harnesses/).

        Returns:
            True if build succeeded, False if skipped or failed.

        Raises:
            ValueError: If agent directory not found.
        """
        agent_dir = self.agents_dir / agent_name
        if not agent_dir.is_dir():
            raise ValueError(f"Agent '{agent_name}' not found in {self.agents_dir}")

        bundle = AgentBundle(agent_dir)
        if not bundle.has_build_script:
            logger.warning("No build_bundle.sh for agent '%s', skipping.", agent_name)
            return False

        logger.info("Building %s agent bundle...", agent_name)
        result = subprocess.run(
            ["bash", str(bundle.build_script), str(bundle.bundle_dir)],
            cwd=str(agent_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("Build failed for %s: %s", agent_name, result.stderr)
            return False

        logger.info("Built %s bundle at %s", agent_name, bundle.bundle_dir)
        return True

    def rebuild_agent(self, agent_name: str) -> bool:
        """Rebuild an agent bundle from scratch (removes existing bundle first).

        Args:
            agent_name: Name of the agent.

        Returns:
            True if rebuild succeeded.
        """
        agent_dir = self.agents_dir / agent_name
        bundle = AgentBundle(agent_dir)
        if bundle.exists:
            import shutil
            shutil.rmtree(bundle.bundle_dir)
        return self.build_agent(agent_name)

    def build_all(self) -> list[bool]:
        """Build all agent bundles.

        Returns:
            List of success booleans, one per agent.
        """
        results = []
        for bundle in discover_agents(self.agents_dir):
            if not bundle.has_build_script:
                logger.warning("No build_bundle.sh for agent '%s', skipping.", bundle.name)
                results.append(False)
                continue
            try:
                success = self.build_agent(bundle.name)
                results.append(success)
            except ValueError:
                results.append(False)
        return results

    def rebuild_all(self) -> list[bool]:
        """Rebuild all agent bundles from scratch.

        Returns:
            List of success booleans, one per agent.
        """
        results = []
        for bundle in discover_agents(self.agents_dir):
            if not bundle.has_build_script:
                logger.warning("No build_bundle.sh for agent '%s', skipping.", bundle.name)
                results.append(False)
                continue
            try:
                success = self.rebuild_agent(bundle.name)
                results.append(success)
            except ValueError:
                results.append(False)
        return results

    def list_bundles(self) -> list[tuple[str, str]]:
        """List all built bundles with their sizes.

        Returns:
            List of (agent_name, size_human) tuples.
        """
        result = []
        for bundle in discover_agents(self.agents_dir):
            if bundle.exists:
                result.append((bundle.name, bundle.size_human))
        return result
