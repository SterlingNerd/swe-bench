"""Unit tests for data models."""

import json
import tempfile
from pathlib import Path

import pytest

from swebench_orchestrator.models import (
    Attempt,
    Instance,
    InstanceSummary,
    Result,
    RunManifest,
    Summary,
    compute_storage_info,
    instance_to_image_name,
    read_json,
    write_json,
)


class TestInstance:
    """Tests for the Instance model."""

    def test_instance_id_property(self):
        inst = Instance(
            instance_id="django__django-11039",
            repo="django/django",
            version="4.2",
            testbed="testbed",
            problem_statement="Test problem",
            hint_string="",
            base_commit="abc123",
            patch="",
            test_patch="",
            failure_log="",
            created_at="2024-01-01T00:00:00Z",
            difficulty="medium",
            environment_commit_hash="def456",
            repo_directory="/testbed",
        )
        assert inst.instance_id == "django__django-11039"

    def test_repo_image_name(self):
        inst = Instance(
            instance_id="django__django-11039",
            repo="django/django",
            version="4.2",
            testbed="testbed",
            problem_statement="",
            hint_string="",
            base_commit="abc123",
            patch="",
            test_patch="",
            failure_log="",
            created_at="2024-01-01T00:00:00Z",
            difficulty="medium",
            environment_commit_hash="def456",
            repo_directory="/testbed",
        )
        assert inst.repo_image_name == "django_django"

    def test_repo_image_name_no_slash(self):
        inst = Instance(
            instance_id="flask__flask-1000",
            repo="pallets/flask",
            version="2.3",
            testbed="testbed",
            problem_statement="",
            hint_string="",
            base_commit="abc123",
            patch="",
            test_patch="",
            failure_log="",
            created_at="2024-01-01T00:00:00Z",
            difficulty="easy",
            environment_commit_hash="def456",
            repo_directory="/testbed",
        )
        assert inst.repo_image_name == "pallets_flask"


class TestInstanceSummary:
    """Tests for InstanceSummary."""

    def test_from_instance(self):
        inst = Instance(
            instance_id="django__django-11039",
            repo="django/django",
            version="4.2",
            testbed="testbed",
            problem_statement="",
            hint_string="",
            base_commit="abc123",
            patch="",
            test_patch="",
            failure_log="",
            created_at="2024-01-01T00:00:00Z",
            difficulty="medium",
            environment_commit_hash="def456",
            repo_directory="/testbed",
        )
        summary = InstanceSummary.from_instance(inst)
        assert summary.instance_id == "django__django-11039"
        assert summary.repo == "django/django"
        assert summary.version == "4.2"
        assert summary.difficulty == "medium"


class TestRunManifest:
    """Tests for RunManifest."""

    def test_default_values(self):
        manifest = RunManifest(run_id="run-001", agent="pi")
        assert manifest.agent == "pi"
        assert manifest.timeout == 3600
        assert manifest.profile == "default"
        assert manifest.dataset_hash == ""
        assert manifest.commit_hash == ""

    def test_custom_values(self):
        manifest = RunManifest(
            run_id="run-002",
            agent="codex",
            timeout=7200,
            profile="aggressive",
            dataset_hash="sha256:abc123",
            commit_hash="git-hash-xyz",
        )
        assert manifest.run_id == "run-002"
        assert manifest.agent == "codex"
        assert manifest.timeout == 7200
        assert manifest.profile == "aggressive"


class TestAttempt:
    """Tests for Attempt model."""

    def test_default_values(self):
        attempt = Attempt(attempt_id="attempt-001", instance_id="django__django-11039")
        assert attempt.status == "pending"
        assert attempt.patch_bytes == 0
        assert attempt.elapsed_seconds == 0
        assert attempt.container_exit_code == 0
        assert attempt.local_eval is None

    def test_completed_attempt(self):
        attempt = Attempt(
            attempt_id="attempt-001",
            instance_id="django__django-11039",
            status="completed",
            patch_bytes=42,
            elapsed_seconds=120,
            container_exit_code=0,
            local_eval="resolved",
        )
        assert attempt.status == "completed"
        assert attempt.patch_bytes == 42


class TestResult:
    """Tests for Result model."""

    def test_default_values(self):
        result = Result()
        assert result.status == "patch_collected"
        assert result.patch_bytes == 0
        assert result.elapsed_seconds == 0

    def test_from_dict(self):
        data = {"status": "timed_out", "patch_bytes": 0, "elapsed_seconds": 3600}
        result = Result(**data)
        assert result.status == "timed_out"
        assert result.elapsed_seconds == 3600


class TestSummary:
    """Tests for Summary model."""

    def test_default_values(self):
        summary = Summary(agent="pi")
        assert summary.agent == "pi"
        assert summary.total == 0
        assert summary.resolved == 0

    def test_with_rows(self):
        rows = [
            {"instance_id": "django__django-11039", "status": "resolved", "local_eval": "resolved"},
            {"instance_id": "flask__flask-1000", "status": "failed", "local_eval": "failed"},
        ]
        summary = Summary(
            agent="pi",
            total=2,
            resolved=1,
            failed=1,
            rows=rows,
        )
        assert summary.total == 2
        assert summary.resolved == 1
        assert summary.failed == 1


class TestComputeStorageInfo:
    """Tests for storage info computation."""

    def test_normal_usage(self):
        info = compute_storage_info(50.0, 80.0)
        assert info["usage_pct"] == 50.0
        assert info["is_warning"] is False
        assert info["is_critical"] is False

    def test_at_threshold(self):
        info = compute_storage_info(80.0, 80.0)
        assert info["is_warning"] is True
        assert info["is_critical"] is False

    def test_above_threshold(self):
        info = compute_storage_info(85.0, 80.0)
        assert info["is_warning"] is True
        assert info["is_critical"] is False

    def test_critical(self):
        info = compute_storage_info(92.0, 80.0)
        assert info["is_warning"] is True
        assert info["is_critical"] is True


class TestInstanceToImageName:
    """Tests for instance_to_image_name function."""

    def test_x86_64(self):
        name = instance_to_image_name("django__django-11039")
        assert name == "swebench/sweb.eval.x86_64.django_1776_django-11039:latest"

    def test_arm64(self):
        name = instance_to_image_name("django__django-11039", arch="arm64")
        assert name == "swebench/sweb.eval.arm64.django_1776_django-11039:latest"

    def test_custom_registry(self):
        name = instance_to_image_name("flask__flask-1000", registry="myregistry")
        assert name == "myregistry/sweb.eval.x86_64.flask_1776_flask-1000:latest"

    def test_repo_with_slash(self):
        # psf/requests → instance_id is "requests__requests-1234"
        name = instance_to_image_name("requests__requests-1234")
        assert "requests_1776_requests-1234" in name


class TestReadWriteJson:
    """Tests for JSON file I/O."""

    def test_write_and_read_json(self, tmp_path: Path):
        data = {"key": "value", "number": 42}
        path = tmp_path / "test.json"
        write_json(path, data)
        loaded = read_json(path)
        assert loaded == data

    def test_write_creates_parent_dirs(self, tmp_path: Path):
        data = {"nested": {"deep": True}}
        path = tmp_path / "a" / "b" / "c" / "test.json"
        write_json(path, data)
        assert path.exists()
        assert read_json(path) == data

    def test_read_nonexistent_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            read_json(tmp_path / "nonexistent.json")
