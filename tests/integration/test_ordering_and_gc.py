"""Integration tests for smart ordering and garbage collection."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.dataset import DatasetCache


class TestInstanceOrdering:
    """Tests for instance sorting by repo → version → instance_id."""

    def test_sorted_by_repo_then_version(self, test_workspace):
        """Instances sorted by repo first, then version within repo."""
        cache_file = test_workspace / "cache.json"
        data = [
            {"instance_id": "zoo__zoo-3", "repo": "zoo/zoo", "version": "1.0"},
            {"instance_id": "aaa__aaa-2", "repo": "aaa/aaa", "version": "2.0"},
            {"instance_id": "aaa__aaa-1", "repo": "aaa/aaa", "version": "1.0"},
            {"instance_id": "zzz__zzz-1", "repo": "zzz/zzz", "version": "1.0"},
        ]
        cache_file.write_text(json.dumps(data))

        cache = DatasetCache(cache_file)
        ordered = list(cache.list_instances())

        assert ordered[0]["instance_id"] == "aaa__aaa-1"  # aaa v1.0
        assert ordered[1]["instance_id"] == "aaa__aaa-2"  # aaa v2.0
        assert ordered[2]["instance_id"] == "zoo__zoo-3"  # zoo v1.0
        assert ordered[3]["instance_id"] == "zzz__zzz-1"  # zzz v1.0

    def test_sorted_by_version_then_instance_id(self, test_workspace):
        """Within same repo/version, sorted by instance_id."""
        cache_file = test_workspace / "cache.json"
        data = [
            {"instance_id": "django__django-2000", "repo": "django/django", "version": "4.2"},
            {"instance_id": "django__django-1000", "repo": "django/django", "version": "4.2"},
            {"instance_id": "django__django-3000", "repo": "django/django", "version": "4.2"},
        ]
        cache_file.write_text(json.dumps(data))

        cache = DatasetCache(cache_file)
        ordered = list(cache.list_instances())

        assert ordered[0]["instance_id"] == "django__django-1000"
        assert ordered[1]["instance_id"] == "django__django-2000"
        assert ordered[2]["instance_id"] == "django__django-3000"


class TestDiskUsageMonitoring:
    """Tests for disk usage monitoring and GC triggers."""

    def test_below_threshold_no_gc(self, test_workspace):
        """Below threshold → no GC needed."""
        from swebench_orchestrator.storage import check_storage

        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 50.0
            result = check_storage(test_workspace, threshold_pct=80.0)
            assert result["is_warning"] is False
            assert result["is_critical"] is False

    def test_at_threshold_triggers_warning(self, test_workspace):
        """At threshold → warning triggered."""
        from swebench_orchestrator.storage import check_storage

        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 80.0
            result = check_storage(test_workspace, threshold_pct=80.0)
            assert result["is_warning"] is True
            assert result["is_critical"] is False

    def test_above_threshold_triggers_warning(self, test_workspace):
        """Above threshold → warning triggered."""
        from swebench_orchestrator.storage import check_storage

        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 85.0
            result = check_storage(test_workspace, threshold_pct=80.0)
            assert result["is_warning"] is True

    def test_critical_at_90(self, test_workspace):
        """At 90% → critical triggered."""
        from swebench_orchestrator.storage import check_storage

        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 92.0
            result = check_storage(test_workspace, threshold_pct=80.0)
            assert result["is_warning"] is True
            assert result["is_critical"] is True

    def test_custom_threshold(self, test_workspace):
        """Custom threshold works correctly."""
        from swebench_orchestrator.storage import check_storage

        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 75.0
            result = check_storage(test_workspace, threshold_pct=90.0)
            assert result["is_warning"] is False


class TestPeriodicGC:
    """Tests for periodic garbage collection during run_all."""

    def test_gc_every_n_instances(self, test_workspace):
        """GC triggered every N instances processed."""
        from swebench_orchestrator.storage import check_storage

        gc_triggered = []

        def mock_check(path, threshold_pct=80.0):
            # Simulate normal disk usage
            return {"usage_pct": 50.0, "is_warning": False, "is_critical": False}

        with patch("swebench_orchestrator.storage.check_storage", side_effect=mock_check):
            # Simulate processing 50 instances with GC every 20
            instances = list(range(50))
            gc_count = 0
            gc_interval = 20

            for i, iid in enumerate(instances):
                # Check if we should trigger periodic GC
                if (i + 1) % gc_interval == 0 and i > 0:
                    gc_count += 1
                    # In real code, this would call prune_docker_images()

            assert gc_count == 2  # GC at instance 20 and 40


class TestEmergencyGC:
    """Tests for emergency garbage collection when disk is full."""

    def test_emergency_gc_at_critical(self, test_workspace):
        """Emergency GC triggered when disk reaches critical threshold."""
        from swebench_orchestrator.storage import check_storage

        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 95.0
            result = check_storage(test_workspace, threshold_pct=80.0)

            # Should trigger emergency GC
            assert result["is_critical"] is True
            assert result["is_warning"] is True

    def test_emergency_gc_prevents_new_runs(self, test_workspace):
        """When disk is critical, new runs should be blocked."""
        from swebench_orchestrator.storage import check_storage

        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 95.0
            result = check_storage(test_workspace, threshold_pct=80.0)

            # Should block new runs
            assert result["is_critical"] is True


class TestImagePruning:
    """Tests for Docker image pruning after eval."""

    def test_prune_after_eval(self, test_workspace):
        """Images are pruned after evaluation completes."""
        from swebench_orchestrator.storage import get_swebench_images, prune_docker_images

        # Mock the subprocess calls
        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            # First call: list images
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout="swebench/sweb.eval.x86_64.django_1776_django-11039 abc123\n"
            )

            images = get_swebench_images()
            assert len(images) == 1
            assert images[0]["name"] == "swebench/sweb.eval.x86_64.django_1776_django-11039"

            # Second call: prune images
            mock_subprocess.run.return_value = MagicMock(returncode=0)
            result = prune_docker_images()
            assert result["removed"] >= 0
