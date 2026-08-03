"""Integration tests for dataset operations with real file I/O."""

import json
from pathlib import Path

from swebench_orchestrator.dataset import DatasetCache, fetch_and_cache_dataset


class TestDatasetCacheIntegration:
    """Integration tests for DatasetCache with real file operations."""

    def test_full_lifecycle(self, test_workspace):
        """Test complete cache lifecycle: create, validate, query, save."""
        cache_file = test_workspace / "cache.json"

        # Start with invalid cache
        cache = DatasetCache(cache_file)
        assert cache.is_valid is False

        # Save valid data
        data = [
            {"instance_id": "django__django-11039", "repo": "django/django", "version": "4.2"},
            {"instance_id": "flask__flask-1000", "repo": "pallets/flask", "version": "2.3"},
        ]
        cache.save(data)

        # Now valid
        assert cache.is_valid is True
        assert cache.count == 2

        # Query works
        result = cache.get_instance("django__django-11039")
        assert result is not None
        assert result["repo"] == "django/django"

        # List with filter
        results = list(cache.list_instances(filter_str="flask"))
        assert len(results) == 1

    def test_cache_corruption_recovery(self, test_workspace):
        """Test that corrupted cache is detected and can be replaced."""
        cache_file = test_workspace / "cache.json"

        # Write valid data
        cache = DatasetCache(cache_file)
        cache.save([{"instance_id": "test__test-1"}])
        assert cache.is_valid is True

        # Corrupt the file
        cache_file.write_text("corrupted!!!")
        assert cache.is_valid is False

        # Replace with valid data
        cache.save([{"instance_id": "new__new-1"}])
        assert cache.is_valid is True
        assert cache.count == 1

    def test_cache_persists_across_instances(self, test_workspace):
        """Test that cache data persists when DatasetCache is re-instantiated."""
        cache_file = test_workspace / "cache.json"

        # Save data
        cache1 = DatasetCache(cache_file)
        cache1.save([{"instance_id": "django__django-11039"}])

        # Re-open cache
        cache2 = DatasetCache(cache_file)
        assert cache2.is_valid is True
        assert cache2.count == 1
        assert cache2.get_instance("django__django-11039") is not None

    def test_list_sorted_by_repo_version(self, test_workspace):
        """Test that list_instances returns sorted results."""
        cache_file = test_workspace / "cache.json"
        data = [
            {"instance_id": "zoo__zoo-1", "repo": "zoo/zoo", "version": "2.0"},
            {"instance_id": "aaa__aaa-2", "repo": "aaa/aaa", "version": "2.0"},
            {"instance_id": "aaa__aaa-1", "repo": "aaa/aaa", "version": "1.0"},
        ]
        cache = DatasetCache(cache_file)
        cache.save(data)

        results = list(cache.list_instances())
        assert results[0]["instance_id"] == "aaa__aaa-1"  # aaa v1.0 first
        assert results[1]["instance_id"] == "aaa__aaa-2"  # aaa v2.0 second
        assert results[2]["instance_id"] == "zoo__zoo-1"  # zoo last

    def test_filter_case_insensitive(self, test_workspace):
        """Test that filtering is case-insensitive."""
        cache_file = test_workspace / "cache.json"
        data = [
            {"instance_id": "Django__django-11039", "repo": "django/django"},
            {"instance_id": "Flask__flask-1000", "repo": "pallets/flask"},
        ]
        cache = DatasetCache(cache_file)
        cache.save(data)

        # Lowercase filter
        results = list(cache.list_instances(filter_str="django"))
        assert len(results) == 1

        # Uppercase filter
        results = list(cache.list_instances(filter_str="FLASK"))
        assert len(results) == 1

    def test_fetch_and_cache_uses_valid_cache(self, test_workspace):
        """Test that fetch_and_cache_dataset reuses valid cache."""
        cache_file = test_workspace / "cache.json"
        existing_data = [{"instance_id": "django__django-11039"}]
        cache_file.write_text(json.dumps(existing_data))

        with __import__("unittest.mock").mock.patch(
            "swebench_orchestrator.dataset._fetch_from_hf"
        ) as mock_fetch:
            result = fetch_and_cache_dataset(cache_file)
            mock_fetch.assert_not_called()  # Should not call HF
            assert result == existing_data

    def test_fetch_and_cache_replaces_invalid(self, test_workspace):
        """Test that invalid cache triggers fresh fetch."""
        cache_file = test_workspace / "cache.json"
        cache_file.write_text("invalid")

        new_data = [{"instance_id": "new__new-1"}]
        with __import__("unittest.mock").mock.patch(
            "swebench_orchestrator.dataset._fetch_from_hf", return_value=new_data
        ) as mock_fetch:
            result = fetch_and_cache_dataset(cache_file)
            mock_fetch.assert_called_once()
            assert result == new_data
            assert cache_file.exists()
