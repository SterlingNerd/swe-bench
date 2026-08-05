"""CLI interface for the SWE-bench orchestrator.

Provides a click-based command-line interface that mirrors the run.sh commands:
- --index, --list, --build, --rebuild
- --run, --run-all
- --eval, --summarize, --status
- --init, --cleanup, --cleanup-partial
- --interactive
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from swebench_orchestrator.config import Config
from swebench_orchestrator.dataset import fetch_and_cache_dataset
from swebench_orchestrator.docker_ops import DockerOps
from swebench_orchestrator.locking import LockFile
from swebench_orchestrator.logging_config import setup_logging_from_cli
from swebench_orchestrator.manifest import (
    cleanup_partial_attempts,
    list_runs,
)
from swebench_orchestrator.runner import Runner, summarize_results
from swebench_orchestrator.shutdown import setup_signal_handlers


def setup_logging(verbose: bool = False) -> None:
    """Configure logging based on verbosity level.

    Replaces the crude ``exec > >(tee -a "$LOG_FILE") 2>&1`` from run.sh
    with Python's standard logging module, providing both console (stderr)
    and file handlers with proper log levels and formatting.
    """
    setup_logging_from_cli(verbose=verbose)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """SWE-bench Orchestrator — unified build, index, work, and eval.

    Self-contained agent bundles mounted into swebench eval images.

    USAGE
      swebench-orchestrator [OPTIONS] COMMAND [ARGS]...
      swebench-orchestrator --help            Show this help

    COMMANDS
      --index              Fetch dataset from HuggingFace and cache locally
      --list [FILTER]      List cached instances (optional grep filter)
      --build [AGENT]      Build agent bundle(s) only
      --rebuild [SCOPE]    Rebuild from scratch (--no-cache): all|<agent>
      --run AGENT ID       Run agent against a specific instance
      --run-all AGENT      Run agent against all cached instances
      --eval AGENT         Evaluate collected patches via swebench harness
      --summarize [AGENT]  Combine and summarize results
      --status [AGENT]     Show completion status
      --interactive AGENT ID  Drop into interactive shell in eval image
      --init               Install swebench harness (creates .venv/swebench)
      --cleanup            Remove swe_* containers and swebench images
      --cleanup-partial    Remove incomplete output directories
    """
    ctx.ensure_object(dict)
    setup_logging(verbose)

    # Acquire single-instance lock (Issue #9)
    repo_root = Path(__file__).parent.parent.parent
    try:
        config = Config.from_env(repo_root)
    except Exception:
        config = Config.from_env()

    lock = LockFile(config.lock_file)
    try:
        lock.acquire()
    except RuntimeError as e:
        click.echo(f"ERROR: {e}", err=True)
        ctx.exit(1)

    # Store lock in context so it survives command dispatch
    ctx.obj["_lock"] = lock

    # Install signal handlers for graceful shutdown (Issue #8)
    setup_signal_handlers()

    ctx.obj["config"] = config


@main.command()
@click.pass_context
def index(ctx: click.Context) -> None:
    """Fetch the SWE-bench Verified dataset from HuggingFace and cache it."""
    config = ctx.obj["config"]
    click.echo("=== Indexing SWE-bench Verified ===")

    try:
        data = fetch_and_cache_dataset(config.cache_file, config.hf_dataset)
        click.echo(f"Cached {len(data)} instances at {config.cache_file}")
    except Exception as e:
        click.echo(f"ERROR: Failed to fetch dataset: {e}", err=True)
        ctx.exit(1)


@main.result_callback()
@click.pass_context
def release_lock(ctx: click.Context, result: object, **kwargs: object) -> None:
    """Release the single-instance lock when the CLI exits."""
    lock = ctx.obj.get("_lock")
    if lock is not None:
        lock.release()


@main.command()
@click.argument("filter", required=False, default=None)
@click.pass_context
def list(ctx: click.Context, filter: str | None) -> None:
    """Print all cached instances with optional filter."""
    config = ctx.obj["config"]
    from swebench_orchestrator.dataset import DatasetCache

    cache = DatasetCache(config.cache_file)
    if not cache.is_valid:
        click.echo("No cached dataset. Run --index first.", err=True)
        ctx.exit(1)

    click.echo("=== SWE-bench Verified Instances ===")
    for inst in cache.list_instances(filter_str=filter):
        click.echo(
            f"{inst['instance_id']:40s} {inst['repo']:30s} v{inst['version']:10s} {inst['difficulty']:20s}"
        )
    click.echo(f"\nTotal: {cache.count} instances")


@main.command()
@click.argument("agent", required=False, default=None)
@click.pass_context
def build(ctx: click.Context, agent: str | None) -> None:
    """Build agent bundle(s) only (no Docker images)."""
    from swebench_orchestrator.bundles import BundleBuilder

    config = ctx.obj["config"]
    builder = BundleBuilder(config.agents_dir)

    click.echo("=== Building Agent Bundles (no Docker images) ===")

    if agent:
        try:
            success = builder.build_agent(agent)
            if not success:
                click.echo(f"WARNING: Failed to build {agent}", err=True)
                ctx.exit(1)
        except ValueError as e:
            click.echo(f"ERROR: {e}", err=True)
            ctx.exit(1)
    else:
        builder.build_all()

    # List built bundles
    click.echo("\n=== Built Bundles ===")
    for name, size in builder.list_bundles():
        click.echo(f"  {name} bundle: {size}")


@main.command()
@click.argument("scope", required=False, default="all")
@click.pass_context
def rebuild(ctx: click.Context, scope: str) -> None:
    """Rebuild agent bundles from scratch (--no-cache)."""
    from swebench_orchestrator.bundles import BundleBuilder

    config = ctx.obj["config"]
    builder = BundleBuilder(config.agents_dir)

    if scope != "all":
        agent_dir = config.agents_dir / scope
        if not agent_dir.is_dir():
            click.echo(f"ERROR: Unknown rebuild target '{scope}'. Available agents:", err=True)
            for a in builder.available_agents:
                click.echo(f"  {a}", err=True)
            ctx.exit(1)
        try:
            success = builder.rebuild_agent(scope)
            if not success:
                click.echo(f"WARNING: Failed to rebuild {scope}", err=True)
                ctx.exit(1)
        except ValueError as e:
            click.echo(f"ERROR: {e}", err=True)
            ctx.exit(1)
    else:
        builder.rebuild_all()

    click.echo("\n=== Built Bundles ===")
    for name, size in builder.list_bundles():
        click.echo(f"  {name} bundle: {size}")


@main.command()
@click.argument("agent")
@click.argument("instance_id")
@click.option("--timeout", "-t", default=3600, type=int, help="Timeout in seconds (default: 3600)")
@click.option("--run-id", default=None, help="Run ID for manifest tracking")
@click.pass_context
def run(
    ctx: click.Context,
    agent: str,
    instance_id: str,
    timeout: int,
    run_id: str | None,
) -> None:
    """Run an agent against a single instance."""
    config = ctx.obj["config"]

    # Validate timeout is numeric (click handles this with type=int)
    if timeout < 0:
        click.echo("ERROR: Timeout must be non-negative", err=True)
        ctx.exit(2)

    runner = Runner(config)
    try:
        result = runner.run_instance(agent, instance_id, timeout, run_id)
        status = result.get("status", "unknown")
        elapsed = result.get("elapsed_seconds", 0)
        click.echo(f"Result: {status} ({elapsed}s)")

        if status in ("timed_out", "container_error", "copy_failed"):
            ctx.exit(1)
    except ValueError as e:
        click.echo(f"ERROR: {e}", err=True)
        ctx.exit(1)


@main.command()
@click.argument("agent")
@click.option("--timeout", "-t", default=3600, type=int, help="Timeout per instance (default: 3600)")
@click.option("--resume", is_flag=True, help="Skip instances with existing results")
@click.pass_context
def run_all(
    ctx: click.Context,
    agent: str,
    timeout: int,
    resume: bool,
) -> None:
    """Run an agent against all cached instances."""
    config = ctx.obj["config"]

    if timeout < 0:
        click.echo("ERROR: Timeout must be non-negative", err=True)
        ctx.exit(1)

    runner = Runner(config)
    try:
        result = runner.run_all(agent, timeout, resume)
        click.echo(f"\nDone: {result['run']} run, {result['skipped']} skipped (resume), {result['failed']} failed")

        if result["failed"] > 0:
            ctx.exit(1)
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        ctx.exit(1)


@main.command()
@click.argument("agent")
@click.pass_context
def eval(ctx: click.Context, agent: str) -> None:
    """Evaluate collected patches via swebench harness."""
    config = ctx.obj["config"]

    # Check swebench is installed
    if not config.swebench_py.exists():
        click.echo("ERROR: swebench not installed. Run --init first.", err=True)
        ctx.exit(1)

    eval_dir = config.output_dir / agent
    if not eval_dir.is_dir():
        click.echo("No outputs found. Run instances first (--run or --run-all).", err=True)
        ctx.exit(1)

    # Collect instances with patches
    instance_ids = []
    for d in sorted(eval_dir.iterdir()):
        if d.is_dir() and d.name not in ("eval", "logs"):
            patch_file = d / "patch.diff"
            if patch_file.exists() and patch_file.stat().st_size > 0:
                instance_ids.append(d.name)

    if not instance_ids:
        click.echo("No patches found to evaluate. Run instances first.", err=True)
        ctx.exit(1)

    # Build predictions file
    import json as _json

    preds_file = eval_dir / "predictions.jsonl"
    with open(preds_file, "w") as f:
        for iid in instance_ids:
            patch_path = eval_dir / iid / "patch.diff"
            patch = patch_path.read_text()
            f.write(_json.dumps({
                "instance_id": iid,
                "model_name_or_path": agent,
                "model_patch": patch,
            }) + "\n")

    click.echo(f"Wrote {len(instance_ids)} predictions to {preds_file}")
    click.echo(f"[EVAL] Running swebench harness on {len(instance_ids)} patch(es) for '{agent}'")

    # Run swebench harness via subprocess
    import subprocess

    report_dir = eval_dir / "eval"
    report_dir.mkdir(exist_ok=True)

    cmd = [
        str(config.swebench_py),
        "-m", "swebench.harness.run_evaluation",
        "--dataset_name", config.hf_dataset,
        "--split", "test",
        "--predictions_path", str(preds_file),
        "--max_workers", "1",
        "--cache_level", "instance",
        "--report_dir", str(report_dir),
        "--run_id", agent,
        "-i" + ",".join(instance_ids),
    ]

    click.echo(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(eval_dir))
    if result.returncode != 0:
        click.echo("ERROR: swebench harness failed.", err=True)
        ctx.exit(result.returncode)

    # Fold harness results back into each instance's result.json
    from swebench_orchestrator.runner import find_harness_report, fold_harness_results

    report_data = find_harness_report(eval_dir)
    if report_data:
        folded = fold_harness_results(eval_dir, report_data)
        click.echo(f"Folded harness results for {folded} instance(s).")
    else:
        click.echo("WARNING: No harness report found — results not folded into result.json", err=True)


@main.command()
@click.argument("agent", required=False, default=None)
@click.pass_context
def summarize(ctx: click.Context, agent: str | None) -> None:
    """Combine and summarize results."""
    config = ctx.obj["config"]

    if agent:
        summary = summarize_results(config.output_dir / agent, agent=agent)
    else:
        # Summarize all agents
        output_dir = config.output_dir
        if not output_dir.is_dir():
            click.echo("No outputs found.")
            return

        for agent_dir in sorted(output_dir.iterdir()):
            if agent_dir.is_dir() and agent_dir.name not in ("eval", "logs"):
                summary = summarize_results(agent_dir, agent=agent_dir.name)
                _print_summary(summary)
                click.echo()
        return

    _print_summary(summary)


def _print_summary(summary: dict) -> None:
    """Print a formatted summary table."""
    click.echo(f"Agent: {summary['agent']}")
    click.echo(f"{'instance_id':42s} {'status':12s} {'local_eval':12s} {'patch_B':>8s} {'elapsed_s':>10s}")
    for row in summary["rows"]:
        click.echo(
            f"{row['instance_id']:42s} {str(row['status']):12s} {str(row.get('local_eval', '')):12s} "
            f"{str(row.get('patch_bytes', 0) or 0):>8s} {str(row.get('elapsed_seconds', 0) or 0):>10s}"
        )
    click.echo(
        f"\nTotal: {summary['total']} | resolved: {summary['resolved']} "
        f"| failed: {summary['failed']} | error: {summary['errored']} "
        f"| no_patch: {summary['no_patch']} | timed_out: {summary['timed_out']} "
        f"| agent_error: {summary['agent_errors']}"
    )


@main.command()
@click.argument("agent", required=False, default=None)
@click.pass_context
def status(ctx: click.Context, agent: str | None) -> None:
    """Show completion status."""
    config = ctx.obj["config"]

    click.echo("=== SWE-bench Harness Status ===")
    click.echo(f"Output directory: {config.output_dir}")
    click.echo()

    if agent:
        output_dir = config.output_dir / agent
        if not output_dir.is_dir():
            click.echo(f"No outputs found for agent '{agent}'.")
            return

        total = 0
        resolved = 0
        failed = 0
        no_patch = 0
        timed_out = 0
        errors = 0
        unknown = 0

        for instance_dir in sorted(output_dir.iterdir()):
            if not instance_dir.is_dir() or instance_dir.name in ("eval", "logs"):
                continue

            total += 1
            result_file = instance_dir / "result.json"

            if result_file.exists():
                from swebench_orchestrator.models import read_json
                try:
                    meta = read_json(result_file)
                    status = meta.get("status", "unknown")
                except Exception:
                    status = "unknown"
            else:
                status = "unknown"

            if status == "resolved":
                resolved += 1
                click.echo(click.style("✓", fg="green"), nl=False)
                click.echo(f" {instance_dir.name} ({status})")
            elif status == "failed":
                failed += 1
                click.echo(click.style("✗", fg="red"), nl=False)
                click.echo(f" {instance_dir.name} ({status})")
            elif status == "no_patch":
                no_patch += 1
                click.echo(click.style("—", fg="yellow"), nl=False)
                click.echo(f" {instance_dir.name} (no patch)")
            elif status == "timed_out":
                timed_out += 1
                click.echo(click.style("⌛", fg="yellow"), nl=False)
                click.echo(f" {instance_dir.name} (timed out)")
            elif status in ("error", "agent_error", "container_error"):
                errors += 1
                click.echo(click.style("!", fg="red"), nl=False)
                click.echo(f" {instance_dir.name} ({status})")
            else:
                unknown += 1
                click.echo(click.style("?", fg="white"), nl=False)
                click.echo(f" {instance_dir.name} ({status})")

        click.echo()
        click.echo(
            f"Total: {total} | Resolved: {resolved} | Failed: {failed} "
            f"| No patch: {no_patch} | Timed out: {timed_out} "
            f"| Errors: {errors} | Unknown: {unknown}"
        )
    else:
        # Show all agents
        if not config.output_dir.is_dir():
            click.echo("No outputs found. Run instances first.")
            return

        for agent_dir in sorted(config.output_dir.iterdir()):
            if agent_dir.is_dir() and agent_dir.name not in ("eval", "logs"):
                # Reuse the logic above for each agent
                summary = summarize_results(agent_dir, agent=agent_dir.name)
                click.echo(f"Agent: {summary['agent']}")
                click.echo(
                    f"Total: {summary['total']} | Resolved: {summary['resolved']} "
                    f"| Failed: {summary['failed']} | No patch: {summary['no_patch']} "
                    f"| Timed out: {summary['timed_out']} | Errors: {summary['agent_errors']}"
                )
                click.echo()


@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Install the swebench harness in a local venv."""
    config = ctx.obj["config"]

    if config.swebench_py.exists():
        click.echo(f"swebench already installed at {config.swebench_venv}")
        result = subprocess.run(
            [str(config.swebench_py), "-c", "import swebench; print(f'  Version: {swebench.__version__}')"],
            capture_output=True,
            text=True,
        )
        click.echo(result.stdout.strip())
        return

    click.echo(f"Creating venv at {config.swebench_venv}...")
    import subprocess

    subprocess.run([sys.executable, "-m", "venv", str(config.repo_root / config.swebench_venv)], check=True)
    click.echo("Installing swebench...")
    pip_path = str(config.repo_root / config.swebench_venv / "bin" / "pip")
    subprocess.run([pip_path, "install", "swebench"], check=True)

    result = subprocess.run(
        [str(config.swebench_py), "-c", "import swebench; print(f'  Version: {swebench.__version__}')"],
        capture_output=True,
        text=True,
    )
    click.echo(result.stdout.strip())
    click.echo("=== swebench installed ===")


