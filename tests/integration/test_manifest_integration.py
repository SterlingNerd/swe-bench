"""Integration tests for manifest operations with real file I/O."""

import json
from pathlib import Path

from swebench_orchestrator.manifest import (
    RunManager,
    cleanup_partial_attempts,
    create_run_manifest,
    list_runs,
    resolve_run,
)


class TestRunManagerIntegration:
    """Integration tests for RunManager with real filesystem operations."""

    def test_full_run_lifecycle(self, test_workspace):
        """Test complete run lifecycle: create, attempt, result, query."""
        runs_dir = test_workspace / "runs"
        manager = RunManager(runs_dir)

        # Create run
        run = manager.create_run(agent="pi", timeout=3600)
        assert run.run_id.startswith("run-")
        assert run.agent == "pi"

        # Verify manifest file
        manifest_file = runs_dir / run.run_id / "manifest.json"
        assert manifest_file.exists()
        data = json.loads(manifest_file.read_text())
        assert data["run_id"] == run.run_id
        assert data["agent"] == "pi"

        # Create attempt
        attempt = manager.create_attempt(run.run_id, "django__django-11039")
        assert attempt.instance_id == "django__django-11039"
        assert "attempt-001" in attempt.attempt_id

        # Verify attempt directory
        attempt_dir = runs_dir / run.run_id / "tasks" / "django__django-11039" / attempt.attempt_id
        assert attempt_dir.is_dir()
        assert (attempt_dir / "meta.json").exists()

        # Update result
        manager.update_attempt_result(
            run.run_id,
            attempt.attempt_id,
            status="completed",
            patch_bytes=42,
            elapsed_seconds=120,
        )

        # Verify result file
        result_file = attempt_dir / "result.json"
        assert result_file.exists()
        result_data = json.loads(result_file.read_text())
        assert result_data["status"] == "completed"
        assert result_data["patch_bytes"] == 42

    def test_multiple_attempts_increment(self, test_workspace):
        """Test that multiple attempts for same instance get sequential IDs."""
        runs_dir = test_workspace / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")

        a1 = manager.create_attempt(run.run_id, "django__django-11039")
        a2 = manager.create_attempt(run.run_id, "django__django-11039")
        a3 = manager.create_attempt(run.run_id, "django__django-11039")

        assert "attempt-001" in a1.attempt_id
        assert "attempt-002" in a2.attempt_id
        assert "attempt-003" in a3.attempt_id

    def test_resolve_run_by_id(self, test_workspace):
        """Test resolving a specific run by ID."""
        runs_dir = test_workspace / "runs"
        manager = RunManager(runs_dir)

        run1 = manager.create_run(agent="pi")
        run2 = manager.create_run(agent="codex")

        resolved = manager.resolve_run("pi", run1.run_id)
        assert resolved is not None
        assert resolved.run_id == run1.run_id

    def test_resolve_run_latest(self, test_workspace):
        """Test resolving the latest run for an agent."""
        runs_dir = test_workspace / "runs"
        manager = RunManager(runs_dir)

        run1 = manager.create_run(agent="pi")
        run2 = manager.create_run(agent="pi")

        latest = manager.resolve_run("pi")
        assert latest is not None
        assert latest.run_id == run2.run_id  # Latest created

    def test_list_runs_filtered(self, test_workspace):
        """Test listing runs filtered by agent."""
        runs_dir = test_workspace / "runs"
        manager = RunManager(runs_dir)

        manager.create_run(agent="pi")
        manager.create_run(agent="codex")
        manager.create_run(agent="pi")

        pi_runs = list(list_runs(runs_dir, agent="pi"))
        assert len(pi_runs) == 2

        all_runs = list(list_runs(runs_dir))
        assert len(all_runs) == 3

    def test_get_attempt_result(self, test_workspace):
        """Test retrieving attempt results."""
        runs_dir = test_workspace / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")
        attempt = manager.create_attempt(run.run_id, "django__django-11039")

        # No result yet
        result = manager.get_attempt_result(run.run_id, attempt.attempt_id)
        assert result is None

        # Write result
        manager.update_attempt_result(
            run.run_id,
            attempt.attempt_id,
            status="completed",
            patch_bytes=100,
        )

        # Read result
        result = manager.get_attempt_result(run.run_id, attempt.attempt_id)
        assert result is not None
        assert result["status"] == "completed"
        assert result["patch_bytes"] == 100

    def test_list_attempts(self, test_workspace):
        """Test listing attempts for a run."""
        runs_dir = test_workspace / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")

        manager.create_attempt(run.run_id, "django__django-11039")
        manager.create_attempt(run.run_id, "flask__flask-1000")
        manager.create_attempt(run.run_id, "django__django-11039")  # Second attempt

        all_attempts = list(manager.list_attempts(run.run_id))
        assert len(all_attempts) == 3

        django_attempts = list(manager.list_attempts(run.run_id, "django__django-11039"))
        assert len(django_attempts) == 2


