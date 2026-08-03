"""Run manifest infrastructure — P1: manifest-based runs with attempt isolation.

Provides:
- Run manifests with provenance tracking
- Immutable attempt directories
- Scoped cleanup of partial attempts
- Event export for audit logging
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from swebench_orchestrator.models import (
    Attempt,
    Result,
    RunManifest,
    read_json,
    write_json,
)

logger = logging.getLogger(__name__)


def create_run_manifest(
    runs_dir: Path,
    agent: str,
    timeout: int = 3600,
    profile: str = "default",
    dataset_hash: str = "",
    commit_hash: str = "",
) -> RunManifest:
    """Create a new run manifest.

    Args:
        runs_dir: Root directory for all runs.
        agent: Agent name.
        timeout: Default timeout per instance in seconds.
        profile: Configuration profile name.
        dataset_hash: Hash of the dataset used.
        commit_hash: Git commit hash of the orchestrator.

    Returns:
        The created RunManifest.
    """
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    manifest_dir = runs_dir / run_id
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest = RunManifest(
        run_id=run_id,
        agent=agent,
        timeout=timeout,
        profile=profile,
        dataset_hash=dataset_hash,
        commit_hash=commit_hash,
    )

    write_json(manifest_dir / "manifest.json", manifest.model_dump())
    (manifest_dir / "tasks").mkdir(exist_ok=True)

    logger.info("Created run %s for agent %s", run_id, agent)
    return manifest


def resolve_run(
    runs_dir: Path,
    agent: str,
    run_id: Optional[str] = None,
) -> Optional[RunManifest]:
    """Resolve a run by ID or get the latest for an agent.

    Args:
        runs_dir: Root directory for all runs.
        agent: Agent name to filter by.
        run_id: Specific run ID (if None, gets latest).

    Returns:
        RunManifest if found, None otherwise.
    """
    runs = list(list_runs(runs_dir, agent=agent))
    if not runs:
        return None

    if run_id:
        for run in runs:
            if run.run_id == run_id:
                return run
        return None

    # Return latest (sorted by created_at descending)
    return max(runs, key=lambda r: r.created_at)


def list_runs(
    runs_dir: Path,
    agent: Optional[str] = None,
) -> Iterator[RunManifest]:
    """List all run manifests, optionally filtered by agent.

    Args:
        runs_dir: Root directory for all runs.
        agent: If provided, only return runs for this agent.

    Yields:
        RunManifest objects sorted by created_at descending.
    """
    if not runs_dir.is_dir():
        return

    manifests = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        manifest_file = run_dir / "manifest.json"
        if not manifest_file.exists():
            continue

        try:
            data = read_json(manifest_file)
            m = RunManifest(**data)
            if agent and m.agent != agent:
                continue
            manifests.append(m)
        except (json.JSONDecodeError, KeyError, ValueError):
            logger.warning("Invalid manifest in %s", run_dir)
            continue

    # Sort by created_at descending (newest first)
    for m in sorted(manifests, key=lambda x: x.created_at, reverse=True):
        yield m


class RunManager:
    """Manages run manifests and attempt directories.

    Provides a clean interface for:
    - Creating runs with manifests
    - Creating isolated attempts
    - Updating attempt results
    - Querying attempt state
    """

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir).resolve()

    def create_run(
        self,
        agent: str,
        timeout: int = 3600,
        profile: str = "default",
        dataset_hash: str = "",
        commit_hash: str = "",
    ) -> RunManifest:
        """Create a new run.

        Args:
            agent: Agent name.
            timeout: Default timeout per instance.
            profile: Configuration profile.
            dataset_hash: Dataset hash for provenance.
            commit_hash: Git commit hash.

        Returns:
            The created RunManifest.
        """
        return create_run_manifest(
            runs_dir=self.runs_dir,
            agent=agent,
            timeout=timeout,
            profile=profile,
            dataset_hash=dataset_hash,
            commit_hash=commit_hash,
        )

    def resolve_run(
        self,
        agent: str,
        run_id: Optional[str] = None,
    ) -> Optional[RunManifest]:
        """Resolve a run by ID or get the latest.

        Args:
            agent: Agent name.
            run_id: Specific run ID (optional).

        Returns:
            RunManifest if found.
        """
        return resolve_run(self.runs_dir, agent, run_id)

    def create_attempt(
        self,
        run_id: str,
        instance_id: str,
    ) -> Attempt:
        """Create a new attempt directory for an instance.

        Creates an immutable attempt directory under the run's tasks.
        Attempt IDs are sequential (attempt-001, attempt-002, ...).

        Args:
            run_id: The run this attempt belongs to.
            instance_id: The SWE-bench instance ID.

        Returns:
            The created Attempt object.
        """
        run_dir = self.runs_dir / run_id
        if not run_dir.is_dir():
            raise ValueError(f"Run {run_id} not found")

        tasks_dir = run_dir / "tasks" / instance_id
        tasks_dir.mkdir(parents=True, exist_ok=True)

        # Find next attempt number
        existing_attempts = sorted(
            [d for d in tasks_dir.iterdir() if d.is_dir() and d.name.startswith("attempt-")],
            key=lambda d: int(d.name.split("-")[1]),
        )
        next_num = len(existing_attempts) + 1
        attempt_id = f"attempt-{next_num:03d}"

        attempt_dir = tasks_dir / attempt_id
        attempt_dir.mkdir(exist_ok=True)

        # Write initial meta.json
        write_json(attempt_dir / "meta.json", {
            "attempt_id": attempt_id,
            "instance_id": instance_id,
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        logger.info("Created %s/%s/%s", run_id, instance_id, attempt_id)
        return Attempt(
            attempt_id=attempt_id,
            instance_id=instance_id,
        )

    def update_attempt_result(
        self,
        run_id: str,
        attempt_id: str,
        status: str = "completed",
        patch_bytes: int = 0,
        elapsed_seconds: int = 0,
        container_exit_code: int = 0,
        local_eval: Optional[str] = None,
    ) -> Path:
        """Update an attempt's result.json.

        Args:
            run_id: The run ID.
            attempt_id: The attempt ID.
            status: Result status.
            patch_bytes: Size of the generated patch.
            elapsed_seconds: Time taken.
            container_exit_code: Container exit code.
            local_eval: Local evaluation result (resolved/failed/error).

        Returns:
            Path to the result.json file.
        """
        run_dir = self.runs_dir / run_id
        if not run_dir.is_dir():
            raise ValueError(f"Run {run_id} not found")

        # Find the attempt directory by searching all task dirs
        attempt_dir = None
        for task_dir in run_dir.glob("tasks/*/"):
            candidate = task_dir / attempt_id
            if candidate.is_dir():
                attempt_dir = candidate
                break

        if attempt_dir is None:
            # Create the directory structure
            attempt_dir = run_dir / "tasks" / attempt_id.replace("attempt-", "") / attempt_id
            attempt_dir.mkdir(parents=True, exist_ok=True)

        result_file = attempt_dir / "result.json"
        result = Result(
            status=status,
            patch_bytes=patch_bytes,
            elapsed_seconds=elapsed_seconds,
            container_exit_code=container_exit_code,
            local_eval=local_eval,
        )
        write_json(result_file, result.model_dump())
        return result_file

    def get_attempt_result(
        self,
        run_id: str,
        attempt_id: str,
    ) -> Optional[dict[str, Any]]:
        """Get an attempt's result data.

        Args:
            run_id: The run ID.
            attempt_id: The attempt ID.

        Returns:
            Result dict if found, None otherwise.
        """
        run_dir = self.runs_dir / run_id
        if not run_dir.is_dir():
            return None

        # Search for the result file
        for task_dir in run_dir.glob("tasks/*/"):
            result_file = task_dir / attempt_id / "result.json"
            if result_file.exists():
                try:
                    return read_json(result_file)
                except (json.JSONDecodeError, ValueError):
                    return None

        return None

    def list_attempts(
        self,
        run_id: str,
        instance_id: Optional[str] = None,
    ) -> Iterator[Attempt]:
        """List all attempts for a run, optionally filtered by instance.

        Args:
            run_id: The run ID.
            instance_id: If provided, only list attempts for this instance.

        Yields:
            Attempt objects.
        """
        run_dir = self.runs_dir / run_id
        if not run_dir.is_dir():
            return

        task_dirs = run_dir.glob("tasks/*/")
        for task_dir in task_dirs:
            if instance_id and task_dir.name != instance_id:
                continue
            for attempt_dir in sorted(task_dir.iterdir()):
                if not attempt_dir.is_dir() or not attempt_dir.name.startswith("attempt-"):
                    continue
                yield Attempt(
                    attempt_id=attempt_dir.name,
                    instance_id=task_dir.name,
                )


def cleanup_partial_attempts(
    runs_dir: Path,
    agent: Optional[str] = None,
    dry_run: bool = True,
) -> list[dict[str, str]]:
    """List or remove incomplete attempt directories.

    An attempt is considered incomplete if it lacks both result.json and patch.diff.

    Args:
        runs_dir: Root directory for all runs.
        agent: If provided, only clean up runs for this agent.
        dry_run: If True, list incomplete attempts without removing them.

    Returns:
        List of dicts with 'run_id', 'instance_id', 'attempt_id' for each incomplete attempt.
    """
    incomplete = []
    removed = []

    for manifest in list_runs(runs_dir, agent=agent):
        run_dir = runs_dir / manifest.run_id
        tasks_dir = run_dir / "tasks"

        if not tasks_dir.is_dir():
            continue

        for instance_dir in tasks_dir.iterdir():
            if not instance_dir.is_dir():
                continue
            for attempt_dir in instance_dir.iterdir():
                if not attempt_dir.is_dir() or not attempt_dir.name.startswith("attempt-"):
                    continue

                result_file = attempt_dir / "result.json"
                patch_file = attempt_dir / "patch.diff"

                # Incomplete if missing result.json (patch.diff alone is insufficient)
                if not result_file.exists():
                    entry = {
                        "run_id": manifest.run_id,
                        "instance_id": instance_dir.name,
                        "attempt_id": attempt_dir.name,
                        "path": str(attempt_dir),
                    }
                    incomplete.append(entry)

                    if not dry_run:
                        import shutil
                        shutil.rmtree(attempt_dir)
                        removed.append(entry)

    if dry_run:
        logger.info("Found %d incomplete attempts (dry run)", len(incomplete))
    else:
        logger.info("Removed %d incomplete attempts", len(removed))

    return removed if not dry_run else incomplete


def export_events(
    runs_dir: Path,
    agent: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Export an audit log of all events across runs.

    Args:
        runs_dir: Root directory for all runs.
        agent: If provided, only export events for this agent.

    Returns:
        List of event dicts sorted by timestamp.
    """
    events = []

    for manifest in list_runs(runs_dir, agent=agent):
        run_dir = runs_dir / manifest.run_id

        # Run creation event
        events.append({
            "timestamp": manifest.created_at,
            "event": "run_created",
            "run_id": manifest.run_id,
            "agent": manifest.agent,
            "timeout": manifest.timeout,
            "profile": manifest.profile,
        })

        # Attempt events
        for attempt in list_attempts(run_dir):
            result = None
            result_file = run_dir / "tasks" / attempt.instance_id / attempt.attempt_id / "result.json"
            if result_file.exists():
                try:
                    result = read_json(result_file)
                except (json.JSONDecodeError, ValueError):
                    pass

            events.append({
                "timestamp": manifest.created_at,  # Use run creation as proxy
                "event": "attempt_created",
                "run_id": manifest.run_id,
                "instance_id": attempt.instance_id,
                "attempt_id": attempt.attempt_id,
                "status": result.get("status") if result else "pending",
            })

    # Sort by timestamp
    events.sort(key=lambda e: e.get("timestamp", ""))
    return events
