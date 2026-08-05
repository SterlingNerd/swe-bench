"""Unit tests for configuration."""

import os
from pathlib import Path

import pytest

from swebench_orchestrator.config import Config


class TestConfig:
    """Tests for the Config class."""

    def test_defaults(self, tmp_path: Path):
        config = Config(repo_root=tmp_path)
        assert config.agents_dir == tmp_path / "agents"
        assert config.workspace_dir == tmp_path / "workspace"
        assert config.output_dir == tmp_path / "workspace" / "outputs"
        assert config.runs_dir == tmp_path / "runs"
        assert config.hf_dataset == "princeton-nlp/SWE-bench_Verified"
        assert config.max_storage_pct == 80.0
        assert config.swebench_registry == "swebench"

    def test_custom_cache_file(self, tmp_path: Path):
        config = Config(repo_root=tmp_path, cache_file="/custom/cache.json")
        assert config.cache_file == "/custom/cache.json"

    def test_custom_storage_threshold(self, tmp_path: Path):
        config = Config(repo_root=tmp_path, max_storage_pct=90.0)
        assert config.max_storage_pct == 90.0

    def test_docker_run_flags(self, tmp_path: Path):
        config = Config(repo_root=tmp_path)
        flags = config.docker_run_flags
        assert "--memory" in flags
        assert "32g" in flags
        assert "--pids-limit" in flags
        assert "500" in flags
        assert "--cap-drop" in flags
        assert "ALL" in flags

    def test_from_env(self, tmp_path: Path):
        config = Config.from_env(tmp_path)
        assert config.repo_root == tmp_path.resolve()

    def test_workspace_dir_from_env(self, tmp_path: Path):
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("SWE_WORKSPACE_DIR", str(tmp_path / "custom_ws"))
            config = Config(repo_root=tmp_path)
            assert config.workspace_dir == tmp_path / "custom_ws"
            assert config.output_dir == tmp_path / "custom_ws" / "outputs"

    def test_log_file_location(self, tmp_path: Path):
        config = Config(repo_root=tmp_path)
        assert config.log_file == tmp_path / "workspace" / "run.log"

    def test_swebench_py_path(self, tmp_path: Path):
        config = Config(repo_root=tmp_path)
        assert config.swebench_py == tmp_path / ".venv" / "swebench" / "bin" / "python"

    def test_frozen(self, tmp_path: Path):
        """Config should be immutable (frozen dataclass)."""
        config = Config(repo_root=tmp_path)
        with pytest.raises(Exception):  # dataclass frozen raises FrozenInstanceError
            config.hf_dataset = "something-else"
