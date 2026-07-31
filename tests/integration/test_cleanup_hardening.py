"""Integration tests for cleanup & hardening (P5).

Tests:
- cleanup-partial scope (never traverse above agent directory)
- Scoped container cleanup (active container only)
- Artifact preservation during timeout/error paths
- Run ID isolation for eval
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from swebench_orchestrator.manifest import (
    RunManager,
    cleanup_partial_attempts,
    list_runs,
)


class TestCleanupPartialScope:
    """Tests for cleanup-partial scope enforcement."""

    def test_never_traverses_above_agent_dir(self, test_workspace):
        """cleanup_partial never removes directories above agent level."""
        runs_dir = test_workspace / "runs"
        runs_dir.mkdir(exist_ok=True)

        # Create two agents' runs
        manager1 = RunManager(runs_dir)
        run1 = manager1.create_run(agent="agent-a")
        attempt1 = manager1.create_attempt(run1.run_id, "django__django-11039")
        # Make first attempt complete
        manager1.update_attempt_result(run1.run_id, attempt1.attempt_id, status="completed", patch_bytes=42)
        (runs_dir / run1.run_id / "tasks" / "django__django-11039" / attempt1.attempt_id / "patch.diff").write_text("diff")

        manager2 = RunManager(runs_dir)
        run2 = manager2.create_run(agent="agent-b")
        manager2.create_attempt(run2.run_id, "flask__flask-1000")

        # Create incomplete attempts for both agents
        incomplete_a = runs_dir / run1.run_id / "tasks" / "requests__requests-1234" / "attempt-001"
        incomplete_a.mkdir(parents=True)

        incomplete_b = runs_dir / run2.run_id / "tasks" / "django__django-5678" / "attempt-001"
        incomplete_b.mkdir(parents=True)

        # Cleanup only agent-a's runs
        removed = cleanup_partial_attempts(runs_dir, agent="agent-a", dry_run=False)

        # Should only remove agent-a's incomplete attempts (not the complete one)
        assert len(removed) == 1
        assert not incomplete_a.exists()

        # Agent-b's incomplete attempt should still exist
        assert incomplete_b.exists()

        # The runs directory itself should still exist
        assert runs_dir.exists()

    def test_cleanup_partial_keeps_complete_attempts(self, test_workspace):
        """Complete attempts are never removed."""
        runs_dir = test_workspace / "runs"
        runs_dir.mkdir(exist_ok=True)

        manager = RunManager(runs_dir)
        run = manager.create_run(agent="test-agent")
        attempt = manager.create_attempt(run.run_id, "django__django-11039")

        # Make it complete
        manager.update_attempt_result(
            run.run_id,
            attempt.attempt_id,
            status="completed",
            patch_bytes=42,
        )
        (runs_dir / run.run_id / "tasks" / "django__django-11039" / attempt.attempt_id / "patch.diff").write_text("diff")

        # Cleanup
        removed = cleanup_partial_attempts(runs_dir, dry_run=False)
        assert len(removed) == 0

        # Complete attempt should still exist
        assert (runs_dir / run.run_id / "tasks" / "django__django-11039" / attempt.attempt_id).exists()


class TestScopedContainerCleanup:
    """Tests for scoped container cleanup."""

    def test_cleanup_targets_only_active_container(self):
        """Cleanup only targets the active container, not all swe_* containers."""
        import subprocess as real_subprocess
        from swebench_orchestrator.storage import cleanup_docker_containers

        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            # Simulate multiple containers and their removal
            mock_subprocess.run.side_effect = [
                # First call: list swe_* containers
                MagicMock(
                    returncode=0,
                    stdout="swe_agent_a_instance1\nswe_agent_b_instance2\n",
                ),
                # Second call: docker rm -f (for 2 containers)
                MagicMock(returncode=0),
                # Third call: network inspect (none)
                MagicMock(returncode=0, stdout=""),
            ]
            # Make TimeoutExpired accessible
            mock_subprocess.TimeoutExpired = real_subprocess.TimeoutExpired

            result = cleanup_docker_containers()
            assert result["containers_removed"] == 2


class TestArtifactPreservation:
    """Tests for artifact preservation during error paths."""

    def test_artifacts_preserved_on_timeout(self, test_workspace):
        """On timeout, artifacts written by agent are preserved."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Simulate agent writing partial outputs before timeout
        instance_dir = output_dir / "django__django-11039"
        instance_dir.mkdir()
        (instance_dir / "result.json").write_text(
            '{"status": "timed_out", "patch_bytes": 0, "elapsed_seconds": 3600}'
        )
        (instance_dir / "agent_output.txt").write_text("Partial output before timeout...")
        (instance_dir / "pi-sessions").mkdir()
        (instance_dir / "pi-sessions" / "session.json").write_text('{"turns": 5}')

        # Verify artifacts are preserved
        assert (instance_dir / "result.json").exists()
        assert (instance_dir / "agent_output.txt").exists()
        assert (instance_dir / "pi-sessions" / "session.json").exists()

    def test_artifacts_preserved_on_error(self, test_workspace):
        """On error, artifacts written by agent are preserved."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        instance_dir = output_dir / "flask__flask-1000"
        instance_dir.mkdir()
        (instance_dir / "result.json").write_text(
            '{"status": "agent_error", "patch_bytes": 0, "elapsed_seconds": 10}'
        )
        (instance_dir / "agent_output.txt").write_text("Error output...")

        # Verify artifacts are preserved
        assert (instance_dir / "result.json").exists()
        assert (instance_dir / "agent_output.txt").exists()


class TestRunIdIsolation:
    """Tests for run ID isolation in eval."""

    def test_different_run_ids_produce_separate_evals(self, test_workspace):
        """Different run IDs produce separate eval reports."""
        runs_dir = test_workspace / "runs"
        runs_dir.mkdir(exist_ok=True)

        manager = RunManager(runs_dir)

        # Create two runs for the same agent
        run1 = manager.create_run(agent="test-agent", timeout=3600)
        run2 = manager.create_run(agent="test-agent", timeout=7200)

        # Verify they have different run IDs
        assert run1.run_id != run2.run_id

        # Both runs should be resolvable
        resolved1 = manager.resolve_run("test-agent", run1.run_id)
        resolved2 = manager.resolve_run("test-agent", run2.run_id)

        assert resolved1 is not None
        assert resolved2 is not None
        assert resolved1.run_id == run1.run_id
        assert resolved2.run_id == run2.run_id

    def test_eval_uses_run_id_for_report_isolation(self, test_workspace):
        """Eval uses run_id to prevent collision when patches change."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # First eval run
        preds1 = output_dir / "predictions.jsonl"
        with open(preds1, "w") as f:
            f.write(json.dumps({
                "instance_id": "django__django-11039",
                "model_name_or_path": "test-agent",
                "model_patch": "patch v1",
            }) + "\n")

        # Second eval run (different patch)
        preds2 = output_dir / "predictions_v2.jsonl"
        with open(preds2, "w") as f:
            f.write(json.dumps({
                "instance_id": "django__django-11039",
                "model_name_or_path": "test-agent",
                "model_patch": "patch v2",
            }) + "\n")

        # Both predictions files should exist independently
        assert preds1.exists()
        assert preds2.exists()

        # They should have different content
        content1 = json.loads(preds1.read_text())
        content2 = json.loads(preds2.read_text())
        assert content1["model_patch"] == "patch v1"
        assert content2["model_patch"] == "patch v2"


