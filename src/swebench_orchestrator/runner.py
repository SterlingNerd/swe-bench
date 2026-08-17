"""Runner module — orchestrates agent runs against SWE-bench instances.

Provides:
- run_instance() for single instance execution
- summarize_results() for result aggregation
- Runner class combining all operations
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from swebench_orchestrator.config import Config
from swebench_orchestrator.dataset import DatasetCache
from swebench_orchestrator.docker_ops import ContainerResult, DockerOps
from swebench_orchestrator.manifest import RunManager
from swebench_orchestrator.models import (
    Instance,
    RunManifest,
    Summary,
    compute_storage_info,
    instance_to_image_name,
    read_json,
    write_json,
)
from swebench_orchestrator.storage import check_storage

logger = logging.getLogger(__name__)


def _normalize_local_eval(local_eval: Any) -> Optional[str]:
    """Normalize local_eval to a string value.

    Handles both formats:
    - New string format: "resolved", "failed", "error"
    - Legacy dict format: {"resolved": true/false, "error": true/false}

    Returns:
        "resolved", "failed", "error", or None if not determinable.
    """
    if isinstance(local_eval, str):
        return local_eval
    if isinstance(local_eval, dict):
        if local_eval.get("error"):
            return "error"
        if local_eval.get("resolved"):
            return "resolved"
        return "failed"
    return None


def run_instance(
    agents_dir: Path,
    agent: str,
    instance_id: str,
    output_dir: Path,
    timeout: int = 3600,
    cache_file: Optional[Path] = None,
    docker_ops: Optional[DockerOps] = None,
    registry: str = "swebench",
    threshold_pct: float = 80.0,
) -> dict[str, Any]:
    """Run an agent against a single SWE-bench instance.

    This is the core "work" phase function that:
    1. Validates inputs (agent, bundle, instance)
    2. Checks disk space
    3. Pulls/loads the swebench image
    4. Runs the agent container
    5. Copies outputs back
    6. Records results

    Args:
        agents_dir: Path to agents directory.
        agent: Agent name (directory under harnesses/).
        instance_id: SWE-bench instance ID.
        output_dir: Base output directory.
        timeout: Maximum runtime in seconds.
        cache_file: Path to dataset cache file.
        docker_ops: DockerOps instance (for testing).
        registry: Docker registry prefix for swebench images.
        threshold_pct: Disk usage warning threshold percentage.

    Returns:
        Dict with run results including status, elapsed_seconds, etc.

    Raises:
        ValueError: If agent, bundle, or instance not found.
    """
    if not isinstance(timeout, int) or timeout < 0:
        raise ValueError(f"Timeout must be a non-negative integer, got {timeout!r}")

    if docker_ops is None:
        docker_ops = DockerOps()

    # Validate agent exists
    agents_dir = Path(agents_dir).resolve()
    agent_dir = agents_dir / agent
    if not agent_dir.is_dir():
        available = []
        if agents_dir.is_dir():
            available = [d.name for d in agents_dir.iterdir() if d.is_dir() and d.name != "base"]
        raise ValueError(
            f"Agent '{agent}' not found. Available agents: {', '.join(available) or 'none'}"
        )

    # Validate bundle exists
    bundle_dir = agent_dir.resolve() / "bundle"
    if not bundle_dir.is_dir():
        raise ValueError(f"Agent bundle not found at {bundle_dir}. Run --build {agent} first.")

    # Get instance data from cache
    if cache_file is None:
        cache_file = Path("/tmp/swe_verified_cache.json")

    dataset_cache = DatasetCache(cache_file)
    inst_data = dataset_cache.get_instance(instance_id)
    if inst_data is None:
        raise ValueError(f"Instance not found: {instance_id}")

    repo_url = inst_data["repo"]
    base_commit = inst_data["base_commit"]
    problem_statement = inst_data["problem_statement"]

    # Determine swebench image
    image_name = instance_to_image_name(instance_id, registry=registry)

    # Check storage
    storage_status = check_storage(output_dir, threshold_pct=threshold_pct)
    if storage_status["is_warning"]:
        logger.warning("Disk at %s%% (threshold: %s%%)", storage_status["usage_pct"], storage_status["threshold_pct"])

    # Pull image if needed
    if not docker_ops.image_exists(image_name):
        logger.info("Pulling swebench image: %s", image_name)
        if not docker_ops.pull_image(image_name):
            raise RuntimeError(f"Failed to pull image: {image_name}")

    # Prepare output directory - only create parent dirs, not instance dir.
    # The container creates the instance dir (as root), then we fix permissions after copy.
    # Pre-creating the instance dir causes permission issues: host creates it, container
    # (root) can't create subdirs inside due to Docker/WSL restrictions.
    output_dir_resolved = Path(output_dir).resolve()
    agent_output_root = output_dir_resolved / agent
    agent_output_root.mkdir(parents=True, exist_ok=True)
    instance_output_dir = agent_output_root / instance_id

    container_name = f"swe_{agent}_{instance_id}"

    # Release any stale container from previous interrupted run
    docker_ops.release_container(container_name)

    # Build Docker command
    started_at = time.time()
    import os as _os
    docker_flags = [
        "--memory", "32g",
        "--memory-swap", "64g",
        "--pids-limit", "500",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=2g",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--add-host", "host.docker.internal:host-gateway",
        "-e", f"SWE_AGENT_NAME={agent}",
        "-e", f"SWE_OUTPUT_ROOT=/workspace/outputs/{agent}",
        "-v", f"{bundle_dir}:/agent:ro",
        "-v", f"{output_dir_resolved}:/workspace/outputs",
        # Run as host user to avoid permission issues with mounted volumes
        "--user", f"{_os.getuid()}:{_os.getgid()}",
    ]

    command = [
        "/agent/entrypoint.sh",
        instance_id,
        f"https://github.com/{repo_url}",
        base_commit,
        problem_statement,
    ]

    logger.info(
        "[WORK] Running: %s against %s (image: %s)",
        agent, instance_id, image_name,
    )

    # Run the container
    result = docker_ops.run_container(
        image_name=image_name,
        container_name=container_name,
        flags=docker_flags,
        command=command,
        timeout_seconds=timeout,
    )

    elapsed = int(time.time() - started_at)

    # Handle timeout
    if result.status == "timed_out":
        docker_ops.remove_container(container_name)
        if not instance_output_dir.exists():
            instance_output_dir.mkdir(parents=True, exist_ok=True)
        record_result(
            instance_output_dir / "result.json",
            status="timed_out",
            container_exit_code=result.exit_code,
            elapsed_seconds=elapsed,
        )
        logger.error("%s/%s timed out after %ds", agent, instance_id, timeout)
        return {
            "status": "timed_out",
            "exit_code": result.exit_code,
            "elapsed_seconds": elapsed,
        }

    # Handle container error
    if result.status == "error":
        docker_ops.remove_container(container_name)
        if not instance_output_dir.exists():
            instance_output_dir.mkdir(parents=True, exist_ok=True)
        record_result(
            instance_output_dir / "result.json",
            status="container_error",
            container_exit_code=result.exit_code,
            elapsed_seconds=elapsed,
        )
        logger.error("%s/%s container exited with code %d", agent, instance_id, result.exit_code)
        return {
            "status": "container_error",
            "exit_code": result.exit_code,
            "elapsed_seconds": elapsed,
        }

    # Copy outputs before removing container
    cp_tmp = instance_output_dir.parent / f".tmp_{instance_id}"
    cp_ok = False

    try:
        container_state = docker_ops.inspect_container_state(container_name)
        logger.info("Container state after run: %s", container_state or "unknown")

        if docker_ops.copy_from_container(
            container_name,
            f"/workspace/outputs/{agent}/{instance_id}",
            cp_tmp,
        ):
            # Ensure instance output dir exists (container creates it in real runs,
            # but mocked tests need us to create it).
            instance_output_dir.mkdir(parents=True, exist_ok=True)

            # Flatten: docker cp nests the instance dir (single level).
            nested = cp_tmp / instance_id
            if nested.is_dir():
                for item in nested.iterdir():
                    dest = instance_output_dir / item.name
                    if dest.exists():
                        import shutil
                        if dest.is_dir():
                            shutil.rmtree(dest)
                        else:
                            dest.unlink()
                    item.rename(dest)
                cp_ok = True
            else:
                # Copy directly if no nesting (files placed directly in cp_tmp)
                for item in cp_tmp.iterdir():
                    dest = instance_output_dir / item.name
                    if dest.exists():
                        import shutil
                        if dest.is_dir():
                            shutil.rmtree(dest)
                        else:
                            dest.unlink()
                    item.rename(dest)
                cp_ok = True

        if not cp_ok:
            logger.warning("Copy succeeded but no output files found")

    finally:
        # Clean up temp dir
        if cp_tmp.exists():
            import shutil
            shutil.rmtree(cp_tmp, ignore_errors=True)

    # Remove container after copying outputs
    docker_ops.remove_container(container_name)

    if not cp_ok:
        logger.error("Failed to copy outputs from container")
        return {
            "status": "copy_failed",
            "exit_code": 1,
            "elapsed_seconds": elapsed,
        }

    # Fix ownership recursively (container runs as root, host user needs access)
    import os, stat
    try:
        for root, dirs, files in os.walk(str(instance_output_dir)):
            for d in dirs:
                os.chown(os.path.join(root, d), os.getuid(), os.getgid())
            for f in files:
                os.chown(os.path.join(root, f), os.getuid(), os.getgid())
    except OSError:
        pass

    # Check result.json for final status
    result_file = instance_output_dir / "result.json"
    final_status = "patch_collected"
    if result_file.exists():
        try:
            data = read_json(result_file)
            final_status = data.get("status", "unknown")
        except (json.JSONDecodeError, ValueError):
            pass

    # Update elapsed in result.json
    if result_file.exists():
        try:
            data = read_json(result_file)
            data["elapsed_seconds"] = elapsed
            write_json(result_file, data)
        except (json.JSONDecodeError, ValueError):
            pass

    return {
        "status": final_status,
        "exit_code": 0,
        "elapsed_seconds": elapsed,
    }


def record_result(
    result_file: Path,
    status: str = "patch_collected",
    container_exit_code: int = 0,
    elapsed_seconds: int = 0,
) -> None:
    """Record a result to a JSON file.

    Merges with existing data if the file exists.

    Args:
        result_file: Path to write the result.
        status: Result status.
        container_exit_code: Container exit code.
        elapsed_seconds: Elapsed time in seconds.
    """
    result_file.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if result_file.exists():
        try:
            existing = read_json(result_file)
        except (json.JSONDecodeError, ValueError):
            pass

    existing.update({
        "status": status,
        "container_exit_code": container_exit_code,
        "elapsed_seconds": elapsed_seconds,
    })
    existing.setdefault("patch_bytes", 0)

    write_json(result_file, existing)


def summarize_results(output_dir: Path, agent: Optional[str] = None) -> dict[str, Any]:
    """Summarize results for an agent's output directory.

    Args:
        output_dir: Agent's output directory (e.g., outputs/pi/).
        agent: Explicit agent name (defaults to parent dir name).

    Returns:
        Summary dict with counts and per-instance rows.
    """
    if agent is None:
        agent = output_dir.parent.name
    rows = []

    if not output_dir.is_dir():
        return {
            "agent": agent,
            "total": 0,
            "resolved": 0,
            "failed": 0,
            "errored": 0,
            "no_patch": 0,
            "timed_out": 0,
            "agent_errors": 0,
            "rows": [],
        }

    for instance_dir in sorted(output_dir.iterdir()):
        if not instance_dir.is_dir():
            continue

        iid = instance_dir.name
        # Skip special directories
        if iid in ("eval", "logs"):
            continue

        result_file = instance_dir / "result.json"
        if not result_file.exists():
            continue

        try:
            meta = read_json(result_file)
        except (json.JSONDecodeError, ValueError):
            continue

        rows.append({
            "instance_id": iid,
            "status": meta.get("status"),
            "patch_bytes": meta.get("patch_bytes", 0),
            "elapsed_seconds": meta.get("elapsed_seconds", 0),
            "local_eval": meta.get("local_eval"),
        })

    total = len(rows)
    # Normalize local_eval for both string and legacy dict formats
    normalized = [_normalize_local_eval(r.get("local_eval")) for r in rows]
    resolved = sum(1 for v in normalized if v == "resolved")
    failed = sum(1 for v in normalized if v == "failed")
    errored = sum(1 for v in normalized if v == "error")
    no_patch = sum(1 for r in rows if r["status"] == "no_patch")
    timed_out = sum(1 for r in rows if r["status"] == "timed_out")
    patch_collected = sum(1 for r in rows if r["status"] == "patch_collected")
    agent_errors = sum(
        1 for r in rows
        if r["status"] in ("agent_error", "container_error")
    )

    return {
        "agent": agent,
        "total": total,
        "resolved": resolved,
        "failed": failed,
        "errored": errored,
        "no_patch": no_patch,
        "timed_out": timed_out,
        "patch_collected": patch_collected,
        "agent_errors": agent_errors,
        "rows": rows,
    }


def generate_predictions(
    output_dir: Path,
    agent: str,
) -> tuple[Path, list[str]]:
    """Generate predictions.jsonl from instance patches.

    Args:
        output_dir: Agent's output directory.
        agent: Agent name (used as model_name_or_path).

    Returns:
        Tuple of (predictions_file_path, list_of_instance_ids).
    """
    preds_file = output_dir / "predictions.jsonl"
    instance_ids = []

    for d in sorted(output_dir.iterdir()):
        if not d.is_dir() or d.name in ("eval", "logs"):
            continue
        patch_file = d / "patch.diff"
        if patch_file.exists() and patch_file.stat().st_size > 0:
            instance_ids.append(d.name)

    with open(preds_file, "w") as f:
        for iid in instance_ids:
            patch_path = output_dir / iid / "patch.diff"
            patch_content = patch_path.read_text()
            f.write(json.dumps({
                "instance_id": iid,
                "model_name_or_path": agent,
                "model_patch": patch_content,
            }) + "\n")

    logger.info("Wrote %d predictions to %s", len(instance_ids), preds_file)
    return preds_file, instance_ids


def fold_harness_results(
    output_dir: Path,
    report_data: dict[str, Any],
) -> int:
    """Fold swebench harness results into each instance's result.json.

    Args:
        output_dir: Agent's output directory.
        report_data: Harness report with resolved_ids, unresolved_ids, error_ids.

    Returns:
        Number of instances updated.
    """
    resolved = set(report_data.get("resolved_ids", []))
    errored = set(report_data.get("error_ids", []))
    unresolved = set(report_data.get("unresolved_ids", []))

    folded = 0
    for iid in resolved | errored | unresolved:
        result_file = output_dir / iid / "result.json"
        if not result_file.exists():
            continue

        try:
            meta = read_json(result_file)
        except (json.JSONDecodeError, ValueError):
            continue

        if iid in resolved:
            meta["local_eval"] = "resolved"
            meta["status"] = "resolved"
        elif iid in errored:
            meta["local_eval"] = "error"
            meta["status"] = "error"
        else:
            meta["local_eval"] = "failed"
            meta["status"] = "failed"

        write_json(result_file, meta)
        folded += 1

    logger.info("Folded results for %d instances", folded)
    return folded


def find_harness_report_for_instance(
    output_dir: Path,
    instance_id: str,
    run_id: str,
) -> Optional[dict[str, Any]]:
    """Find the swebench harness report for a single-instance eval.

    When the harness is invoked with --run_id, it creates a report named
    {agent}.{run_id}.json. The harness writes to the current working directory
    (output_dir) rather than the --report_dir, so we check both locations.

    Args:
        output_dir: Agent's output directory.
        instance_id: The SWE-bench instance ID (for logging).
        run_id: The --run_id passed to the harness.

    Returns:
        Report dict if found, None otherwise.
    """
    eval_dir = output_dir / "eval"
    agent = output_dir.name
    # Try the actual harness naming pattern: {agent}.{run_id}.json
    # Check both output_dir (where harness actually writes) and eval_dir (--report_dir)
    candidates = [
        output_dir / f"{agent}.{run_id}.json",  # Actual harness location
        eval_dir / f"{agent}.{run_id}.json",    # Expected --report_dir location
        # Fallback to old expected pattern
        output_dir / f"{run_id}.{run_id}.json",
        eval_dir / f"{run_id}.{run_id}.json",
    ]
    # Also check newest JSON files in both dirs
    candidates.extend(sorted(output_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True))
    if eval_dir.is_dir():
        candidates.extend(sorted(eval_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True))

    for report_file in candidates:
        if report_file.exists():
            try:
                data = read_json(report_file)
                if "resolved_ids" in data and "unresolved_ids" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                logger.warning("Failed to read harness report: %s", report_file)
    return None


def find_harness_report(output_dir: Path) -> Optional[dict[str, Any]]:
    """Find the swebench harness report JSON file.

    Searches for reports in both the output directory (where harness actually writes)
    and the eval directory (--report_dir) with various naming patterns.
    Also combines individual per-instance reports (from run_eval_instance) if no
    combined batch report is found.

    Args:
        output_dir: Agent's output directory.

    Returns:
        Report dict if found, None otherwise.
    """
    eval_dir = output_dir / "eval"
    agent = output_dir.name

    # Try common report naming patterns in both locations (combined batch reports)
    candidates = [
        output_dir / f"{agent}.{agent}.json",        # Actual harness location
        eval_dir / f"{agent}.{agent}.json",          # Expected --report_dir location
        output_dir / f"{agent}__{agent}.json",
        eval_dir / f"{agent}__{agent}.json",
    ]

    # Also try newest JSON files in both dirs, but exclude individual per-instance reports
    # (pattern: {agent}.{instance_id}.json where instance_id != agent)
    def is_batch_report(f: Path) -> bool:
        return f.name in (f"{agent}.{agent}.json", f"{agent}__{agent}.json")
    
    for f in sorted(output_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if is_batch_report(f):
            candidates.append(f)
    if eval_dir.is_dir():
        for f in sorted(eval_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if is_batch_report(f):
                candidates.append(f)

    for report_file in candidates:
        if report_file.exists():
            try:
                data = read_json(report_file)
                if "resolved_ids" in data and "unresolved_ids" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                continue

    # Fallback: combine individual per-instance reports (pattern: {agent}.{instance_id}.json)
    # These are created by run_eval_instance for each instance
    individual_reports = list(output_dir.glob(f"{agent}.*.json"))
    if not individual_reports and eval_dir.is_dir():
        individual_reports = list(eval_dir.glob(f"{agent}.*.json"))
    
    if individual_reports:
        combined = {"resolved_ids": [], "unresolved_ids": [], "error_ids": []}
        for report_file in individual_reports:
            # Skip the combined batch report files
            if report_file.name in (f"{agent}.{agent}.json", f"{agent}__{agent}.json"):
                continue
            try:
                data = read_json(report_file)
                if "resolved_ids" in data and "unresolved_ids" in data:
                    combined["resolved_ids"].extend(data.get("resolved_ids", []))
                    combined["unresolved_ids"].extend(data.get("unresolved_ids", []))
                    combined["error_ids"].extend(data.get("error_ids", []))
            except (json.JSONDecodeError, ValueError):
                continue
        
        if combined["resolved_ids"] or combined["unresolved_ids"] or combined["error_ids"]:
            return combined

    return None


def run_eval(
    output_dir: Path,
    agent: str,
    dataset_name: str = "princeton-nlp/SWE-bench_Verified",
    swebench_py: Optional[Path] = None,
) -> dict[str, Any]:
    """Run swebench harness evaluation for an agent's patches.

    Args:
        output_dir: Agent's output directory.
        agent: Agent name.
        dataset_name: HuggingFace dataset name.
        swebench_py: Path to swebench Python (uses config if None).

    Returns:
        Dict with evaluation results.
    """
    # Generate predictions
    preds_file, instance_ids = generate_predictions(output_dir, agent)

    if not instance_ids:
        logger.warning("No patches found to evaluate")
        return {"status": "no_patches", "instances": 0}

    logger.info(
        "[EVAL] Running swebench harness on %d patch(es) for '%s'",
        len(instance_ids), agent,
    )

    # Run swebench harness
    if swebench_py is None:
        swebench_py = Path(".venv/swebench/bin/python").absolute()

    import subprocess

    report_dir = output_dir / "eval"
    report_dir.mkdir(exist_ok=True)

    cmd = [
        str(swebench_py),
        "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset_name,
        "--split", "test",
        "--predictions_path", str(preds_file.absolute()),
        "--max_workers", "1",
        "--cache_level", "instance",
        "--report_dir", str(report_dir.absolute()),
        "--run_id", agent,
        "-i" + ",".join(instance_ids),
    ]

    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(output_dir))

    if result.returncode != 0:
        logger.error("swebench harness failed with exit code %d", result.returncode)
        return {"status": "harness_error", "instances": len(instance_ids)}

    # Find and fold harness results
    report_data = find_harness_report(output_dir)
    folded = 0
    if report_data:
        folded = fold_harness_results(output_dir, report_data)
        logger.info("Folded %d harness results", folded)
    else:
        logger.warning("No harness report found, skipping result folding")

    return {
        "status": "completed",
        "instances": len(instance_ids),
        "folded": folded,
    }


class Runner:
    """High-level orchestrator combining all operations.

    Provides a clean interface for the full workflow:
    - Running instances (single or batch)
    - Evaluating patches
    - Summarizing results
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.docker_ops = DockerOps()
        self.run_manager = RunManager(config.runs_dir)

    def run_instance(
        self,
        agent: str,
        instance_id: str,
        timeout: int = 3600,
        run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run an agent against a single instance.

        Args:
            agent: Agent name.
            instance_id: SWE-bench instance ID.
            timeout: Maximum runtime in seconds.
            run_id: Run ID for manifest tracking (creates new if None).

        Returns:
            Result dict with status and timing.
        """
        # Resolve or create run
        if run_id:
            manifest = self.run_manager.resolve_run(agent, run_id)
            if manifest is None:
                raise ValueError(f"Run {run_id} not found for agent {agent}")
        else:
            manifest = self.run_manager.create_run(agent=agent, timeout=timeout)
            run_id = manifest.run_id

        # Create attempt
        attempt = self.run_manager.create_attempt(run_id, instance_id)

        # Run the instance
        result = run_instance(
            agents_dir=self.config.agents_dir,
            agent=agent,
            instance_id=instance_id,
            output_dir=self.config.output_dir,
            timeout=timeout,
            cache_file=self.config.cache_file,
            docker_ops=self.docker_ops,
            registry=self.config.swebench_registry,
            threshold_pct=self.config.max_storage_pct,
        )

        # Update attempt result
        self.run_manager.update_attempt_result(
            run_id,
            attempt.attempt_id,
            status=result.get("status", "unknown"),
            elapsed_seconds=result.get("elapsed_seconds", 0),
        )

        # Include attempt_id in return for callers that need it
        result["attempt_id"] = attempt.attempt_id
        return result

    def _ensure_run(self, agent: str, timeout: int) -> tuple[str, RunManifest]:
        """Ensure a run manifest exists for the agent.

        Creates a new run if none exists. Returns (run_id, manifest).

        Args:
            agent: Agent name.
            timeout: Default timeout per instance.

        Returns:
            Tuple of (run_id, RunManifest).
        """
        manifest = self.run_manager.resolve_run(agent)
        if manifest is None:
            manifest = self.run_manager.create_run(agent=agent, timeout=timeout)
        return manifest.run_id, manifest

    def run_all(
        self,
        agent: str,
        timeout: int = 3600,
        resume: bool = False,
    ) -> dict[str, Any]:
        """Run an agent against all cached instances.

        Before starting, waits for any previously running containers to finish
        (safety net for interrupted runs).

        For each instance, interleaves:
        1. Work phase — run agent, produce patch
        2. Eval phase — run harness for that single instance
        3. Cleanup — remove the instance's Docker image

        Args:
            agent: Agent name.
            timeout: Maximum runtime per instance.
            resume: Skip instances that already have results.

        Returns:
            Dict with run statistics (total, resolved, no_answer, timeout, error).
        """
        # Safety net: wait for any stale containers from interrupted runs
        logger.info("Waiting for any running %s containers to finish...", agent)
        self.docker_ops.wait_for_agent_containers(agent, timeout_seconds=timeout)

        # Ensure a run manifest exists
        run_id, _ = self._ensure_run(agent, timeout)

        # Get all instances from cache
        dataset_cache = DatasetCache(self.config.cache_file)
        instance_ids = [inst["instance_id"] for inst in dataset_cache.data]
        total = len(instance_ids)

        # Read existing results to initialize stats
        output_dir = self.config.output_dir / agent
        success = 0
        no_answer = 0
        timeout_count = 0
        error_count = 0
        pending_eval = 0
        pre_existing = 0
        if output_dir.is_dir():
            for instance_dir in output_dir.iterdir():
                if not instance_dir.is_dir() or instance_dir.name in ("eval", "logs"):
                    continue
                result_file = instance_dir / "result.json"
                if result_file.exists():
                    pre_existing += 1
                    try:
                        from swebench_orchestrator.models import read_json
                        meta = read_json(result_file)
                        local_eval = _normalize_local_eval(meta.get("local_eval"))
                        status = meta.get("status", "")
                        if local_eval == "resolved":
                            success += 1
                        elif local_eval == "failed":
                            no_answer += 1
                        elif local_eval == "error":
                            error_count += 1
                        elif status == "timed_out":
                            timeout_count += 1
                        elif status == "patch_collected":
                            # Patch generated but eval not run yet
                            pending_eval += 1
                        else:
                            # Unknown status - count as error
                            error_count += 1
                    except (json.JSONDecodeError, ValueError):
                        # Invalid JSON, count as error
                        error_count += 1

        count = 0

        for idx, iid in enumerate(instance_ids):
            # Print stats at start of each instance
            # completed = resolved + no_answer + timeout + error (not pending_eval)
            completed = success + no_answer + timeout_count + error_count
            logger.info(
                "[%d/%d] %s | completed: %d | resolved: %d | no_answer: %d | timeout: %d | error: %d | pending: %d",
                idx + 1, total, iid, completed, success, no_answer, timeout_count, error_count, pending_eval
            )

            if resume:
                result_file = self.config.output_dir / agent / iid / "result.json"
                if result_file.exists():
                    # Already counted in initial scan, just skip
                    continue

            count += 1
            try:
                # Phase 1: Work — run agent, produce patch
                # run_instance creates its own attempt and returns attempt_id
                result = self.run_instance(agent, iid, timeout, run_id=run_id)

                attempt_id = result.get("attempt_id")
                if not attempt_id:
                    logger.error("No attempt_id returned for %s after run_instance", iid)
                    error_count += 1
                    continue
                work_status = result.get("status", "unknown")

                if work_status == "timed_out":
                    timeout_count += 1
                    # Update manifest with work result; skip eval on failure
                    self.run_manager.update_attempt_result(
                        run_id,
                        attempt_id,
                        status=work_status,
                        elapsed_seconds=result.get("elapsed_seconds", 0),
                    )
                    continue
                elif work_status in ("container_error", "copy_failed"):
                    error_count += 1
                    # Update manifest with work result; skip eval on failure
                    self.run_manager.update_attempt_result(
                        run_id,
                        attempt_id,
                        status=work_status,
                        elapsed_seconds=result.get("elapsed_seconds", 0),
                    )
                    continue

                # Update manifest with work result
                self.run_manager.update_attempt_result(
                    run_id,
                    attempt_id,
                    status=work_status,
                    elapsed_seconds=result.get("elapsed_seconds", 0),
                )

                # Phase 2: Eval — run harness for this single instance
                eval_result = self.run_eval_instance(
                    agent=agent,
                    instance_id=iid,
                    output_dir=self.config.output_dir / agent,
                    dataset_name=self.config.hf_dataset,
                    swebench_py=self.config.swebench_py if self.config.swebench_py.exists() else None,
                )

                local_eval = eval_result.get("local_eval")

                # Update manifest with eval result
                self.run_manager.update_attempt_result(
                    run_id,
                    attempt_id,
                    status=work_status,
                    elapsed_seconds=result.get("elapsed_seconds", 0),
                    local_eval=local_eval,
                )

                # Count based on eval result
                if local_eval == "resolved":
                    success += 1
                elif local_eval == "failed":
                    no_answer += 1
                elif local_eval == "error":
                    error_count += 1

                # Phase 3: Cleanup — remove the instance's Docker image
                image_name = instance_to_image_name(iid, registry=self.config.swebench_registry)
                self.docker_ops.remove_image(image_name)

            except Exception as e:
                logger.error("Failed to run %s: %s", iid, e)
                error_count += 1

        return {
            "total": total,
            "resolved": success,
            "no_answer": no_answer,
            "timeout": timeout_count,
            "error": error_count,
            "pending_eval": pending_eval,
        }

    def summarize(self, agent: str) -> dict[str, Any]:
        """Summarize results for an agent.

        Args:
            agent: Agent name.

        Returns:
            Summary dict.
        """
        output_dir = self.config.output_dir / agent
        return summarize_results(output_dir, agent=agent)

    def run_eval_instance(
        self,
        agent: str,
        instance_id: str,
        output_dir: Optional[Path] = None,
        dataset_name: str = "princeton-nlp/SWE-bench_Verified",
        swebench_py: Optional[Path] = None,
    ) -> dict[str, Any]:
        """Run swebench harness evaluation for a single instance.

        Creates a temp predictions.jsonl with one entry, runs the harness,
        folds the result, and cleans up the temp file.

        Args:
            agent: Agent name.
            instance_id: SWE-bench instance ID to evaluate.
            output_dir: Instance output directory (defaults to config).
            dataset_name: HuggingFace dataset name.
            swebench_py: Path to swebench Python (uses config if None).

        Returns:
            Dict with status and local_eval result.
            On success: {"status": "completed", "local_eval": "resolved"/"failed"/"error"}
            On failure: {"status": "harness_error"/"no_patch"/"no_report", "local_eval": None}
        """
        if output_dir is None:
            output_dir = self.config.output_dir / agent

        # Read patch from instance output directory
        patch_file = output_dir / instance_id / "patch.diff"
        if not patch_file.exists() or patch_file.stat().st_size == 0:
            logger.warning(
                "Agent completed for %s but produced no patch (agent signaled completion without modifying files), skipping eval",
                instance_id,
            )
            return {"status": "no_patch", "local_eval": None}

        # Create unique run_id to avoid report collisions
        run_id = f"{agent}_{instance_id}"

        # Write single-entry predictions.jsonl to a temp file
        preds_file = output_dir / f".tmp_predictions_{instance_id}.jsonl"
        try:
            patch_content = patch_file.read_text()
            with open(preds_file, "w") as f:
                f.write(json.dumps({
                    "instance_id": instance_id,
                    "model_name_or_path": agent,
                    "model_patch": patch_content,
                }) + "\n")

            # Run swebench harness for this single instance
            if swebench_py is None:
                swebench_py = Path(".venv/swebench/bin/python").absolute()

            import subprocess

            report_dir = output_dir / "eval"
            report_dir.mkdir(exist_ok=True)

            cmd = [
                str(swebench_py),
                "-m", "swebench.harness.run_evaluation",
                "--dataset_name", dataset_name,
                "--split", "test",
                "--predictions_path", str(preds_file.absolute()),
                "--max_workers", "1",
                "--cache_level", "instance",
                "--report_dir", str(report_dir.absolute()),
                "--run_id", run_id,
                "-i", instance_id,
            ]

            logger.info("[EVAL] Running harness for %s (run_id=%s)", instance_id, run_id)
            result = subprocess.run(cmd, cwd=str(output_dir))

            if result.returncode != 0:
                logger.error(
                    "swebench harness failed for %s with exit code %d",
                    instance_id, result.returncode,
                )
                return {"status": "harness_error", "local_eval": None}

            # Find and fold harness results
            report_data = find_harness_report_for_instance(output_dir, instance_id, run_id)
            if report_data:
                # Fold results into result.json so local_eval is persisted
                fold_harness_results(output_dir, report_data)
                resolved = set(report_data.get("resolved_ids", []))
                errored = set(report_data.get("error_ids", []))
                unresolved = set(report_data.get("unresolved_ids", []))

                if instance_id in resolved:
                    local_eval = "resolved"
                elif instance_id in errored:
                    local_eval = "error"
                else:
                    local_eval = "failed"

                logger.info("Eval for %s: %s", instance_id, local_eval)
                return {"status": "completed", "local_eval": local_eval}
            else:
                logger.warning("No harness report found for %s", instance_id)
                return {"status": "no_report", "local_eval": None}

        finally:
            # Always clean up temp predictions file
            if preds_file.exists():
                try:
                    preds_file.unlink()
                except OSError:
                    pass

    def eval(self, agent: str) -> dict[str, Any]:
        """Run swebench harness evaluation for an agent.

        Args:
            agent: Agent name.

        Returns:
            Eval result dict with status and instance count.
        """
        output_dir = self.config.output_dir / agent
        return run_eval(
            output_dir=output_dir,
            agent=agent,
            dataset_name=self.config.hf_dataset,
            swebench_py=self.config.swebench_py if self.config.swebench_py.exists() else None,
        )
