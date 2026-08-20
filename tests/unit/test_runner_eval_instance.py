"""Unit tests for Runner.run_eval_instance and find_harness_report_for_instance."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.config import Config
from swebench_orchestrator.runner import (
    Runner,
    find_harness_report_for_instance,
)


@pytest.fixture()
def test_workspace(tmp_path: Path) -> Path:
    """Create a clean test workspace with agents and outputs directories."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "harnesses").mkdir()
    (workspace / "outputs").mkdir()
    (workspace / "runs").mkdir()
    return workspace


@pytest.fixture()
def cache_file(test_workspace: Path) -> Path:
    """Create a dataset cache file with test instances."""
    cache = test_workspace / "cache.json"
    data = [
        {
            "instance_id": "django__django-11039",
            "repo": "django/django",
            "version": "4.2",
            "testbed": "testbed",
            "problem_statement": "Test problem 1",
            "hint_string": "",
            "base_commit": "abc123",
            "patch": "",
            "test_patch": "",
            "failure_log": "",
            "created_at": "2024-01-01T00:00:00Z",
            "difficulty": "medium",
            "environment_commit_hash": "def456",
            "repo_directory": "/testbed",
        },
    ]
    cache.write_text(json.dumps(data))
    return cache


class TestFindHarnessReportForInstance:
    """Tests for find_harness_report_for_instance helper."""

    def test_finds_report_with_run_id(self, tmp_path: Path):
        """Returns report data when the run_id-based file exists (fallback pattern)."""
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        (eval_dir / "pi_django__django-11039.pi_django__django-11039.json").write_text(
            json.dumps(report_data)
        )

        result = find_harness_report_for_instance(tmp_path, "django__django-11039", "pi_django__django-11039")
        assert result == report_data

    def test_finds_report_with_agent_runid_pattern(self, tmp_path: Path):
        """Returns report data when the actual harness pattern {agent}.{run_id}.json exists."""
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        # This is the actual pattern the harness writes: {agent}.{run_id}.json
        (eval_dir / "pi.pi_django__django-11039.json").write_text(
            json.dumps(report_data)
        )

        result = find_harness_report_for_instance(tmp_path, "django__django-11039", "pi_django__django-11039")
        assert result == report_data

    def test_returns_none_when_no_eval_dir(self, tmp_path: Path):
        """Returns None when eval directory doesn't exist."""
        result = find_harness_report_for_instance(tmp_path, "django__django-11039", "pi_django__django-11039")
        assert result is None

    def test_returns_none_when_no_report_file(self, tmp_path: Path):
        """Returns None when no report file matches the run_id."""
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        # Wrong run_id file exists
        (eval_dir / "wrong.wrong.json").write_text("{}")

        result = find_harness_report_for_instance(tmp_path, "django__django-11039", "pi_django__django-11039")
        assert result is None

    def test_returns_none_on_corrupted_json(self, tmp_path: Path):
        """Returns None when report file contains invalid JSON."""
        eval_dir = tmp_path / "eval"
        eval_dir.mkdir()
        (eval_dir / "pi_django__django-11039.pi_django__django-11039.json").write_text("not json")

        result = find_harness_report_for_instance(tmp_path, "django__django-11039", "pi_django__django-11039")
        assert result is None