class TestCleanupPartialAttemptsIntegration:
    """Integration tests for cleanup_partial_attempts."""

    def test_removes_incomplete_attempts(self, test_workspace):
        """Test that incomplete attempts are removed."""
        runs_dir = test_workspace / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")

        # Create a complete attempt
        complete = manager.create_attempt(run.run_id, "django__django-11039")
        manager.update_attempt_result(
            run.run_id,
            complete.attempt_id,
            status="completed",
            patch_bytes=42,
        )
        # Also create patch.diff to make it fully complete
        (runs_dir / run.run_id / "tasks" / "django__django-11039" / complete.attempt_id / "patch.diff").write_text("diff")

        # Create an incomplete attempt (no result.json)
        incomplete_dir = runs_dir / run.run_id / "tasks" / "flask__flask-1000" / "attempt-001"
        incomplete_dir.mkdir(parents=True)
        (incomplete_dir / "agent_output.txt").write_text("partial output")

        # Cleanup
        removed = cleanup_partial_attempts(runs_dir, dry_run=False)
        assert len(removed) == 1
        assert not incomplete_dir.exists()

        # Complete attempt should still exist
        assert (runs_dir / run.run_id / "tasks" / "django__django-11039" / complete.attempt_id).exists()

    def test_dry_run_lists_without_removing(self, test_workspace):
        """Test that dry_run lists incomplete attempts without removing."""
        runs_dir = test_workspace / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")

        # Create incomplete attempt
        incomplete_dir = runs_dir / run.run_id / "tasks" / "flask__flask-1000" / "attempt-001"
        incomplete_dir.mkdir(parents=True)

        # Dry run
        listed = cleanup_partial_attempts(runs_dir, dry_run=True)
        assert len(listed) == 1
        assert incomplete_dir.exists()  # Not removed

    def test_respects_agent_filter(self, test_workspace):
        """Test that agent filter limits cleanup scope."""
        runs_dir = test_workspace / "runs"
        RunManager(runs_dir).create_run(agent="pi")
        RunManager(runs_dir).create_run(agent="codex")

        # Create incomplete in pi's run
        pi_runs = list(list_runs(runs_dir, agent="pi"))
        if pi_runs:
            incomplete_dir = runs_dir / pi_runs[0].run_id / "tasks" / "test__test-1" / "attempt-001"
            incomplete_dir.mkdir(parents=True)

        # Cleanup only codex runs — should not touch pi's incomplete
        removed = cleanup_partial_attempts(runs_dir, agent="codex", dry_run=False)
        assert len(removed) == 0


class TestCreateRunManifestIntegration:
    """Integration tests for create_run_manifest function."""

    def test_creates_proper_directory_structure(self, test_workspace):
        """Test that manifest creation creates correct directory structure."""
        runs_dir = test_workspace / "runs"
        manifest = create_run_manifest(runs_dir, agent="pi", timeout=7200)

        # Check directory structure
        assert (runs_dir / manifest.run_id).is_dir()
        assert (runs_dir / manifest.run_id / "manifest.json").exists()
        assert (runs_dir / manifest.run_id / "tasks").is_dir()

    def test_manifest_content(self, test_workspace):
        """Test that manifest file contains correct data."""
        runs_dir = test_workspace / "runs"
        manifest = create_run_manifest(
            runs_dir,
            agent="codex",
            timeout=7200,
            profile="aggressive",
            dataset_hash="sha256:abc123",
        )

        data = json.loads((runs_dir / manifest.run_id / "manifest.json").read_text())
        assert data["run_id"] == manifest.run_id
        assert data["agent"] == "codex"
        assert data["timeout"] == 7200
        assert data["profile"] == "aggressive"
        assert data["dataset_hash"] == "sha256:abc123"
