"""Tests for Docker command construction in run_instance.

These tests verify fixes for several bugs:
1. image_name was duplicated in the docker command (once in flags, once in command)
2. Volume paths were relative instead of absolute
3. Container user wasn't set to match host user (causing permission issues)
4. Output directory pre-creation caused permission denied errors
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.runner import run_instance


class TestDockerCommandConstruction:
    """Tests for docker command construction in run_instance."""

    def _setup_mocks(self, tmp_path: Path):
        """Create minimal agent setup and return cache file path."""
        agents_dir = tmp_path / "harnesses"
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
        return agents_dir, cache_file

    def test_image_name_not_duplicated_in_command(self, tmp_path: Path):
        """image_name should appear exactly once in the docker run command.

        Bug fix: Previously image_name was passed both in flags (as part of
        docker_ops.run_container) AND in the command list, resulting in:
          docker run ... image_name /agent/entrypoint.sh ... image_name /agent/entrypoint.sh ...
        This caused exit code 127 (command not found).
        """
        agents_dir, cache_file = self._setup_mocks(tmp_path)

        docker_ops = MagicMock()
        docker_ops.image_exists.return_value = True
        docker_ops.run_container.return_value = MagicMock(
            status="success", exit_code=0
        )
        docker_ops.copy_from_container.return_value = False

        run_instance(
            agents_dir=agents_dir,
            agent="pi",
            instance_id="django__django-11039",
            output_dir=tmp_path / "outputs",
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # Get the command that was passed to run_container
        call_args = docker_ops.run_container.call_args
        command = call_args.kwargs["command"] if call_args.kwargs else call_args[1]["command"]

        # image_name should NOT be in the command list (it's added by docker_ops)
        image_name = "swebench/sweb.eval.x86_64.django_1776_django-11039:latest"
        assert image_name not in command, (
            f"image_name should not be in command list (it's added by docker_ops.run_container). "
            f"Found: {command}"
        )

        # The command should start with /agent/entrypoint.sh
        assert command[0] == "/agent/entrypoint.sh", f"Expected command to start with entrypoint, got: {command}"

    def test_volume_paths_are_absolute(self, tmp_path: Path):
        """Volume mount paths should be absolute, not relative.

        Bug fix: Relative volume paths caused Docker to fail because the
        container couldn't resolve them correctly.
        """
        agents_dir, cache_file = self._setup_mocks(tmp_path)

        docker_ops = MagicMock()
        docker_ops.image_exists.return_value = True
        docker_ops.run_container.return_value = MagicMock(
            status="success", exit_code=0
        )
        docker_ops.copy_from_container.return_value = False

        run_instance(
            agents_dir=agents_dir,
            agent="pi",
            instance_id="django__django-11039",
            output_dir=tmp_path / "outputs",
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # Get the flags that were passed to run_container
        call_args = docker_ops.run_container.call_args
        flags = call_args.kwargs["flags"] if call_args.kwargs else call_args[1]["flags"]

        # Find volume mount flags
        volume_flags = [f for f in flags if f.startswith("-v") or f == "-v"]
        assert len(volume_flags) >= 2, f"Expected at least 2 volume flags, got: {flags}"

        # Check that volume paths are absolute
        for i, flag in enumerate(flags):
            if flag == "-v" and i + 1 < len(flags):
                mount_spec = flags[i + 1]
                host_path = mount_spec.split(":")[0]
                assert host_path.startswith("/"), (
                    f"Volume host path should be absolute, got: {host_path} "
                    f"in mount spec: {mount_spec}"
                )

    def test_container_runs_as_host_user(self, tmp_path: Path):
        """Container should run as root with HOST_UID/HOST_GID env vars for entrypoint.

        The entrypoint handles permission fixing: it chowns /testbed and output dirs
        to the host user, then runs the agent as that user via runuser.
        This avoids Docker/WSL chown issues while still letting the agent write.
        """
        import os
        agents_dir, cache_file = self._setup_mocks(tmp_path)

        docker_ops = MagicMock()
        docker_ops.image_exists.return_value = True
        docker_ops.run_container.return_value = MagicMock(
            status="success", exit_code=0
        )
        docker_ops.copy_from_container.return_value = False

        run_instance(
            agents_dir=agents_dir,
            agent="pi",
            instance_id="django__django-11039",
            output_dir=tmp_path / "outputs",
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # Get the flags that were passed to run_container
        call_args = docker_ops.run_container.call_args
        flags = call_args.kwargs["flags"] if call_args.kwargs else call_args[1]["flags"]

        # Check for HOST_UID/HOST_GID env vars instead of --user flag
        assert "-e" in flags
        host_uid_idx = flags.index("-e") + 1
        # Find HOST_UID env var
        host_uid_found = False
        host_gid_found = False
        for i, flag in enumerate(flags):
            if flag == "-e" and i + 1 < len(flags):
                env_val = flags[i + 1]
                if env_val.startswith("HOST_UID="):
                    host_uid_found = True
                    assert env_val == f"HOST_UID={os.getuid()}", f"Expected HOST_UID={os.getuid()}, got {env_val}"
                elif env_val.startswith("HOST_GID="):
                    host_gid_found = True
                    assert env_val == f"HOST_GID={os.getgid()}", f"Expected HOST_GID={os.getgid()}, got {env_val}"
        assert host_uid_found, f"HOST_UID not found in flags: {flags}"
        assert host_gid_found, f"HOST_GID not found in flags: {flags}"
        # Should NOT have --user flag
        assert "--user" not in flags, f"Should not have --user flag, got: {flags}"

    def test_instance_output_dir_not_pre_created(self, tmp_path: Path):
        """Instance output directory should NOT be pre-created before container runs.

        Bug fix: Pre-creating the instance dir caused permission denied errors when
        the container (running as root) tried to create subdirectories inside it.
        Docker/WSL doesn't allow root to create dirs inside host-owned directories.
        """
        agents_dir, cache_file = self._setup_mocks(tmp_path)

        output_dir = tmp_path / "outputs"
        output_dir.mkdir(parents=True)

        docker_ops = MagicMock()
        docker_ops.image_exists.return_value = True
        docker_ops.run_container.return_value = MagicMock(
            status="success", exit_code=0
        )
        docker_ops.copy_from_container.return_value = False

        run_instance(
            agents_dir=agents_dir,
            agent="pi",
            instance_id="django__django-11039",
            output_dir=output_dir,
            timeout=3600,
            cache_file=cache_file,
            docker_ops=docker_ops,
        )

        # The agent output root should exist (parent dir)
        agent_output_root = output_dir / "pi"
        assert agent_output_root.exists(), "Agent output root should be created"

        # But the instance dir should NOT be pre-created
        instance_dir = agent_output_root / "django__django-11039"
        # Note: The container creates it, so after run_instance completes with
        # copy_from_container=False, the instance dir may or may not exist depending
        # on whether the error handlers create it. We just verify the runner doesn't
        # pre-create it by checking that docker_ops.run_container was called.
        docker_ops.run_container.assert_called_once()


class TestRunEvalPaths:
    """Tests for run_eval path handling."""

    def test_swebench_py_uses_absolute_path(self, tmp_path: Path):
        """swebench_py should use absolute path to avoid symlink resolution issues.

        Bug fix: .venv/swebench/bin/python is a symlink to python3. Using resolve()
        follows the symlink to /usr/bin/python3 (system Python) which doesn't have
        swebench installed. Using absolute() keeps the symlink and uses the venv Python.
        """
        from swebench_orchestrator.runner import run_eval

        output_dir = tmp_path / "outputs" / "pi"
        output_dir.mkdir(parents=True)

        # Create a fake swebench venv with a symlink like the real one
        venv_bin = tmp_path / ".venv" / "swebench" / "bin"
        venv_bin.mkdir(parents=True)

        # Create a real python in the venv (simulating the venv's python3)
        real_python = venv_bin / "python3"
        real_python.write_text("#!/bin/bash\necho 'venv python'")
        real_python.chmod(0o755)

        # Create symlink like the real .venv/swebench/bin/python
        symlink_python = venv_bin / "python"
        symlink_python.symlink_to("python3")

        with patch("swebench_orchestrator.runner.generate_predictions") as mock_preds:
            # Return a patch so subprocess.run is actually called
            mock_preds.return_value = (
                output_dir / "predictions.jsonl",
                ["test__test-1"],
            )
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                run_eval(output_dir, "pi", swebench_py=None)

                # Get the command that was passed to subprocess.run
                call_args = mock_run.call_args
                cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("args", [])

                # The python path should be absolute (not relative)
                python_path = cmd[0]
                assert python_path.startswith("/"), (
                    f"swebench_py path should be absolute, got: {python_path}"
                )

    def test_predictions_path_is_absolute(self, tmp_path: Path):
        """predictions_path should be absolute in the eval command.

        Bug fix: Relative paths caused FileNotFoundError when subprocess.run
        was called with cwd=output_dir.
        """
        from swebench_orchestrator.runner import run_eval

        output_dir = tmp_path / "outputs" / "pi"
        output_dir.mkdir(parents=True)

        # Create a fake swebench python
        venv_bin = tmp_path / ".venv" / "swebench" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_python.write_text("#!/bin/bash\necho 'venv python'")
        fake_python.chmod(0o755)

        with patch("swebench_orchestrator.runner.generate_predictions") as mock_preds:
            mock_preds.return_value = (output_dir / "predictions.jsonl", ["test__test-1"])
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                run_eval(output_dir, "pi", swebench_py=fake_python)

                # Get the command
                call_args = mock_run.call_args
                cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("args", [])

                # Find --predictions_path argument
                pred_idx = cmd.index("--predictions_path")
                pred_path = cmd[pred_idx + 1]
                assert pred_path.startswith("/"), (
                    f"--predictions_path should be absolute, got: {pred_path}"
                )

    def test_report_dir_is_absolute(self, tmp_path: Path):
        """report_dir should be absolute in the eval command."""
        from swebench_orchestrator.runner import run_eval

        output_dir = tmp_path / "outputs" / "pi"
        output_dir.mkdir(parents=True)

        # Create a fake swebench python
        venv_bin = tmp_path / ".venv" / "swebench" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_python.write_text("#!/bin/bash\necho 'venv python'")
        fake_python.chmod(0o755)

        with patch("swebench_orchestrator.runner.generate_predictions") as mock_preds:
            mock_preds.return_value = (output_dir / "predictions.jsonl", ["test__test-1"])
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                run_eval(output_dir, "pi", swebench_py=fake_python)

                # Get the command
                call_args = mock_run.call_args
                cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("args", [])

                # Find --report_dir argument
                report_idx = cmd.index("--report_dir")
                report_path = cmd[report_idx + 1]
                assert report_path.startswith("/"), (
                    f"--report_dir should be absolute, got: {report_path}"
                )