class TestRunEvalInstance:
    """Tests for Runner.run_eval_instance method."""

    @pytest.fixture
    def runner(self, test_workspace: Path) -> Runner:
        """Create a Runner with minimal config."""
        config = Config(repo_root=test_workspace)
        return Runner(config)

    def test_no_patch_returns_no_patch(self, runner: Runner, tmp_path: Path):
        """Returns no_patch status when patch.diff doesn't exist."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        # No patch.diff file
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        (eval_dir / "test-agent_django__django-11039.test-agent_django__django-11039.json").write_text(
            json.dumps(report_data)
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        assert result == {"status": "completed", "local_eval": "resolved"}

    def test_empty_patch_evaluates_empty_patch(self, runner: Runner, tmp_path: Path):
        """Evaluates empty patch when patch.diff is empty."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("")
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        (eval_dir / "test-agent_django__django-11039.test-agent_django__django-11039.json").write_text(
            json.dumps(report_data)
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        assert result == {"status": "completed", "local_eval": "resolved"}

    def test_harness_success_resolved(self, runner: Runner, tmp_path: Path):
        """Returns resolved when harness reports the instance as resolved."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test.py b/test.py\n+print('hello')")

        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        (eval_dir / "test-agent_django__django-11039.test-agent_django__django-11039.json").write_text(
            json.dumps(report_data)
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        assert result == {"status": "completed", "local_eval": "resolved"}

    def test_harness_success_failed(self, runner: Runner, tmp_path: Path):
        """Returns failed when harness reports the instance as unresolved."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test.py b/test.py\n+print('hello')")

        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        report_data = {
            "resolved_ids": [],
            "unresolved_ids": ["django__django-11039"],
            "error_ids": [],
        }
        (eval_dir / "test-agent_django__django-11039.test-agent_django__django-11039.json").write_text(
            json.dumps(report_data)
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        assert result == {"status": "completed", "local_eval": "failed"}

    def test_harness_success_error(self, runner: Runner, tmp_path: Path):
        """Returns error when harness reports the instance as errored."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test.py b/test.py\n+print('hello')")

        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        report_data = {
            "resolved_ids": [],
            "unresolved_ids": [],
            "error_ids": ["django__django-11039"],
        }
        (eval_dir / "test-agent_django__django-11039.test-agent_django__django-11039.json").write_text(
            json.dumps(report_data)
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        assert result == {"status": "completed", "local_eval": "error"}

    def test_harness_failure_returns_error(self, runner: Runner, tmp_path: Path):
        """Returns harness_error when the harness subprocess fails."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test.py b/test.py\n+print('hello')")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        assert result == {"status": "harness_error", "local_eval": None}

    def test_no_report_returns_no_report(self, runner: Runner, tmp_path: Path):
        """Returns no_report when harness succeeds but no report is found."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test.py b/test.py\n+print('hello')")

        # No eval dir, so no report
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        assert result == {"status": "no_report", "local_eval": None}

    def test_cleans_up_temp_predictions(self, runner: Runner, tmp_path: Path):
        """Temp predictions file is cleaned up even on error."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("diff --git a/test.py b/test.py\n+print('hello')")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        # Temp file should be cleaned up
        temp_files = list(output_dir.glob(".tmp_predictions_*.jsonl"))
        assert len(temp_files) == 0

    def test_cleans_up_temp_predictions_on_empty_patch(self, runner: Runner, tmp_path: Path):
        """Temp predictions file is cleaned up when patch is empty (now evaluated)."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        # No patch.diff (empty patch)
        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        (eval_dir / "test-agent_django__django-11039.test-agent_django__django-11039.json").write_text(
            json.dumps(report_data)
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        assert result == {"status": "completed", "local_eval": "resolved"}
        # Temp file should be cleaned up
        temp_files = list(output_dir.glob(".tmp_predictions_*.jsonl"))
        assert len(temp_files) == 0

    def test_writes_correct_predictions_jsonl(self, runner: Runner, tmp_path: Path):
        """Predictions file has correct format before being cleaned up."""
        output_dir = tmp_path / "test-agent"
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir(parents=True)
        (instance_dir / "patch.diff").write_text("my patch content")

        eval_dir = output_dir / "eval"
        eval_dir.mkdir()
        report_data = {
            "resolved_ids": ["django__django-11039"],
            "unresolved_ids": [],
            "error_ids": [],
        }
        (eval_dir / "test-agent_django__django-11039.test-agent_django__django-11039.json").write_text(
            json.dumps(report_data)
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.run_eval_instance(
                agent="test-agent",
                instance_id="django__django-11039",
                output_dir=output_dir,
            )

        # Verify the harness was called with correct arguments
        call_args = mock_run.call_args
        cmd = call_args.args[0]
        assert "--run_id" in cmd
        run_id_idx = cmd.index("--run_id")
        assert cmd[run_id_idx + 1] == "test-agent_django__django-11039"
        assert "-i" in cmd
        i_idx = cmd.index("-i")
        assert cmd[i_idx + 1] == "django__django-11039"