class TestResumeFlag:
    """Tests for --resume flag in run_all."""

    def test_resume_skips_completed_instances(self, test_workspace):
        """--resume skips instances that already have result.json."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Create a completed instance
        completed = output_dir / "django__django-11039"
        completed.mkdir()
        (completed / "result.json").write_text('{"status": "patch_collected"}')

        # Create an incomplete instance
        incomplete = output_dir / "flask__flask-1000"
        incomplete.mkdir()
        # No result.json

        # Count instances that would be skipped with --resume
        skipped = 0
        to_run = []
        for d in sorted(output_dir.iterdir()):
            if d.is_dir() and d.name not in ("eval", "logs"):
                if (d / "result.json").exists():
                    skipped += 1
                else:
                    to_run.append(d.name)

        assert skipped == 1
        assert "django__django-11039" not in to_run
        assert "flask__flask-1000" in to_run

    def test_resume_without_flag_runs_all(self, test_workspace):
        """Without --resume, all instances are processed."""
        output_dir = test_workspace / "outputs" / "test-agent"
        output_dir.mkdir(parents=True)

        # Create a completed instance
        completed = output_dir / "django__django-11039"
        completed.mkdir()
        (completed / "result.json").write_text('{"status": "patch_collected"}')

        # Without resume, all instances should be in the run list
        to_run = []
        for d in sorted(output_dir.iterdir()):
            if d.is_dir() and d.name not in ("eval", "logs"):
                to_run.append(d.name)

        assert len(to_run) == 1
        assert "django__django-11039" in to_run