@main.command()
@click.argument("agent")
@click.argument("instance_id")
@click.pass_context
def interactive(ctx: click.Context, agent: str, instance_id: str) -> None:
    """Drop into an interactive shell in the swebench eval image."""
    config = ctx.obj["config"]
    docker_ops = DockerOps()

    # Validate agent bundle
    bundle_dir = config.agents_dir / agent / "bundle"
    if not bundle_dir.is_dir():
        click.echo(f"ERROR: Agent bundle not found at {bundle_dir}. Run --build {agent} first.", err=True)
        ctx.exit(1)

    # Get instance data
    from swebench_orchestrator.dataset import DatasetCache
    cache = DatasetCache(config.cache_file)
    inst_data = cache.get_instance(instance_id)
    if not inst_data:
        click.echo(f"ERROR: Instance not found: {instance_id}", err=True)
        ctx.exit(1)

    # Determine image
    image_name = instance_to_image_name(instance_id)

    # Pull image if needed
    if not docker_ops.image_exists(image_name):
        click.echo(f"Pulling swebench image: {image_name}...")
        docker_ops.pull_image(image_name)

    click.echo(f"Starting interactive shell for {agent} in {image_name}...")

    import subprocess

    cmd = [
        "docker", "run", "--rm", "-i",
    ]
    if sys.stdout.isatty():
        cmd.append("-t")

    cmd += [
        "--memory", "32g",
        "--memory-swap", "64g",
        "--pids-limit", "500",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=2g",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--add-host", "host.docker.internal:host-gateway",
        "-e", f"SWE_AGENT_NAME={agent}",
        "-e", f"SWE_OUTPUT_ROOT=/workspace/outputs/{agent}",
        "-v", str(config.workspace_dir) + ":/workspace:rw",
        "-v", str(bundle_dir) + ":/agent:ro",
        image_name,
        "/agent/entrypoint.sh",
        "--interactive",
    ]

    subprocess.run(cmd)


