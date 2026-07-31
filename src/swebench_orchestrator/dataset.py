"""Dataset operations — fetch, cache, and query SWE-bench instances."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


class DatasetCache:
    """Manages a local JSON cache of SWE-bench instances.

    Validates cache integrity on each access and provides query operations.
    """

    def __init__(self, cache_file: Path) -> None:
        self.cache_file = Path(cache_file)
        self._data: Optional[list[dict[str, Any]]] = None

    @property
    def data(self) -> list[dict[str, Any]]:
        """Load and return cached data. Raises if cache is invalid."""
        if self._data is not None:
            return self._data
        self._load()
        return self._data  # type: ignore[return-value]

    @property
    def is_valid(self) -> bool:
        """Check if the cache file exists and contains valid data."""
        if not self.cache_file.exists():
            return False
        try:
            content = self.cache_file.read_text()
            if not content.strip():
                return False
            data = json.loads(content)
            if not isinstance(data, list) or len(data) == 0:
                return False
            return True
        except (json.JSONDecodeError, ValueError):
            return False

    @property
    def count(self) -> int:
        """Number of instances in the cache."""
        return len(self.data)

    def _load(self) -> None:
        """Load data from cache file."""
        content = self.cache_file.read_text()
        self._data = json.loads(content)
        if not isinstance(self._data, list):
            raise ValueError("Cache file does not contain a JSON array")

    def get_instance(self, instance_id: str) -> Optional[dict[str, Any]]:
        """Find a single instance by ID.

        Returns None if not found.
        """
        for inst in self.data:
            if inst.get("instance_id") == instance_id:
                return inst
        return None

    def list_instances(
        self,
        filter_str: Optional[str] = None,
    ) -> Iterator[dict[str, Any]]:
        """List instances, optionally filtered.

        Results are sorted by (repo, version). Filter is case-insensitive
        substring match against any field value.
        """
        results = self.data
        if filter_str:
            filter_lower = filter_str.lower()
            results = [
                inst for inst in results
                if any(filter_lower in str(v).lower() for v in inst.values())
            ]
        # Sort by repo, then version
        for inst in sorted(results, key=lambda x: (x.get("repo", ""), x.get("version", ""))):
            yield inst

    def save(self, data: list[dict[str, Any]]) -> None:
        """Save data to the cache file."""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(data, indent=2))
        self._data = data


def _fetch_from_hf(dataset_name: str) -> list[dict[str, Any]]:
    """Fetch dataset from HuggingFace.

    Uses a Docker container with python:3.10-slim to avoid dependency issues.
    Falls back to direct Python if Docker is unavailable.
    """
    try:
        return _fetch_from_hf_docker(dataset_name)
    except Exception:
        logger.warning("Docker fetch failed, trying direct Python fetch...")
        return _fetch_from_hf_direct(dataset_name)


def _fetch_from_hf_docker(dataset_name: str) -> list[dict[str, Any]]:
    """Fetch dataset using a Docker container."""
    import subprocess

    cmd = [
        "docker", "run", "--rm",
        "-e", f"HF_DATASET={dataset_name}",
        "python:3.10-slim",
        "bash", "-c",
        """pip install -q datasets >/dev/null 2>&1
python3 << 'PYEOF'
from datasets import load_dataset
import json, os
ds = load_dataset(os.environ["HF_DATASET"], split="test")
data = [dict(i) for i in ds]
print(json.dumps(data))
PYEOF
""",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"Dataset fetch failed: {result.stderr}")

    data = json.loads(result.stdout)
    if not isinstance(data, list):
        raise ValueError("Unexpected dataset format from HuggingFace")
    return data


def _fetch_from_hf_direct(dataset_name: str) -> list[dict[str, Any]]:
    """Fetch dataset directly via Python (no Docker)."""
    from datasets import load_dataset

    ds = load_dataset(dataset_name, split="test")
    data = [dict(i) for i in ds]
    return data


def fetch_and_cache_dataset(
    cache_file: Path,
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
) -> list[dict[str, Any]]:
    """Fetch dataset from HuggingFace and cache it.

    Uses existing cache if valid. Re-fetches if cache is missing, empty, or corrupted.

    Args:
        cache_file: Path to the local cache file.
        dataset_name: HuggingFace dataset identifier.

    Returns:
        List of instance dictionaries.

    Raises:
        RuntimeError: If fetching from HuggingFace fails.
    """
    cache = DatasetCache(cache_file)

    if cache.is_valid:
        logger.info("Using cached dataset (%d instances)", cache.count)
        return cache.data

    logger.info("Fetching dataset from HuggingFace: %s", dataset_name)
    data = _fetch_from_hf(dataset_name)

    if not data:
        raise RuntimeError("Dataset fetch returned empty results")

    cache.save(data)
    logger.info("Cached %d instances to %s", len(data), cache_file)
    return data
