"""Configuration management for the SWE-bench orchestrator."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Config:
    """Immutable configuration for the orchestrator.

    Mirrors the shell variables from run.sh but in a Python-friendly way.
    """

    repo_root: Path
    agents_dir: Path = field(init=False)
    workspace_dir: Path = field(init=False)
    output_dir: Path = field(init=False)
    runs_dir: Path = field(init=False)
    cache_file: Path = field(default="/tmp/swe_verified_cache.json")
    hf_dataset: str = "princeton-nlp/SWE-bench_Verified"
    max_storage_pct: float = 80.0
    swebench_venv: Path = field(default=".venv/swebench")
    swebench_py: Path = field(init=False)
    log_file: Path = field(init=False)
    lock_file: Path = field(default="/tmp/swe-bench-run.lock")
    swebench_registry: str = "swebench"

    def __post_init__(self) -> None:
        object.__setattr__(self, "agents_dir", self.repo_root / "agents")
        workspace = Path(os.environ.get("SWE_WORKSPACE_DIR", str(self.repo_root / "workspace")))
        object.__setattr__(self, "workspace_dir", workspace)
        object.__setattr__(self, "output_dir", self.workspace_dir / "outputs")
        object.__setattr__(self, "runs_dir", self.repo_root / "runs")
        object.__setattr__(self, "swebench_py", self.repo_root / self.swebench_venv / "bin" / "python")
        object.__setattr__(self, "log_file", self.workspace_dir / "run.log")

    @classmethod
    def from_env(cls, repo_root: Optional[Path] = None) -> Config:
        """Create a Config from environment and defaults.

        Args:
            repo_root: Override the repository root. Defaults to parent of this file's directory.
        """
        if repo_root is None:
            # Default: one level up from this package
            import swebench_orchestrator
            pkg_dir = Path(swebench_orchestrator.__file__).parent.parent.parent
            repo_root = pkg_dir.resolve()
        return cls(repo_root=repo_root)

    @property
    def docker_run_flags(self) -> list[str]:
        """Default Docker run flags for agent containers."""
        return [
            "--memory", "32g",
            "--memory-swap", "64g",
            "--pids-limit", "500",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=2g",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--add-host", "host.docker.internal:host-gateway",
        ]
