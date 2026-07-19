#!/usr/bin/env python3
"""Manifest-backed artifact management for SWE-bench runs.

The orchestrator deliberately delegates all run/attempt path decisions to this
module.  A run manifest is the only authority for cleanup and evaluation; no
command discovers attempts by recursively globbing arbitrary output trees.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
FINALIZED = "finalized"
ATTEMPT_OUTCOMES = {
    "patch_collected",
    "no_patch",
    "agent_error",
    "invalid_result",
    "container_error",
    "timed_out",
    "operator_cancelled",
    "oom_killed",
}


class ArtifactError(RuntimeError):
    """A fail-closed artifact contract violation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def component(value: str, label: str) -> str:
    if not COMPONENT_RE.fullmatch(value):
        raise ArtifactError(f"invalid {label}: {value!r}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        tmp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    atomic_write_bytes(path, payload)


def read_json(path: Path, label: str) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ArtifactError(f"missing {label}: {path}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise ArtifactError(f"invalid {label}: {path}: {exc}") from exc


def ensure_plain_directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ArtifactError(f"{label} is not a plain directory: {path}")
    return path


def manifest_path(run_dir: Path) -> Path:
    return run_dir / "manifest.json"


def load_manifest(run_dir: Path) -> dict[str, Any]:
    ensure_plain_directory(run_dir, "run directory")
    manifest = read_json(manifest_path(run_dir), "run manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ArtifactError(f"unsupported run manifest schema: {manifest_path(run_dir)}")
    if manifest.get("run_id") != run_dir.name:
        raise ArtifactError("run manifest id does not match its directory")
    component(str(manifest.get("agent", "")), "manifest agent")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, dict):
        raise ArtifactError("run manifest tasks must be an object")
    return manifest


def save_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    atomic_write_json(manifest_path(run_dir), manifest)


def expected_attempt_rel(instance_id: str, attempt_id: str) -> Path:
    return Path("tasks") / component(instance_id, "instance id") / "attempts" / component(
        attempt_id, "attempt id"
    )


def attempt_dir_from_descriptor(
    run_dir: Path, instance_id: str, descriptor: dict[str, Any]
) -> Path:
    attempt_id = component(str(descriptor.get("attempt_id", "")), "attempt id")
    expected = expected_attempt_rel(instance_id, attempt_id)
    if descriptor.get("path") != expected.as_posix():
        raise ArtifactError(
            f"attempt path mismatch for {instance_id}/{attempt_id}: {descriptor.get('path')!r}"
        )
    candidate = run_dir / expected
    resolved_root = run_dir.resolve()
    try:
        candidate.resolve(strict=False).relative_to(resolved_root)
    except ValueError as exc:
        raise ArtifactError(f"attempt escapes run directory: {candidate}") from exc
    for parent in (candidate.parent, candidate):
        if parent.exists() and parent.is_symlink():
            raise ArtifactError(f"symlinked attempt path is forbidden: {parent}")
    return candidate


def task_for(manifest: dict[str, Any], instance_id: str) -> dict[str, Any]:
    component(instance_id, "instance id")
    try:
        task = manifest["tasks"][instance_id]
    except KeyError as exc:
        raise ArtifactError(f"instance is not planned in this run: {instance_id}") from exc
    if not isinstance(task, dict) or not isinstance(task.get("attempts"), list):
        raise ArtifactError(f"invalid task record: {instance_id}")
    return task


def descriptor_for(task: dict[str, Any], attempt_id: str) -> dict[str, Any]:
    component(attempt_id, "attempt id")
    matches = [item for item in task["attempts"] if item.get("attempt_id") == attempt_id]
    if len(matches) != 1:
        raise ArtifactError(f"attempt not found or duplicated: {attempt_id}")
    return matches[0]


def cmd_create_run(args: argparse.Namespace) -> None:
    runs_dir = Path(args.runs_dir).resolve()
    run_id = component(args.run_id, "run id")
    agent = component(args.agent, "agent")
    profile = component(args.profile, "profile")
    if args.timeout_seconds < 0:
        raise ArtifactError("timeout seconds must be non-negative")
    if not re.fullmatch(r"[0-9a-f]{64}", args.dataset_sha256):
        raise ArtifactError("dataset SHA-256 must be 64 lowercase hexadecimal characters")
    try:
        instance_ids = [line.strip() for line in Path(args.instances_file).read_text().splitlines()]
    except OSError as exc:
        raise ArtifactError(f"cannot read instances file: {exc}") from exc
    instance_ids = [item for item in instance_ids if item]
    if not instance_ids:
        raise ArtifactError("a run must contain at least one instance")
    for instance_id in instance_ids:
        component(instance_id, "instance id")
    if len(set(instance_ids)) != len(instance_ids):
        raise ArtifactError("instances file contains duplicates")

    runs_dir.mkdir(parents=True, exist_ok=True)
    run_dir = runs_dir / run_id
    try:
        run_dir.mkdir()
    except FileExistsError as exc:
        raise ArtifactError(f"run already exists: {run_id}") from exc
    (run_dir / "tasks").mkdir()
    (run_dir / "reports" / "evaluations").mkdir(parents=True)

    now = utc_now()
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "agent": agent,
        "profile": profile,
        "created_at": now,
        "updated_at": now,
        "runner": {
            "git_commit": args.runner_commit,
            "dirty": bool(args.runner_dirty),
        },
        "dataset": {
            "name": args.dataset_name,
            "split": args.dataset_split,
            "cache_sha256": args.dataset_sha256,
            "instance_ids": instance_ids,
        },
        "config": {"timeout_seconds": args.timeout_seconds},
        "tasks": {
            instance_id: {"attempts": [], "selected_attempt": None}
            for instance_id in instance_ids
        },
        "evaluations": [],
        "latest_evaluation": None,
    }
    atomic_write_json(manifest_path(run_dir), manifest)

    latest_dir = runs_dir / "latest"
    latest_dir.mkdir(exist_ok=True)
    atomic_write_bytes(latest_dir / agent, f"{run_id}\n".encode())
    print(run_dir)


def resolve_run(runs_dir: Path, agent: str, run_id: str | None) -> Path:
    runs_dir = runs_dir.resolve()
    agent = component(agent, "agent")
    if run_id is None:
        latest_file = runs_dir / "latest" / agent
        if latest_file.is_symlink():
            raise ArtifactError(f"latest-run pointer must not be a symlink: {latest_file}")
        try:
            run_id = latest_file.read_text().strip()
        except OSError as exc:
            raise ArtifactError(f"no latest run is recorded for agent {agent!r}") from exc
    run_id = component(run_id, "run id")
    run_dir = runs_dir / run_id
    manifest = load_manifest(run_dir)
    if manifest["agent"] != agent:
        raise ArtifactError(
            f"run {run_id!r} belongs to {manifest['agent']!r}, not {agent!r}"
        )
    return run_dir


def cmd_resolve_run(args: argparse.Namespace) -> None:
    print(resolve_run(Path(args.runs_dir), args.agent, args.run_id))


def cmd_begin_attempt(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = load_manifest(run_dir)
    task = task_for(manifest, args.instance_id)
    number = len(task["attempts"]) + 1
    attempt_id = f"attempt-{number:04d}"
    rel_path = expected_attempt_rel(args.instance_id, attempt_id)
    attempt_dir = run_dir / rel_path
    attempt_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        attempt_dir.mkdir()
    except FileExistsError as exc:
        raise ArtifactError(f"attempt directory already exists: {attempt_dir}") from exc
    now = utc_now()
    descriptor = {
        "attempt_id": attempt_id,
        "path": rel_path.as_posix(),
        "state": "started",
        "created_at": now,
        "finalized_at": None,
        "outcome": None,
        "eval_eligible": False,
        "patch": None,
        "result": None,
    }
    atomic_write_json(
        attempt_dir / "attempt.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": manifest["run_id"],
            "instance_id": args.instance_id,
            **descriptor,
        },
    )
    task["attempts"].append(descriptor)
    save_manifest(run_dir, manifest)
    print(attempt_dir)


def cmd_task_state(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = load_manifest(run_dir)
    task = task_for(manifest, args.instance_id)
    if not task["attempts"]:
        print("pending")
        return
    latest = task["attempts"][-1]
    if latest.get("state") == "cleaned":
        print("pending")
        return
    if latest.get("state") == FINALIZED:
        print(str(latest.get("outcome") or "finalized"))
    else:
        print(str(latest.get("state") or "unknown"))


def cmd_finalize_attempt(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = load_manifest(run_dir)
    task = task_for(manifest, args.instance_id)
    descriptor = descriptor_for(task, args.attempt_id)
    if descriptor.get("state") == FINALIZED:
        raise ArtifactError(f"attempt is already finalized: {args.attempt_id}")
    attempt_dir = attempt_dir_from_descriptor(run_dir, args.instance_id, descriptor)
    ensure_plain_directory(attempt_dir, "attempt directory")
    result_path = attempt_dir / "result.json"
    patch_path = attempt_dir / "patch.diff"
    result = read_json(result_path, "attempt result")
    if not isinstance(result, dict) or result.get("status") not in ATTEMPT_OUTCOMES:
        raise ArtifactError(f"attempt result has an unsupported status: {result_path}")
    if patch_path.is_symlink() or not patch_path.is_file():
        raise ArtifactError(f"attempt patch is missing or not a plain file: {patch_path}")

    patch_bytes = patch_path.stat().st_size
    declared_bytes = result.get("patch_bytes")
    if (
        not isinstance(declared_bytes, int)
        or isinstance(declared_bytes, bool)
        or declared_bytes != patch_bytes
    ):
        raise ArtifactError(
            f"patch size mismatch: result={declared_bytes!r}, actual={patch_bytes}"
        )
    patch = {"bytes": patch_bytes, "sha256": sha256_file(patch_path)}
    result_artifact = {"bytes": result_path.stat().st_size, "sha256": sha256_file(result_path)}
    eligible = (
        result["status"] == "patch_collected"
        and patch_bytes > 0
        and not result.get("patch_capture_error", False)
    )
    now = utc_now()
    descriptor.update(
        {
            "state": FINALIZED,
            "finalized_at": now,
            "outcome": result["status"],
            "eval_eligible": eligible,
            "patch": patch,
            "result": result_artifact,
        }
    )
    attempt_record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": manifest["run_id"],
        "instance_id": args.instance_id,
        **descriptor,
    }
    atomic_write_json(attempt_dir / "attempt.json", attempt_record)
    if eligible and task.get("selected_attempt") is None:
        task["selected_attempt"] = args.attempt_id
        descriptor["selected_at"] = now
        descriptor["selection_policy"] = "first-eval-eligible"
    save_manifest(run_dir, manifest)
    print(
        json.dumps(
            {
                "eval_eligible": eligible,
                "patch": patch,
                "result": result_artifact,
                "outcome": result["status"],
            }
        )
    )


def cmd_select_attempt(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = load_manifest(run_dir)
    task = task_for(manifest, args.instance_id)
    descriptor = descriptor_for(task, args.attempt_id)
    if descriptor.get("state") != FINALIZED or not descriptor.get("eval_eligible"):
        raise ArtifactError("only a finalized, evaluation-eligible attempt can be selected")
    task["selected_attempt"] = args.attempt_id
    validate_selection(run_dir, args.instance_id, task)
    descriptor["selected_at"] = utc_now()
    descriptor["selection_policy"] = "explicit"
    save_manifest(run_dir, manifest)


def validate_selection(
    run_dir: Path,
    instance_id: str,
    task: dict[str, Any],
) -> tuple[dict[str, Any], Path] | None:
    selected_id = task.get("selected_attempt")
    if selected_id is None:
        return None
    descriptor = descriptor_for(task, str(selected_id))
    if descriptor.get("state") != FINALIZED or not descriptor.get("eval_eligible"):
        raise ArtifactError(f"selected attempt is not evaluation-eligible: {instance_id}")
    attempt_dir = attempt_dir_from_descriptor(run_dir, instance_id, descriptor)
    ensure_plain_directory(attempt_dir, "selected attempt directory")
    patch_path = attempt_dir / "patch.diff"
    if patch_path.is_symlink() or not patch_path.is_file():
        raise ArtifactError(f"selected patch is missing or not a plain file: {patch_path}")
    expected = descriptor.get("patch")
    actual = {"bytes": patch_path.stat().st_size, "sha256": sha256_file(patch_path)}
    if expected != actual:
        raise ArtifactError(f"selected patch changed after finalization: {instance_id}")
    result = read_json(attempt_dir / "result.json", "selected attempt result")
    result_path = attempt_dir / "result.json"
    actual_result = {"bytes": result_path.stat().st_size, "sha256": sha256_file(result_path)}
    if descriptor.get("result") != actual_result:
        raise ArtifactError(f"selected result changed after finalization: {instance_id}")
    if result.get("status") != descriptor.get("outcome"):
        raise ArtifactError(f"selected result changed after finalization: {instance_id}")
    return descriptor, patch_path


def cmd_build_predictions(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = load_manifest(run_dir)
    predictions: list[dict[str, Any]] = []
    selections: dict[str, Any] = {}
    ordered_ids = manifest["dataset"]["instance_ids"]
    for instance_id in ordered_ids:
        task = task_for(manifest, instance_id)
        selected = validate_selection(run_dir, instance_id, task)
        if selected is None:
            continue
        descriptor, patch_path = selected
        patch = patch_path.read_text(encoding="utf-8", errors="surrogateescape")
        predictions.append(
            {
                "instance_id": instance_id,
                "model_name_or_path": manifest["agent"],
                "model_patch": patch,
            }
        )
        selections[instance_id] = {
            "attempt_id": descriptor["attempt_id"],
            "path": descriptor["path"],
            "patch": descriptor["patch"],
        }
    if not predictions:
        raise ArtifactError("run has no selected evaluation-eligible attempts")
    predictions_data = b"".join(
        (json.dumps(item, sort_keys=True) + "\n").encode() for item in predictions
    )
    atomic_write_bytes(Path(args.output), predictions_data)
    atomic_write_json(
        Path(args.selection_output),
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": manifest["run_id"],
            "created_at": utc_now(),
            "selections": selections,
        },
    )
    print(len(predictions))


def cmd_record_evaluation(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = load_manifest(run_dir)
    evaluation_id = component(args.evaluation_id, "evaluation id")
    if any(item.get("evaluation_id") == evaluation_id for item in manifest["evaluations"]):
        raise ArtifactError(f"evaluation is already recorded: {evaluation_id}")
    selections_doc = read_json(Path(args.selection_file), "evaluation selections")
    if selections_doc.get("run_id") != manifest["run_id"]:
        raise ArtifactError("selection snapshot belongs to a different run")
    selections = selections_doc.get("selections")
    if not isinstance(selections, dict) or not selections:
        raise ArtifactError("evaluation selection snapshot is empty")
    for instance_id, snapshot in selections.items():
        task = task_for(manifest, instance_id)
        selected = validate_selection(run_dir, instance_id, task)
        if selected is None:
            raise ArtifactError(f"selection snapshot contains an unselected task: {instance_id}")
        descriptor, _ = selected
        expected_snapshot = {
            "attempt_id": descriptor["attempt_id"],
            "path": descriptor["path"],
            "patch": descriptor["patch"],
        }
        if snapshot != expected_snapshot:
            raise ArtifactError(f"evaluation selection snapshot changed: {instance_id}")
    report = read_json(Path(args.report_file), "official evaluation report")
    if not isinstance(report, dict):
        raise ArtifactError("official evaluation report must be an object")

    def report_ids(key: str) -> set[str]:
        values = report.get(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ArtifactError(f"official evaluation field {key!r} must be a string list")
        if len(values) != len(set(values)):
            raise ArtifactError(f"official evaluation field {key!r} contains duplicates")
        return set(values)

    selected_ids = set(selections)
    resolved = report_ids("resolved_ids")
    unresolved = report_ids("unresolved_ids")
    errored = report_ids("error_ids")
    if (resolved & unresolved) or (resolved & errored) or (unresolved & errored):
        raise ArtifactError("official evaluation outcome lists overlap")
    reported = resolved | unresolved | errored
    extras = reported - selected_ids
    if extras:
        raise ArtifactError(f"evaluation report contains unselected instances: {sorted(extras)}")
    outcomes = {
        instance_id: (
            "resolved"
            if instance_id in resolved
            else "error"
            if instance_id in errored
            else "failed"
            if instance_id in unresolved
            else "missing"
        )
        for instance_id in selections
    }
    eval_dir = run_dir / "reports" / "evaluations" / evaluation_id
    eval_dir.mkdir(parents=True, exist_ok=True)
    if (eval_dir / "evaluation.json").exists():
        raise ArtifactError(f"evaluation record already exists: {evaluation_id}")
    try:
        selection_rel = Path(args.selection_file).resolve().relative_to(run_dir)
        report_rel = Path(args.report_file).resolve().relative_to(run_dir)
    except ValueError as exc:
        raise ArtifactError("evaluation inputs must be contained in the run directory") from exc
    selection_digest = sha256_file(Path(args.selection_file))
    report_digest = sha256_file(Path(args.report_file))
    record = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": evaluation_id,
        "run_id": manifest["run_id"],
        "created_at": utc_now(),
        "selection_file": str(selection_rel),
        "selection_sha256": selection_digest,
        "official_report": str(report_rel),
        "official_report_sha256": report_digest,
        "outcomes": outcomes,
        "counts": {
            "selected": len(selected_ids),
            "resolved": len(resolved),
            "failed": len(unresolved),
            "error": len(errored),
            "missing": len(selected_ids - reported),
        },
    }
    evaluation_path = eval_dir / "evaluation.json"
    atomic_write_json(evaluation_path, record)
    manifest["evaluations"].append(
        {
            "evaluation_id": evaluation_id,
            "path": str(evaluation_path.relative_to(run_dir)),
            "created_at": record["created_at"],
            "selection_sha256": selection_digest,
            "record_sha256": sha256_file(evaluation_path),
        }
    )
    manifest["latest_evaluation"] = evaluation_id
    save_manifest(run_dir, manifest)
    print(evaluation_path)


def latest_evaluation(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any] | None:
    evaluation_id = manifest.get("latest_evaluation")
    if not evaluation_id:
        return None
    matches = [
        item
        for item in manifest["evaluations"]
        if item.get("evaluation_id") == evaluation_id
    ]
    if len(matches) != 1:
        raise ArtifactError("latest evaluation reference is missing or duplicated")
    rel_path = Path(matches[0]["path"])
    expected = (
        Path("reports")
        / "evaluations"
        / component(evaluation_id, "evaluation id")
        / "evaluation.json"
    )
    if rel_path != expected:
        raise ArtifactError("latest evaluation path is outside its expected location")
    evaluation_path = run_dir / rel_path
    record = read_json(evaluation_path, "evaluation record")
    if matches[0].get("record_sha256") != sha256_file(evaluation_path):
        raise ArtifactError("latest evaluation record changed after finalization")
    return record


def build_summary(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    evaluation = latest_evaluation(run_dir, manifest)
    outcomes = evaluation.get("outcomes", {}) if evaluation else {}
    rows: list[dict[str, Any]] = []
    for instance_id in manifest["dataset"]["instance_ids"]:
        task = task_for(manifest, instance_id)
        latest = task["attempts"][-1] if task["attempts"] else None
        rows.append(
            {
                "instance_id": instance_id,
                "attempts": len(task["attempts"]),
                "latest_attempt": latest.get("attempt_id") if latest else None,
                "status": (
                    latest.get("outcome")
                    if latest and latest.get("state") == FINALIZED
                    else latest.get("state")
                    if latest
                    else "pending"
                ),
                "patch_bytes": (latest.get("patch") or {}).get("bytes") if latest else None,
                "selected_attempt": task.get("selected_attempt"),
                "local_eval": outcomes.get(instance_id),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": manifest["run_id"],
        "agent": manifest["agent"],
        "profile": manifest["profile"],
        "planned": len(rows),
        "attempted": sum(row["attempts"] > 0 for row in rows),
        "selected": sum(row["selected_attempt"] is not None for row in rows),
        "resolved": sum(row["local_eval"] == "resolved" for row in rows),
        "failed": sum(row["local_eval"] == "failed" for row in rows),
        "errored": sum(row["local_eval"] == "error" for row in rows),
        "timed_out": sum(row["status"] == "timed_out" for row in rows),
        "rows": rows,
    }


def cmd_summary(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = load_manifest(run_dir)
    summary = build_summary(run_dir, manifest)
    if args.output:
        atomic_write_json(Path(args.output), summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_cleanup_partial(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = load_manifest(run_dir)
    candidates: list[tuple[str, dict[str, Any], Path]] = []
    for instance_id in manifest["dataset"]["instance_ids"]:
        task = task_for(manifest, instance_id)
        selected_id = task.get("selected_attempt")
        for descriptor in task["attempts"]:
            if (
                descriptor.get("state") in {FINALIZED, "cleaned"}
                or descriptor.get("attempt_id") == selected_id
            ):
                continue
            attempt_dir = attempt_dir_from_descriptor(run_dir, instance_id, descriptor)
            candidates.append((instance_id, descriptor, attempt_dir))

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{mode}: {len(candidates)} manifest-listed partial attempt(s)")
    for instance_id, descriptor, attempt_dir in candidates:
        print(f"  {instance_id}/{descriptor['attempt_id']}: {attempt_dir}")
        if not args.apply:
            continue
        if attempt_dir.exists():
            ensure_plain_directory(attempt_dir, "partial attempt directory")
            shutil.rmtree(attempt_dir)
        descriptor["state"] = "cleaned"
        descriptor["cleaned_at"] = utc_now()
    if args.apply and candidates:
        save_manifest(run_dir, manifest)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-run")
    create.add_argument("--runs-dir", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--agent", required=True)
    create.add_argument("--profile", default="default")
    create.add_argument("--dataset-name", required=True)
    create.add_argument("--dataset-split", default="test")
    create.add_argument("--dataset-sha256", required=True)
    create.add_argument("--runner-commit", required=True)
    create.add_argument("--runner-dirty", action="store_true")
    create.add_argument("--timeout-seconds", type=int, required=True)
    create.add_argument("--instances-file", required=True)
    create.set_defaults(func=cmd_create_run)

    resolve = sub.add_parser("resolve-run")
    resolve.add_argument("--runs-dir", required=True)
    resolve.add_argument("--agent", required=True)
    resolve.add_argument("--run-id")
    resolve.set_defaults(func=cmd_resolve_run)

    begin = sub.add_parser("begin-attempt")
    begin.add_argument("--run-dir", required=True)
    begin.add_argument("--instance-id", required=True)
    begin.set_defaults(func=cmd_begin_attempt)

    state = sub.add_parser("task-state")
    state.add_argument("--run-dir", required=True)
    state.add_argument("--instance-id", required=True)
    state.set_defaults(func=cmd_task_state)

    finalize = sub.add_parser("finalize-attempt")
    finalize.add_argument("--run-dir", required=True)
    finalize.add_argument("--instance-id", required=True)
    finalize.add_argument("--attempt-id", required=True)
    finalize.set_defaults(func=cmd_finalize_attempt)

    select = sub.add_parser("select-attempt")
    select.add_argument("--run-dir", required=True)
    select.add_argument("--instance-id", required=True)
    select.add_argument("--attempt-id", required=True)
    select.set_defaults(func=cmd_select_attempt)

    predictions = sub.add_parser("build-predictions")
    predictions.add_argument("--run-dir", required=True)
    predictions.add_argument("--output", required=True)
    predictions.add_argument("--selection-output", required=True)
    predictions.set_defaults(func=cmd_build_predictions)

    evaluation = sub.add_parser("record-evaluation")
    evaluation.add_argument("--run-dir", required=True)
    evaluation.add_argument("--evaluation-id", required=True)
    evaluation.add_argument("--report-file", required=True)
    evaluation.add_argument("--selection-file", required=True)
    evaluation.set_defaults(func=cmd_record_evaluation)

    summary = sub.add_parser("summary")
    summary.add_argument("--run-dir", required=True)
    summary.add_argument("--output")
    summary.set_defaults(func=cmd_summary)

    cleanup = sub.add_parser("cleanup-partial")
    cleanup.add_argument("--run-dir", required=True)
    cleanup.add_argument("--apply", action="store_true")
    cleanup.set_defaults(func=cmd_cleanup_partial)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        args.func(args)
    except (ArtifactError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
