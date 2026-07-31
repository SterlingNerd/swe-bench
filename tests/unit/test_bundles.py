"""Unit tests for agent bundle operations."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from swebench_orchestrator.bundles import (
    AgentBundle,
    BundleBuilder,
    discover_agents,
)


class TestAgentBundle:
    """Tests for the AgentBundle class."""

    def test_bundle_exists(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "pi"
        bundle_dir = agent_dir / "bundle"
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "entrypoint.sh").write_text("#!/bin/bash\necho hello")

        bundle = AgentBundle(agent_dir)
        assert bundle.exists is True

    def test_bundle_missing(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "pi"
        agent_dir.mkdir(parents=True)

        bundle = AgentBundle(agent_dir)
        assert bundle.exists is False

    def test_build_script_exists(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "pi"
        agent_dir.mkdir(parents=True)
        (agent_dir / "build_bundle.sh").write_text("#!/bin/bash\necho building")

        bundle = AgentBundle(agent_dir)
        assert bundle.has_build_script is True

    def test_build_script_missing(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "pi"
        agent_dir.mkdir(parents=True)

        bundle = AgentBundle(agent_dir)
        assert bundle.has_build_script is False

    def test_name(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "codex"
        agent_dir.mkdir(parents=True)

        bundle = AgentBundle(agent_dir)
        assert bundle.name == "codex"

    def test_size(self, tmp_path: Path):
        agent_dir = tmp_path / "agents" / "pi"
        bundle_dir = agent_dir / "bundle"
        bundle_dir.mkdir(parents=True)
        # Create some files
        (bundle_dir / "file1.txt").write_text("x" * 1024)
        (bundle_dir / "file2.txt").write_text("y" * 2048)

        bundle = AgentBundle(agent_dir)
        size_str = bundle.size_human
        assert "KiB" in size_str or "1.5" in size_str or "2.0" in size_str


class TestDiscoverAgents:
    """Tests for discover_agents function."""

    def test_discovers_all_agents(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        (agents_dir / "pi").mkdir(parents=True)
        (agents_dir / "codex").mkdir(parents=True)
        (agents_dir / "base").mkdir(parents=True)  # Should be excluded

        agents = list(discover_agents(agents_dir))
        names = [a.name for a in agents]
        assert "pi" in names
        assert "codex" in names
        assert "base" not in names

    def test_discovers_no_agents(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        agents = list(discover_agents(agents_dir))
        assert len(agents) == 0

    def test_excludes_base(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        (agents_dir / "base").mkdir(parents=True)
        (agents_dir / "pi").mkdir(parents=True)

        agents = list(discover_agents(agents_dir))
        names = [a.name for a in agents]
        assert "base" not in names


class TestBundleBuilder:
    """Tests for the BundleBuilder class."""

    def test_build_single_agent(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        agent_dir.mkdir(parents=True)

        # Create build script
        build_script = agent_dir / "build_bundle.sh"
        build_script.write_text("""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR/bin"
echo '#!/bin/bash' > "$BUNDLE_DIR/bin/node"
echo 'echo mock' >> "$BUNDLE_DIR/bin/node"
chmod +x "$BUNDLE_DIR/bin/node"
echo "Built bundle at $BUNDLE_DIR"
""")
        build_script.chmod(0o755)

        builder = BundleBuilder(agents_dir)
        result = builder.build_agent("pi")
        assert result is True
        bundle = AgentBundle(agent_dir)
        assert bundle.exists is True

    def test_build_nonexistent_agent(self, tmp_path: Path):
        builder = BundleBuilder(tmp_path / "agents")
        with pytest.raises(ValueError, match="not found"):
            builder.build_agent("nonexistent")

    def test_build_agent_without_script(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        agent_dir.mkdir(parents=True)

        builder = BundleBuilder(agents_dir)
        result = builder.build_agent("pi")
        assert result is False  # No build script, returns False

    def test_build_all_agents(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        for name in ["pi", "codex"]:
            agent_dir = agents_dir / name
            agent_dir.mkdir(parents=True)
            build_script = agent_dir / "build_bundle.sh"
            build_script.write_text(f"""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${{1:-./bundle}}"
mkdir -p "$BUNDLE_DIR"
echo "Built {name} bundle at $BUNDLE_DIR"
""")
            build_script.chmod(0o755)

        builder = BundleBuilder(agents_dir)
        results = builder.build_all()
        assert len(results) == 2
        assert all(r is True for r in results)

    def test_build_skips_base(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        (agents_dir / "base").mkdir(parents=True)
        (agents_dir / "pi").mkdir(parents=True)
        build_script = agents_dir / "pi" / "build_bundle.sh"
        build_script.write_text("#!/bin/bash\nmkdir -p ${1:-./bundle}")
        build_script.chmod(0o755)

        builder = BundleBuilder(agents_dir)
        results = builder.build_all()
        assert len(results) == 1  # Only pi, not base

    def test_rebuild_forces_rebuild(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        agent_dir.mkdir(parents=True)

        build_script = agent_dir / "build_bundle.sh"
        build_script.write_text("""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Built at $BUNDLE_DIR" > "$BUNDLE_DIR/built.txt"
""")
        build_script.chmod(0o755)

        builder = BundleBuilder(agents_dir)
        # First build
        result1 = builder.build_agent("pi")
        assert result1 is True

        # Modify the build script to change output
        build_script.write_text("""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "Rebuilt at $BUNDLE_DIR" > "$BUNDLE_DIR/built.txt"
""")

        # Rebuild
        result2 = builder.rebuild_agent("pi")
        assert result2 is True

    def test_list_available_agents(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        (agents_dir / "pi").mkdir(parents=True)
        (agents_dir / "codex").mkdir(parents=True)
        (agents_dir / "base").mkdir()

        builder = BundleBuilder(agents_dir)
        available = builder.available_agents
        assert "pi" in available
        assert "codex" in available
        assert "base" not in available
