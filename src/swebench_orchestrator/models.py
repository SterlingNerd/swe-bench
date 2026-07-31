"""Data models for the SWE-bench orchestrator."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class Instance(BaseModel):
    """A single SWE-bench instance from the dataset."""

    model_config = ConfigDict(populate_by_name=True)

    instance_id: str
    repo: str
    version: str
    testbed: str
    problem_statement: str
    hint_string: str
    base_commit: str
    patch: str
    test_patch: str
    failure_log: str
    created_at: str
    difficulty: str
    environment_commit_hash: str
    repo_directory: str

    @property
    def repo_image_name(self) -> str:
        """Convert repo to image-safe name (slashes → underscores)."""
        return self.repo.replace("/", "_")


class InstanceSummary(BaseModel):
    """Lightweight summary for listing instances."""

    instance_id: str
    repo: str
    version: str
    difficulty: str

    @classmethod
    def from_instance(cls, inst: Instance) -> InstanceSummary:
        return cls(
            instance_id=inst.instance_id,
            repo=inst.repo,
            version=inst.version,
            difficulty=inst.difficulty,
        )


class RunManifest(BaseModel):
    """Manifest for a single run, tracking provenance and state."""

    model_config = ConfigDict(populate_by_name=True)

    run_id: str
    agent: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dataset_hash: str = ""
    timeout: int = 3600
    profile: str = "default"
    commit_hash: str = ""


class Attempt(BaseModel):
    """A single attempt within a run for one instance."""

    model_config = ConfigDict(populate_by_name=True)

    attempt_id: str  # e.g., "attempt-001"
    instance_id: str
    status: str = "pending"  # pending, running, completed, failed, timed_out, agent_error, container_error
    patch_bytes: int = 0
    elapsed_seconds: int = 0
    container_exit_code: int = 0
    local_eval: Optional[str] = None  # resolved, failed, error, None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class Result(BaseModel):
    """Result record written to each attempt directory."""

    model_config = ConfigDict(populate_by_name=True)

    status: str = "patch_collected"
    patch_bytes: int = 0
    elapsed_seconds: int = 0
    container_exit_code: int = 0
    local_eval: Optional[str] = None


class Summary(BaseModel):
    """Aggregated summary for an agent across all instances."""

    agent: str
    total: int = 0
    resolved: int = 0
    failed: int = 0
    errored: int = 0
    no_patch: int = 0
    timed_out: int = 0
    agent_errors: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list)


def compute_storage_info(usage_pct: float, threshold_pct: float = 80.0) -> dict[str, Any]:
    """Compute storage status from raw percentages."""
    is_warning = usage_pct >= threshold_pct
    is_critical = usage_pct >= 90.0
    return {
        "usage_pct": usage_pct,
        "threshold_pct": threshold_pct,
        "is_warning": is_warning,
        "is_critical": is_critical,
    }


def instance_to_image_name(instance_id: str, arch: str = "x86_64", registry: str = "swebench") -> str:
    """Convert instance_id to swebench image name.

    django__django-11039 → swebench/sweb.eval.x86_64.django_1776_django-11039:latest
    """
    repo_part = instance_id.split("__")[0]
    issue_part = instance_id.split("__", 1)[1]
    repo_image_name = repo_part.replace("/", "_")
    return f"{registry}/sweb.eval.{arch}.{repo_image_name}_1776_{issue_part}:latest"


def read_json(path: Path) -> dict[str, Any]:
    """Read and parse a JSON file."""
    with open(path) as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write data to a JSON file with indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
