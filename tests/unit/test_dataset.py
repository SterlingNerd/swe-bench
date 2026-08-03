"""Unit tests for dataset operations."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from swebench_orchestrator.dataset import (
    DatasetCache,
    fetch_and_cache_dataset,
)


class TestDatasetCache:
    """Tests for DatasetCache class."""

    def test_cache_file_not_exists(self, tmp_path: Path):
        cache = DatasetCache(tmp_path / "cache.json")
        assert cache.is_valid is False

    def test_cache_file_empty(self, tmp_path: Path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("")
        cache = DatasetCache(cache_file)
        assert cache.is_valid is False

    def test_cache_file_invalid_json(self, tmp_path: Path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("not json")
        cache = DatasetCache(cache_file)
        assert cache.is_valid is False

    def test_cache_file_not_a_list(self, tmp_path: Path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text('{"key": "value"}')
        cache = DatasetCache(cache_file)
        assert cache.is_valid is False

    def test_cache_file_empty_list(self, tmp_path: Path):
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("[]")
        cache = DatasetCache(cache_file)
        assert cache.is_valid is False

    def test_cache_file_valid(self, tmp_path: Path):
        data = [
            {"instance_id": "django__django-11039", "repo": "django/django"},
            {"instance_id": "flask__flask-1000", "repo": "pallets/flask"},
        ]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(data))
        cache = DatasetCache(cache_file)
        assert cache.is_valid is True
        assert len(cache.data) == 2

    def test_get_instance_found(self, tmp_path: Path):
        data = [
            {"instance_id": "django__django-11039", "repo": "django/django", "version": "4.2"},
            {"instance_id": "flask__flask-1000", "repo": "pallets/flask", "version": "2.3"},
        ]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(data))
        cache = DatasetCache(cache_file)
        result = cache.get_instance("django__django-11039")
        assert result is not None
        assert result["instance_id"] == "django__django-11039"

    def test_get_instance_not_found(self, tmp_path: Path):
        data = [
            {"instance_id": "django__django-11039", "repo": "django/django"},
        ]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(data))
        cache = DatasetCache(cache_file)
        result = cache.get_instance("nonexistent__instance-999")
        assert result is None

    def test_list_instances_no_filter(self, tmp_path: Path):
        data = [
            {"instance_id": "django__django-11039", "repo": "django/django", "version": "4.2", "difficulty": "medium"},
            {"instance_id": "flask__flask-1000", "repo": "pallets/flask", "version": "2.3", "difficulty": "easy"},
            {"instance_id": "requests__requests-1234", "repo": "psf/requests", "version": "2.28", "difficulty": "hard"},
        ]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(data))
        cache = DatasetCache(cache_file)
        results = list(cache.list_instances())
        assert len(results) == 3

    def test_list_instances_with_filter(self, tmp_path: Path):
        data = [
            {"instance_id": "django__django-11039", "repo": "django/django", "version": "4.2", "difficulty": "medium"},
            {"instance_id": "flask__flask-1000", "repo": "pallets/flask", "version": "2.3", "difficulty": "easy"},
        ]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(data))
        cache = DatasetCache(cache_file)
        results = list(cache.list_instances(filter_str="flask"))
        assert len(results) == 1
        assert results[0]["instance_id"] == "flask__flask-1000"

    def test_list_instances_filter_case_insensitive(self, tmp_path: Path):
        data = [
            {"instance_id": "django__django-11039", "repo": "django/django", "version": "4.2", "difficulty": "medium"},
        ]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(data))
        cache = DatasetCache(cache_file)
        results = list(cache.list_instances(filter_str="DJANGO"))
        assert len(results) == 1

    def test_list_instances_sorted_by_repo_version(self, tmp_path: Path):
        data = [
            {"instance_id": "zoo__zoo-1", "repo": "zoo/zoo", "version": "1.0", "difficulty": "easy"},
            {"instance_id": "aaa__aaa-1", "repo": "aaa/aaa", "version": "2.0", "difficulty": "hard"},
            {"instance_id": "aaa__aaa-2", "repo": "aaa/aaa", "version": "1.0", "difficulty": "medium"},
        ]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(data))
        cache = DatasetCache(cache_file)
        results = list(cache.list_instances())
        assert results[0]["instance_id"] == "aaa__aaa-2"  # aaa/aaa v1.0 first
        assert results[1]["instance_id"] == "aaa__aaa-1"  # aaa/aaa v2.0 second
        assert results[2]["instance_id"] == "zoo__zoo-1"  # zoo/zoo last

    def test_count(self, tmp_path: Path):
        data = [
            {"instance_id": "django__django-11039"},
            {"instance_id": "flask__flask-1000"},
            {"instance_id": "requests__requests-1234"},
        ]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(data))
        cache = DatasetCache(cache_file)
        assert cache.count == 3

    def test_save(self, tmp_path: Path):
        cache_file = tmp_path / "cache.json"
        cache = DatasetCache(cache_file)
        data = [{"instance_id": "django__django-11039", "repo": "django/django"}]
        cache.save(data)
        loaded = json.loads(cache_file.read_text())
        assert len(loaded) == 1
        assert loaded[0]["instance_id"] == "django__django-11039"


class TestFetchAndCacheDataset:
    """Tests for fetch_and_cache_dataset function."""

    def test_fetch_returns_data(self, tmp_path: Path):
        """Test that fetch_and_cache_dataset returns the dataset."""
        mock_data = [
            {"instance_id": "django__django-11039", "repo": "django/django"},
        ]
        with patch("swebench_orchestrator.dataset._fetch_from_hf") as mock_fetch:
            mock_fetch.return_value = mock_data
            cache_file = tmp_path / "cache.json"
            result = fetch_and_cache_dataset(cache_file, "test-dataset")
            assert result == mock_data

    def test_fetch_caches_data(self, tmp_path: Path):
        """Test that fetch_and_cache_dataset saves to cache file."""
        mock_data = [
            {"instance_id": "django__django-11039", "repo": "django/django"},
        ]
        with patch("swebench_orchestrator.dataset._fetch_from_hf") as mock_fetch:
            mock_fetch.return_value = mock_data
            cache_file = tmp_path / "cache.json"
            fetch_and_cache_dataset(cache_file, "test-dataset")
            assert cache_file.exists()
            loaded = json.loads(cache_file.read_text())
            assert len(loaded) == 1

    def test_fetch_uses_existing_cache_when_valid(self, tmp_path: Path):
        """Test that valid cached data is reused."""
        existing_data = [
            {"instance_id": "django__django-11039", "repo": "django/django"},
        ]
        cache_file = tmp_path / "cache.json"
        cache_file.write_text(json.dumps(existing_data))

        with patch("swebench_orchestrator.dataset._fetch_from_hf") as mock_fetch:
            result = fetch_and_cache_dataset(cache_file, "test-dataset")
            # Should NOT call HF since cache is valid
            mock_fetch.assert_not_called()
            assert result == existing_data

    def test_fetch_replaces_invalid_cache(self, tmp_path: Path):
        """Test that invalid cached data triggers a fresh fetch."""
        cache_file = tmp_path / "cache.json"
        cache_file.write_text("invalid json")

        mock_data = [
            {"instance_id": "django__django-11039", "repo": "django/django"},
        ]
        with patch("swebench_orchestrator.dataset._fetch_from_hf") as mock_fetch:
            mock_fetch.return_value = mock_data
            result = fetch_and_cache_dataset(cache_file, "test-dataset")
            mock_fetch.assert_called_once()
            assert result == mock_data

    def test_fetch_raises_on_hf_failure(self, tmp_path: Path):
        """Test that HF fetch failures are propagated."""
        cache_file = tmp_path / "cache.json"

        with patch("swebench_orchestrator.dataset._fetch_from_hf") as mock_fetch:
            mock_fetch.side_effect = RuntimeError("HF API error")
            with pytest.raises(RuntimeError, match="HF API error"):
                fetch_and_cache_dataset(cache_file, "test-dataset")
