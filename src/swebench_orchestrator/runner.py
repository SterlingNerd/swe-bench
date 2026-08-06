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
    Summary,
    compute_storage_info,
    instance_to_image_name,
    read_json,
    write_json,
)
from swebench_orchestrator.storage import check_storage

logger = logging.getLogger(__name__)


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
        agent: Agent name (directory under agents/).
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
    agents_dir = Path(agents_dir)
    agent_dir = agents_dir / agent
    if not agent_dir.is_dir():
        available = []
        if agents_dir.is_dir():
            available = [d.name for d in agents_dir.iterdir() if d.is_dir() and d.name != "base"]
        raise ValueError(
            f"Agent '{agent}' not found. Available agents: {', '.join(available) or 'none'}"
        )

    # Validate bundle exists
    bundle_dir = agent_dir / "bundle"
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

    # Prepare output directory
    agent_output_root = Path(output_dir) / agent
    instance_output_dir = agent_output_root / instance_id
    instance_output_dir.mkdir(parents=True, exist_ok=True)

    container_name = f"swe_{agent}_{instance_id}"

    # Release any stale container from previous interrupted run
    docker_ops.release_container(container_name)

    # Build Docker command
    started_at = time.time()
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
        "-v", f"{agent_output_root}:/workspace/outputs",
    ]

    command = [
        image_name,
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
            # Flatten: docker cp nests the instance dir
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
                # Copy directly if no nesting
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

    # Fix ownership
    import os
    try:
        os.chown(str(instance_output_dir), os.getuid(), os.getgid())
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
    resolved = sum(1 for r in rows if r["local_eval"] == "resolved")
    failed = sum(1 for r in rows if r["local_eval"] == "failed")
    errored = sum(1 for r in rows if r["local_eval"] == "error")
    no_patch = sum(1 for r in rows if r["status"] == "no_patch")
    timed_out = sum(1 for r in rows if r["status"] == "timed_out")
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


def find_harness_report(output_dir: Path) -> Optional[dict[str, Any]]:
    """Find the swebench harness report JSON file.

    Searches for reports in the eval directory with various naming patterns.

    Args:
        output_dir: Agent's output directory.

    Returns:
        Report dict if found, None otherwise.
    """
    eval_dir = output_dir / "eval"
    if not eval_dir.is_dir():
        return None

    # Try common report naming patterns
    candidates = [
        eval_dir / f"{output_dir.name}.{output_dir.name}.json",
        eval_dir / f"{output_dir.name}__{output_dir.name}.json",
    ]

    # Also try newest JSON file in eval dir
    json_files = sorted(eval_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    candidates.extend(json_files)

    for report_file in candidates:
        if report_file.exists():
            try:
                data = read_json(report_file)
                if "resolved_ids" in data and "unresolved_ids" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                continue

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
        swebench_py = Path(".venv/swebench/bin/python")

    import subprocess

    report_dir = output_dir / "eval"
    report_dir.mkdir(exist_ok=True)

    cmd = [
        str(swebench_py),
        "-m", "swebench.harness.run_evaluation",
        "--dataset_name", dataset_name,
        "--split", "test",
        "--predictions_path", str(preds_file),
        "--max_workers", "1",
        "--cache_level", "instance",
        "--report_dir", str(report_dir),
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
    if report_data:
        folded = fold_harness_results(output_dir, report_data)
        logger.info("Folded %d harness results", folded)
    else:
        logger.warning("No harness report found, skipping result folding")

    return {
        "status": "completed",
        "instances": len(instance_ids),
        "folded": fold_harness_results(output_dir, report_data or {}) if report_data else 0,
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

        return result

    def run_all(
        self,
        agent: str,
        timeout: int = 3600,
        resume: bool = False,
    ) -> dict[str, Any]:
        """Run an agent against all cached instances.

        Args:
            agent: Agent name.
            timeout: Maximum runtime per instance.
            resume: Skip instances that already have results.

        Returns:
            Dict with run statistics (run, skipped, failed counts).
        """
        # Get all instances from cache
        dataset_cache = DatasetCache(self.config.cache_file)
        instance_ids = [inst["instance_id"] for inst in dataset_cache.data]

        count = 0
        skipped = 0
        failed = 0

        for iid in instance_ids:
            if resume:
                result_file = self.config.output_dir / agent / iid / "result.json"
                if result_file.exists():
                    skipped += 1
                    continue

            count += 1
            try:
                result = self.run_instance(agent, iid, timeout)
                if result.get("status") in ("timed_out", "container_error", "copy_failed"):
                    failed += 1
            except Exception as e:
                logger.error("Failed to run %s: %s", iid, e)
                failed += 1

        return {
            "run": count,
            "skipped": skipped,
            "failed": failed,
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
