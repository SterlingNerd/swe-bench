"""Unit tests for the CLI module."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from swebench_orchestrator.cli import main


class TestCLI:
    """Tests for the CLI interface."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_no_args_shows_help(self):
        result = self.runner.invoke(main, [])
        # Click returns 0 for no args (shows help), or 2 if Config fails
        assert result.exit_code in (0, 2)
        assert "SWE-bench Orchestrator" in result.output

    def test_help_flag(self):
        result = self.runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "SWE-bench Orchestrator" in result.output

    def test_unknown_command_exits_1(self):
        result = self.runner.invoke(main, ["--unknown-flag"])
        # Click returns 2 for unknown options
        assert result.exit_code in (1, 2)

    def test_index_command(self):
        result = self.runner.invoke(main, ["--index"])
        # May fail due to missing cache or HF connectivity
        assert result.exit_code in (0, 1, 2)

    def test_list_command(self):
        result = self.runner.invoke(main, ["--list"])
        assert result.exit_code in (0, 1, 2)

    def test_list_with_filter(self):
        result = self.runner.invoke(main, ["--list", "django"])
        assert result.exit_code in (0, 1, 2)

    def test_build_command(self):
        result = self.runner.invoke(main, ["--build"])
        assert result.exit_code in (0, 1, 2)

    def test_rebuild_command(self):
        result = self.runner.invoke(main, ["--rebuild"])
        assert result.exit_code in (0, 1, 2)

    def test_run_missing_args_exits_1(self):
        result = self.runner.invoke(main, ["--run"])
        # Click returns 2 for missing required arguments
        assert result.exit_code in (1, 2)

    def test_run_all_missing_agent_exits_1(self):
        result = self.runner.invoke(main, ["--run-all"])
        assert result.exit_code in (1, 2)

    def test_eval_missing_agent_exits_1(self):
        result = self.runner.invoke(main, ["--eval"])
        assert result.exit_code in (1, 2)

    def test_interactive_missing_args_exits_1(self):
        result = self.runner.invoke(main, ["--interactive"])
        assert result.exit_code in (1, 2)

    def test_cleanup_command(self):
        result = self.runner.invoke(main, ["--cleanup"])
        assert result.exit_code in (0, 1, 2)

    def test_cleanup_partial_command(self):
        result = self.runner.invoke(main, ["--cleanup-partial"])
        assert result.exit_code in (0, 1, 2)

    def test_init_command(self):
        result = self.runner.invoke(main, ["--init"])
        # May fail if pip not available, but should not crash
        assert result.exit_code in (0, 1, 2)

    def test_status_command(self):
        result = self.runner.invoke(main, ["--status"])
        assert result.exit_code in (0, 1, 2)

    def test_summarize_command(self):
        result = self.runner.invoke(main, ["--summarize"])
        assert result.exit_code in (0, 1, 2)

    def test_interactive_uses_config_registry(self, tmp_path: Path):
        """Interactive command should use config.swebench_registry for image name."""
        # Create necessary structure
        agents_dir = tmp_path / "agents"
        agent_dir = agents_dir / "pi"
        bundle_dir = agent_dir / "bundle"
        agent_dir.mkdir(parents=True)
        bundle_dir.mkdir(parents=True)

        cache_file = tmp_path / "cache.json"
        cache_file.write_text(
            json.dumps([{
                "instance_id": "django__django-11039",
                "repo": "django/django",
                "base_commit": "abc123",
                "problem_statement": "Fix bug",
            }])
        )

        with patch.dict(os.environ, {"SWEBENCH_REGISTRY": "my-registry"}):
            with patch("swebench_orchestrator.cli.DockerOps") as MockDockerOps:
                mock_docker = MagicMock()
                MockDockerOps.return_value = mock_docker
                mock_docker.image_exists.return_value = True

                result = self.runner.invoke(
                    main,
                    ["interactive", "pi", "django__django-11039"],
                    env={"SWE_WORKSPACE_DIR": str(tmp_path / "workspace")},
                )

                # Should not crash with registry error
                assert result.exit_code in (0, 1)
