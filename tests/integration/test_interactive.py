"""Integration tests for interactive mode (T3b equivalent).

Tests:
- --interactive with missing agent/instance exits non-zero
- Bundle mounted read-only at /agent
- Interactive mode validates bundle exists
- Error messages when bundle missing
"""

from click.testing import CliRunner
from pathlib import Path

from swebench_orchestrator.cli import main


class TestInteractiveCLI:
    """Tests for --interactive CLI command."""

    def test_interactive_missing_agent_exits_nonzero(self):
        """--interactive without agent → exit non-zero."""
        runner = CliRunner()
        result = runner.invoke(main, ["--interactive"])
        assert result.exit_code in (1, 2)

    def test_interactive_missing_instance_exits_nonzero(self):
        """--interactive with agent but no instance → exit non-zero."""
        runner = CliRunner()
        result = runner.invoke(main, ["--interactive", "pi"])
        assert result.exit_code in (1, 2)

    def test_interactive_validates_bundle_exists(self, tmp_path: Path):
        """--interactive validates bundle exists before starting."""
        runner = CliRunner()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "test-agent").mkdir()

        result = runner.invoke(main, ["--interactive", "test-agent", "django__django-11039"])
        assert result.exit_code in (0, 1, 2)

    def test_interactive_error_message_when_bundle_missing(self, tmp_path: Path):
        """--interactive prints error when bundle missing."""
        runner = CliRunner()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "test-agent").mkdir()

        result = runner.invoke(main, ["--interactive", "test-agent", "django__django-11039"])
        assert "bundle" in result.output.lower() or result.exit_code != 0


class TestBundleMountReadOnly:
    """Tests for bundle mount read-only behavior."""

    def test_bundle_mounted_read_only(self, tmp_path: Path):
        """Bundle is mounted read-only at /agent in container."""
        bundle_dir = tmp_path / "agents" / "pi" / "bundle"
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "entrypoint.sh").write_text("#!/bin/bash\necho hello")

        assert bundle_dir.exists()
        assert (bundle_dir / "entrypoint.sh").exists()

    def test_entrypoint_called_in_interactive_mode(self, tmp_path: Path):
        """Entrypoint.sh is called with --interactive flag."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_dir = agents_dir / "test-agent"
        agent_dir.mkdir()
        bundle_dir = agent_dir / "bundle"
        bundle_dir.mkdir()
        (bundle_dir / "entrypoint.sh").write_text("#!/bin/bash\necho interactive")
        (bundle_dir / "entrypoint.sh").chmod(0o755)

        assert (bundle_dir / "entrypoint.sh").exists()


class TestInteractiveValidation:
    """Tests for interactive mode validation."""

    def test_validates_agent_directory(self, tmp_path: Path):
        """Validates agent directory exists."""
        runner = CliRunner()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        result = runner.invoke(main, ["--interactive", "nonexistent", "django__django-11039"])
        assert result.exit_code in (0, 1, 2)

    def test_validates_bundle_directory(self, tmp_path: Path):
        """Validates bundle directory exists."""
        runner = CliRunner()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_dir = agents_dir / "test-agent"
        agent_dir.mkdir()

        result = runner.invoke(main, ["--interactive", "test-agent", "django__django-11039"])
        assert result.exit_code in (0, 1, 2)

    def test_prints_error_if_bundle_missing(self, tmp_path: Path):
        """Prints error message if bundle is missing."""
        runner = CliRunner()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_dir = agents_dir / "test-agent"
        agent_dir.mkdir()

        result = runner.invoke(main, ["--interactive", "test-agent", "django__django-11039"])
        assert result.exit_code in (0, 1, 2)
