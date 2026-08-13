"""Integration tests for bundle operations with real filesystem."""

from pathlib import Path

from swebench_orchestrator.bundles import BundleBuilder, discover_agents


class TestBundleBuilderIntegration:
    """Integration tests for BundleBuilder with real subprocess calls."""

    def test_build_agent_creates_bundle(self, test_workspace):
        """Building an agent creates the bundle directory with expected files."""
        agents_dir = test_workspace / "harnesses"
        agent_dir = agents_dir / "test-agent"
        agent_dir.mkdir(parents=True)

        # Create build script that makes a real bundle
        build_script = agent_dir / "build_bundle.sh"
        build_script.write_text("""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR/bin" "$BUNDLE_DIR/lib"
echo '#!/bin/bash' > "$BUNDLE_DIR/bin/node"
echo 'console.log("mock node")' >> "$BUNDLE_DIR/bin/node"
chmod +x "$BUNDLE_DIR/bin/node"
echo "bundle content" > "$BUNDLE_DIR/lib/bundle.js"
""")
        build_script.chmod(0o755)

        builder = BundleBuilder(agents_dir)
        result = builder.build_agent("test-agent")

        assert result is True
        bundle_dir = agent_dir / "bundle"
        assert bundle_dir.is_dir()
        assert (bundle_dir / "bin" / "node").exists()
        assert (bundle_dir / "lib" / "bundle.js").exists()

    def test_build_agent_with_real_node(self, test_workspace):
        """Build script that creates a proper node binary."""
        agents_dir = test_workspace / "harnesses"
        agent_dir = agents_dir / "pi"
        agent_dir.mkdir(parents=True)

        build_script = agent_dir / "build_bundle.sh"
        build_script.write_text("""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR/bin"
# Create a proper node binary
cat > "$BUNDLE_DIR/bin/node" << 'NODEOF'
#!/usr/bin/env python3
import sys
print("mock node", file=sys.stderr)
sys.exit(0)
NODEOF
chmod +x "$BUNDLE_DIR/bin/node"
# Create entrypoint
cat > "$BUNDLE_DIR/entrypoint.sh" << 'EPEOF'
#!/bin/bash
echo "Running agent..."
exit 0
EPEOF
chmod +x "$BUNDLE_DIR/entrypoint.sh"
""")
        build_script.chmod(0o755)

        builder = BundleBuilder(agents_dir)
        result = builder.build_agent("pi")

        assert result is True
        bundle = agent_dir / "bundle"
        assert (bundle / "bin" / "node").exists()
        assert (bundle / "entrypoint.sh").exists()
        # Verify node is executable
        assert (bundle / "bin" / "node").stat().st_mode & 0o111

    def test_build_all_agents(self, test_workspace):
        """Building all agents builds each one."""
        agents_dir = test_workspace / "harnesses"

        for name in ["pi", "codex"]:
            agent_dir = agents_dir / name
            agent_dir.mkdir(parents=True)
            build_script = agent_dir / "build_bundle.sh"
            build_script.write_text(f"""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${{1:-./bundle}}"
mkdir -p "$BUNDLE_DIR"
echo "Built {name} bundle" > "$BUNDLE_DIR/built.txt"
""")
            build_script.chmod(0o755)

        builder = BundleBuilder(agents_dir)
        results = builder.build_all()

        assert len(results) == 2
        assert all(r is True for r in results)

        # Verify both bundles exist
        assert (agents_dir / "pi" / "bundle" / "built.txt").exists()
        assert (agents_dir / "codex" / "bundle" / "built.txt").exists()

    def test_rebuild_removes_existing_bundle(self, test_workspace):
        """Rebuild removes existing bundle before rebuilding."""
        agents_dir = test_workspace / "harnesses"
        agent_dir = agents_dir / "test-agent"
        agent_dir.mkdir(parents=True)

        build_script = agent_dir / "build_bundle.sh"
        build_script.write_text("""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "first build" > "$BUNDLE_DIR/version.txt"
""")
        build_script.chmod(0o755)

        builder = BundleBuilder(agents_dir)

        # First build
        builder.build_agent("test-agent")
        assert (agent_dir / "bundle" / "version.txt").exists()
        assert (agent_dir / "bundle" / "version.txt").read_text() == "first build\n"

        # Modify build script
        build_script.write_text("""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR"
echo "second build" > "$BUNDLE_DIR/version.txt"
""")

        # Rebuild
        builder.rebuild_agent("test-agent")
        assert (agent_dir / "bundle" / "version.txt").read_text() == "second build\n"

    def test_list_bundles(self, test_workspace):
        """List bundles returns only built agents with sizes."""
        agents_dir = test_workspace / "harnesses"

        # Build one agent
        pi_dir = agents_dir / "pi"
        pi_dir.mkdir(parents=True)
        (pi_dir / "build_bundle.sh").write_text("#!/bin/bash\nmkdir -p ${1:-./bundle}\necho x > ${1:-./bundle}/file.txt")
        (pi_dir / "build_bundle.sh").chmod(0o755)

        builder = BundleBuilder(agents_dir)
        builder.build_agent("pi")

        bundles = builder.list_bundles()
        assert len(bundles) == 1
        assert bundles[0][0] == "pi"
        # Size should be non-zero
        assert "B" in bundles[0][1] or "KiB" in bundles[0][1]

    def test_build_nonexistent_agent_raises(self, test_workspace):
        """Building non-existent agent raises ValueError."""
        builder = BundleBuilder(test_workspace / "harnesses")
        try:
            builder.build_agent("nonexistent")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found" in str(e)

    def test_build_skips_agents_without_script(self, test_workspace):
        """Agents without build_bundle.sh are skipped."""
        agents_dir = test_workspace / "harnesses"
        agent_dir = agents_dir / "noscript"
        agent_dir.mkdir(parents=True)

        builder = BundleBuilder(agents_dir)
        result = builder.build_agent("noscript")

        assert result is False  # No build script


class TestDiscoverAgentsIntegration:
    """Integration tests for discover_agents."""

    def test_discovers_all_non_base_agents(self, test_workspace):
        """Discovers all agent directories except 'base'."""
        agents_dir = test_workspace / "harnesses"
        (agents_dir / "pi").mkdir()
        (agents_dir / "codex").mkdir()
        (agents_dir / "base").mkdir()
        (agents_dir / "test-agent").mkdir()

        agents = list(discover_agents(agents_dir))
        names = [a.name for a in agents]

        assert "pi" in names
        assert "codex" in names
        assert "test-agent" in names
        assert "base" not in names

    def test_returns_sorted(self, test_workspace):
        """Agents are returned in sorted order."""
        agents_dir = test_workspace / "harnesses"
        for name in ["zebra", "alpha", "middle"]:
            (agents_dir / name).mkdir()

        agents = list(discover_agents(agents_dir))
        names = [a.name for a in agents]

        assert names == ["alpha", "middle", "zebra"]

    def test_empty_when_no_agents(self, test_workspace):
        """Returns empty when no agent directories exist."""
        agents = list(discover_agents(test_workspace / "harnesses"))
        assert len(agents) == 0

    def test_empty_when_base_only(self, test_workspace):
        """Returns empty when only 'base' directory exists."""
        (test_workspace / "harnesses" / "base").mkdir(parents=True)
        agents = list(discover_agents(test_workspace / "harnesses"))
        assert len(agents) == 0
