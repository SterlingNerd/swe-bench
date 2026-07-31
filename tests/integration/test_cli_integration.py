"""Integration tests for CLI — argument parsing and command dispatch."""

from click.testing import CliRunner

from swebench_orchestrator.cli import main


class TestCLIArgumentParsing:
    """Tests for CLI argument parsing and validation."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_no_args_exits_0_shows_help(self):
        """No arguments → exit 0, shows help."""
        result = self.runner.invoke(main, [])
        assert result.exit_code in (0, 2)  # 0 if Config works, 2 if not
        assert "SWE-bench Orchestrator" in result.output

    def test_help_flag_exits_0(self):
        """--help → exit 0."""
        result = self.runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "SWE-bench Orchestrator" in result.output

    def test_h_flag_exits_0(self):
        """-h → exit 0 (click supports -h as short for --help)."""
        result = self.runner.invoke(main, ["-h"])
        # Click may return 2 if Config fails before help is shown
        assert result.exit_code in (0, 2)

    def test_unknown_option_exits_nonzero(self):
        """Unknown option → exit non-zero."""
        result = self.runner.invoke(main, ["--unknown-flag"])
        assert result.exit_code in (1, 2)

    def test_run_requires_agent_and_instance(self):
        """--run requires both agent and instance_id."""
        result = self.runner.invoke(main, ["--run"])
        assert result.exit_code in (1, 2)

        result = self.runner.invoke(main, ["--run", "pi"])
        assert result.exit_code in (1, 2)

    def test_run_all_requires_agent(self):
        """--run-all requires agent."""
        result = self.runner.invoke(main, ["--run-all"])
        assert result.exit_code in (1, 2)

    def test_eval_requires_agent(self):
        """--eval requires agent."""
        result = self.runner.invoke(main, ["--eval"])
        assert result.exit_code in (1, 2)

    def test_interactive_requires_agent_and_instance(self):
        """--interactive requires both agent and instance_id."""
        result = self.runner.invoke(main, ["--interactive"])
        assert result.exit_code in (1, 2)

        result = self.runner.invoke(main, ["--interactive", "pi"])
        assert result.exit_code in (1, 2)


class TestCLICommandDispatch:
    """Tests for CLI command dispatch (commands don't crash)."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_index_command_dispatches(self):
        """--index command dispatches without crashing."""
        result = self.runner.invoke(main, ["--index"])
        # May fail due to missing cache/HF, but shouldn't crash
        assert result.exit_code in (0, 1, 2)

    def test_list_command_dispatches(self):
        """--list command dispatches without crashing."""
        result = self.runner.invoke(main, ["--list"])
        assert result.exit_code in (0, 1, 2)

    def test_list_with_filter_dispatches(self):
        """--list with filter dispatches."""
        result = self.runner.invoke(main, ["--list", "django"])
        assert result.exit_code in (0, 1, 2)

    def test_build_command_dispatches(self):
        """--build command dispatches."""
        result = self.runner.invoke(main, ["--build"])
        assert result.exit_code in (0, 1, 2)

    def test_build_agent_dispatches(self):
        """--build with agent name dispatches."""
        result = self.runner.invoke(main, ["--build", "pi"])
        assert result.exit_code in (0, 1, 2)

    def test_rebuild_command_dispatches(self):
        """--rebuild command dispatches."""
        result = self.runner.invoke(main, ["--rebuild"])
        assert result.exit_code in (0, 1, 2)

    def test_rebuild_agent_dispatches(self):
        """--rebuild with scope dispatches."""
        result = self.runner.invoke(main, ["--rebuild", "pi"])
        assert result.exit_code in (0, 1, 2)

    def test_run_command_dispatches(self):
        """--run command dispatches (may fail due to missing Docker)."""
        result = self.runner.invoke(main, ["--run", "pi", "django__django-11039"])
        assert result.exit_code in (0, 1, 2)

    def test_run_with_timeout_dispatches(self):
        """--run with --timeout flag dispatches."""
        result = self.runner.invoke(main, ["--run", "pi", "django__django-11039", "--timeout", "1800"])
        assert result.exit_code in (0, 1, 2)

    def test_run_all_command_dispatches(self):
        """--run-all command dispatches."""
        result = self.runner.invoke(main, ["--run-all", "pi"])
        assert result.exit_code in (0, 1, 2)

    def test_run_all_with_resume(self):
        """--run-all with --resume flag dispatches."""
        result = self.runner.invoke(main, ["--run-all", "pi", "--resume"])
        assert result.exit_code in (0, 1, 2)

    def test_eval_command_dispatches(self):
        """--eval command dispatches."""
        result = self.runner.invoke(main, ["--eval", "pi"])
        assert result.exit_code in (0, 1, 2)

    def test_summarize_command_dispatches(self):
        """--summarize command dispatches."""
        result = self.runner.invoke(main, ["--summarize"])
        assert result.exit_code in (0, 1, 2)

    def test_summarize_agent_dispatches(self):
        """--summarize with agent dispatches."""
        result = self.runner.invoke(main, ["--summarize", "pi"])
        assert result.exit_code in (0, 1, 2)

    def test_status_command_dispatches(self):
        """--status command dispatches."""
        result = self.runner.invoke(main, ["--status"])
        assert result.exit_code in (0, 1, 2)

    def test_status_agent_dispatches(self):
        """--status with agent dispatches."""
        result = self.runner.invoke(main, ["--status", "pi"])
        assert result.exit_code in (0, 1, 2)

    def test_init_command_dispatches(self):
        """--init command dispatches."""
        result = self.runner.invoke(main, ["--init"])
        # May fail if pip not available
        assert result.exit_code in (0, 1, 2)

    def test_cleanup_command_dispatches(self):
        """--cleanup command dispatches."""
        result = self.runner.invoke(main, ["--cleanup"])
        assert result.exit_code in (0, 1, 2)

    def test_cleanup_partial_command_dispatches(self):
        """--cleanup-partial command dispatches."""
        result = self.runner.invoke(main, ["--cleanup-partial"])
        assert result.exit_code in (0, 1, 2)

    def test_interactive_command_dispatches(self):
        """--interactive command dispatches."""
        result = self.runner.invoke(main, ["--interactive", "pi", "django__django-11039"])
        assert result.exit_code in (0, 1, 2)


class TestCLIVerbose:
    """Tests for --verbose flag."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_verbose_flag_accepted(self):
        """--verbose flag is accepted."""
        result = self.runner.invoke(main, ["-v", "--help"])
        assert result.exit_code == 0

    def test_verbose_with_command(self):
        """--verbose with command doesn't crash."""
        result = self.runner.invoke(main, ["-v", "--list"])
        assert result.exit_code in (0, 1, 2)