@main.command()
@click.pass_context
def cleanup(ctx: click.Context) -> None:
    """Remove only harness-owned swe_* containers and swebench images."""
    from swebench_orchestrator.storage import (
        cleanup_docker_containers,
        get_swebench_images,
        prune_docker_images,
    )

    click.echo("=== Cleaning up SWE-bench Docker resources ===")

    # Remove containers
    container_result = cleanup_docker_containers()
    if container_result["containers_removed"] > 0:
        click.echo(f"Removed {container_result['containers_removed']} SWE-bench container(s).")
    if container_result["endpoints_released"] > 0:
        click.echo(f"Released {container_result['endpoints_released']} orphaned network endpoint(s).")

    # Remove images
    image_result = prune_docker_images()
    if image_result["removed"] > 0:
        click.echo(f"Removed {image_result['removed']} SWE-bench image(s).")

    if container_result["containers_removed"] == 0 and image_result["removed"] == 0:
        click.echo("No SWE-bench Docker resources found.")

    click.echo("=== Cleanup complete ===")


@main.command()
@click.argument("agent", required=False, default=None)
@click.pass_context
def cleanup_partial(ctx: click.Context, agent: str | None) -> None:
    """Remove output directories missing result.json or patch.diff."""
    config = ctx.obj["config"]

    # Check old-style outputs
    removed_old = 0
    kept_old = 0
    if config.output_dir.is_dir():
        for d in sorted(config.output_dir.iterdir()):
            if not d.is_dir() or d.name in ("eval", "logs"):
                continue
            if agent and d.name != agent:
                continue
            result_file = d / "result.json"
            patch_file = d / "patch.diff"
            if result_file.exists() and patch_file.exists():
                kept_old += 1
            else:
                import shutil
                shutil.rmtree(d)
                removed_old += 1
                click.echo(f"  Removing: {d.name}/")

    # Check manifest-based runs
    removed_new = cleanup_partial_attempts(config.runs_dir, agent=agent, dry_run=False)

    click.echo(f"=== Removed {removed_old + len(removed_new)}, kept {kept_old} complete ===")


# Import here to avoid circular imports
from swebench_orchestrator.models import instance_to_image_name
import subprocess  # noqa: E402
