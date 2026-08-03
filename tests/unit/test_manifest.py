"""Unit tests for manifest operations."""

import json
from pathlib import Path

import pytest

from swebench_orchestrator.manifest import (
    RunManager,
    create_run_manifest,
    resolve_run,
    list_runs,
    cleanup_partial_attempts,
)


class TestCreateRunManifest:
    """Tests for run manifest creation."""

    def test_creates_manifest(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manifest = create_run_manifest(
            runs_dir=runs_dir,
            agent="pi",
            timeout=3600,
            profile="default",
        )
        assert manifest is not None
        assert manifest.agent == "pi"
        assert manifest.timeout == 3600
        assert manifest.profile == "default"

    def test_manifest_file_created(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manifest = create_run_manifest(runs_dir, agent="pi")
        manifest_file = runs_dir / manifest.run_id / "manifest.json"
        assert manifest_file.exists()

    def test_manifest_content(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manifest = create_run_manifest(runs_dir, agent="pi", timeout=7200)
        manifest_file = runs_dir / manifest.run_id / "manifest.json"
        data = json.loads(manifest_file.read_text())
        assert data["run_id"] == manifest.run_id
        assert data["agent"] == "pi"
        assert data["timeout"] == 7200

    def test_run_id_format(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manifest = create_run_manifest(runs_dir, agent="pi")
        assert manifest.run_id.startswith("run-")
        # Should be a UUID-like suffix
        parts = manifest.run_id.split("-", 1)
        assert len(parts[1]) > 0


class TestRunManager:
    """Tests for the RunManager class."""

    def test_create_run(self, tmp_path: Path):
        manager = RunManager(tmp_path / "runs")
        run = manager.create_run(agent="pi", timeout=3600)
        assert run is not None
        assert run.agent == "pi"

    def test_resolve_run_latest(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        RunManager(runs_dir).create_run(agent="pi")
        RunManager(runs_dir).create_run(agent="codex")

        latest = resolve_run(runs_dir, agent="pi")
        assert latest is not None
        assert latest.agent == "pi"

    def test_resolve_run_no_runs(self, tmp_path: Path):
        result = resolve_run(tmp_path / "runs", agent="nonexistent")
        assert result is None

    def test_list_runs(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        RunManager(runs_dir).create_run(agent="pi")
        RunManager(runs_dir).create_run(agent="codex")

        runs = list(list_runs(runs_dir))
        assert len(runs) == 2

    def test_list_runs_filtered(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        RunManager(runs_dir).create_run(agent="pi")
        RunManager(runs_dir).create_run(agent="codex")

        pi_runs = list(list_runs(runs_dir, agent="pi"))
        assert len(pi_runs) == 1
        assert pi_runs[0].agent == "pi"

    def test_create_attempt(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")
        attempt = manager.create_attempt(run.run_id, "django__django-11039")

        assert attempt is not None
        assert attempt.instance_id == "django__django-11039"
        assert attempt.attempt_id.startswith("attempt-")

    def test_attempt_directory_created(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")
        manager.create_attempt(run.run_id, "django__django-11039")

        attempt_dir = runs_dir / run.run_id / "tasks" / "django__django-11039" / "attempt-001"
        assert attempt_dir.is_dir()

    def test_multiple_attempts_increment(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")

        a1 = manager.create_attempt(run.run_id, "django__django-11039")
        a2 = manager.create_attempt(run.run_id, "django__django-11039")

        assert "attempt-001" in a1.attempt_id
        assert "attempt-002" in a2.attempt_id

    def test_update_attempt_result(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")
        attempt = manager.create_attempt(run.run_id, "django__django-11039")

        manager.update_attempt_result(
            run.run_id,
            attempt.attempt_id,
            status="completed",
            patch_bytes=42,
            elapsed_seconds=120,
            container_exit_code=0,
        )

        result_file = (
            runs_dir / run.run_id / "tasks" / "django__django-11039"
            / attempt.attempt_id / "result.json"
        )
        assert result_file.exists()
        data = json.loads(result_file.read_text())
        assert data["status"] == "completed"
        assert data["patch_bytes"] == 42

    def test_get_attempt_result(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")
        attempt = manager.create_attempt(run.run_id, "django__django-11039")

        manager.update_attempt_result(
            run.run_id,
            attempt.attempt_id,
            status="completed",
            patch_bytes=42,
        )

        result = manager.get_attempt_result(run.run_id, attempt.attempt_id)
        assert result is not None
        assert result["status"] == "completed"
        assert result["patch_bytes"] == 42

    def test_get_nonexistent_attempt(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manager = RunManager(runs_dir)
        result = manager.get_attempt_result("run-nonexistent", "attempt-001")
        assert result is None


class TestCleanupPartialAttempts:
    """Tests for partial attempt cleanup."""

    def test_dry_run_lists_incomplete(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")
        manager.create_attempt(run.run_id, "django__django-11039")

        # Create a partial attempt (no result.json)
        partial_dir = runs_dir / run.run_id / "tasks" / "flask__flask-1000" / "attempt-001"
        partial_dir.mkdir(parents=True)
        (partial_dir / "patch.diff").write_text("diff --git a/test b/test")

        incomplete = cleanup_partial_attempts(runs_dir, dry_run=True)
        assert len(incomplete) >= 1

    def test_apply_removes_incomplete(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")
        manager.create_attempt(run.run_id, "django__django-11039")

        # Create a partial attempt
        partial_dir = runs_dir / run.run_id / "tasks" / "flask__flask-1000" / "attempt-001"
        partial_dir.mkdir(parents=True)
        (partial_dir / "patch.diff").write_text("diff")

        removed = cleanup_partial_attempts(runs_dir, dry_run=False)
        assert not partial_dir.exists()

    def test_keeps_complete_attempts(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        manager = RunManager(runs_dir)
        run = manager.create_run(agent="pi")
        attempt = manager.create_attempt(run.run_id, "django__django-11039")

        manager.update_attempt_result(
            run.run_id,
            attempt.attempt_id,
            status="completed",
            patch_bytes=42,
        )

        # Also create a patch.diff to make it complete
        result_file = (
            runs_dir / run.run_id / "tasks" / "django__django-11039"
            / attempt.attempt_id / "result.json"
        )
        patch_file = runs_dir / run.run_id / "tasks" / "django__django-11039" / attempt.attempt_id / "patch.diff"
        patch_file.write_text("diff")

        removed = cleanup_partial_attempts(runs_dir, dry_run=False)
        assert len(removed) == 0  # No incomplete attempts

    def test_respects_agent_filter(self, tmp_path: Path):
        runs_dir = tmp_path / "runs"
        RunManager(runs_dir).create_run(agent="pi")
        RunManager(runs_dir).create_run(agent="codex")

        # Create partial in pi run
        pi_runs = list(list_runs(runs_dir, agent="pi"))
        if pi_runs:
            partial_dir = runs_dir / pi_runs[0].run_id / "tasks" / "test__test-1" / "attempt-001"
            partial_dir.mkdir(parents=True)

        removed = cleanup_partial_attempts(runs_dir, agent="codex", dry_run=False)
        # Should not remove anything from pi's run
        assert len(removed) == 0
