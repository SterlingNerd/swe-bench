"""Unit tests for storage operations."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from swebench_orchestrator.storage import (
    check_storage,
    get_disk_usage_pct,
    prune_docker_images,
    get_swebench_images,
)


class TestGetDiskUsagePct:
    """Tests for disk usage percentage."""

    def test_returns_percentage(self):
        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="Use%\n85\n")
            pct = get_disk_usage_pct("/some/path")
            assert pct == 85.0

    def test_returns_zero_on_failure(self):
        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=1)
            pct = get_disk_usage_pct("/some/path")
            assert pct == 0.0

    def test_parses_percentage_output(self):
        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="Use%\n  92 %\n")
            pct = get_disk_usage_pct("/some/path")
            assert pct == 92.0


class TestCheckStorage:
    """Tests for storage checking."""

    def test_below_threshold(self):
        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 50.0
            result = check_storage("/some/path", threshold_pct=80.0)
            assert result["is_warning"] is False
            assert result["is_critical"] is False

    def test_at_threshold(self):
        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 80.0
            result = check_storage("/some/path", threshold_pct=80.0)
            assert result["is_warning"] is True
            assert result["is_critical"] is False

    def test_above_threshold(self):
        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 85.0
            result = check_storage("/some/path", threshold_pct=80.0)
            assert result["is_warning"] is True

    def test_critical(self):
        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 92.0
            result = check_storage("/some/path", threshold_pct=80.0)
            assert result["is_warning"] is True
            assert result["is_critical"] is True

    def test_custom_threshold(self):
        with patch("swebench_orchestrator.storage.get_disk_usage_pct") as mock_get:
            mock_get.return_value = 75.0
            result = check_storage("/some/path", threshold_pct=90.0)
            assert result["is_warning"] is False


class TestGetSwebenchImages:
    """Tests for listing swebench Docker images."""

    def test_returns_image_list(self):
        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout="swebench/sweb.eval.x86_64.django_1776_django-11039 abc123\nswebench/sweb.eval.x86_64.flask_1776_flask-1000 def456\n"
            )
            images = get_swebench_images()
            assert len(images) == 2
            assert "abc123" in [i["id"] for i in images]

    def test_filters_only_swebench_images(self):
        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(
                returncode=0,
                stdout="swebench/sweb.eval.x86_64.django_1776_django-11039 abc123\nsome/other-image xyz789\n"
            )
            images = get_swebench_images()
            assert len(images) == 1
            assert images[0]["name"] == "swebench/sweb.eval.x86_64.django_1776_django-11039"

    def test_empty_result(self):
        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="")
            images = get_swebench_images()
            assert len(images) == 0


class TestPruneDockerImages:
    """Tests for Docker image pruning."""

    def test_prunes_images(self):
        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=0)
            result = prune_docker_images()
            assert result["removed"] >= 0

    def test_handles_prune_failure(self):
        with patch("swebench_orchestrator.storage.subprocess") as mock_subprocess:
            mock_subprocess.run.return_value = MagicMock(returncode=1)
            result = prune_docker_images()
            assert result["removed"] == 0
