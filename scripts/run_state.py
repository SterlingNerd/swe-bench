#!/usr/bin/env python3
"""Transactional scheduler state for manifest-backed SWE-bench runs.

SQLite is the scheduling authority.  ``manifest.json`` remains a readable audit
export and the immutable-attempt contract remains owned by ``run_artifacts.py``.
The database deliberately uses the rollback journal instead of WAL: every
supported host gets cross-process locking without depending on network-safe WAL
shared memory or on a particular SQLite WAL bug-fix release.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
TASK_STATES = {
    "planned",
    "preparing",
    "running",
    "checkpointing",
    "collected",
    "terminal",
    "retryable",
}
INFRASTRUCTURE_OUTCOMES = {
    "image_pull_rate_limited",
    "image_pull_authentication_error",
    "image_not_found",
    "registry_unavailable",
    "image_digest_mismatch",
    "image_storage_exhausted",
    "docker_daemon_unavailable",
    "evaluation_harness_error",
    "supervisor_lease_expired",
}


class StateError(RuntimeError):
    """A fail-closed scheduler-state violation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"cannot read JSON document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StateError(f"JSON document must be an object: {path}")
    return value


def state_path(run_dir: Path) -> Path:
    return run_dir / "state.sqlite"


def connect(run_dir: Path, *, initialize: bool = False) -> sqlite3.Connection:
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise StateError(f"run directory is not a plain directory: {run_dir}")
    path = state_path(run_dir)
    if not initialize and not path.is_file():
        raise StateError(f"scheduler database is missing: {path}")
    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    # WAL is intentionally avoided; see module docstring.
    mode = str(conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
    if mode not in {"delete", "truncate", "persist"}:
        conn.close()
        raise StateError(f"unsupported SQLite journal mode: {mode}")
    return conn


@contextlib.contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    profile TEXT NOT NULL,
    worker_count INTEGER NOT NULL DEFAULT 1 CHECK(worker_count = 1),
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    instance_id TEXT PRIMARY KEY,
    ordinal INTEGER NOT NULL UNIQUE,
    state TEXT NOT NULL,
    outcome TEXT,
    evaluation_outcome TEXT,
    selected_attempt TEXT,
    preparation_json TEXT,
    owner TEXT,
    lease_expires_at REAL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    instance_id TEXT NOT NULL REFERENCES tasks(instance_id),
    attempt_id TEXT NOT NULL,
    state TEXT NOT NULL,
    outcome TEXT,
    owner TEXT,
    lease_expires_at REAL,
    created_at TEXT NOT NULL,
    finalized_at TEXT,
    PRIMARY KEY(instance_id, attempt_id)
);
CREATE TABLE IF NOT EXISTS leases (
    resource_type TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(resource_type, resource_key)
);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    instance_id TEXT,
    attempt_id TEXT,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS image_leases (
    digest TEXT NOT NULL,
    holder TEXT NOT NULL,
    purpose TEXT NOT NULL,
    expires_at REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(digest, holder)
);
CREATE TABLE IF NOT EXISTS circuit_breakers (
    name TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    reason TEXT,
    retry_after REAL,
    trip_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budgets (
    name TEXT PRIMARY KEY,
    limit_value INTEGER NOT NULL,
    used_value INTEGER NOT NULL DEFAULT 0,
    unit TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    counts_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_state_idx ON attempts(state);
CREATE INDEX IF NOT EXISTS leases_expiry_idx ON leases(expires_at);
CREATE INDEX IF NOT EXISTS events_task_idx ON events(instance_id, event_id);
CREATE INDEX IF NOT EXISTS image_leases_expiry_idx ON image_leases(expires_at);
"""


def emit(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    instance_id: str | None = None,
    attempt_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events(created_at,event_type,instance_id,attempt_id,payload_json) "
        "VALUES(?,?,?,?,?)",
        (utc_now(), event_type, instance_id, attempt_id, json.dumps(payload or {}, sort_keys=True)),
    )


def export_events(run_dir: Path, conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT event_id,created_at,event_type,instance_id,attempt_id,payload_json "
        "FROM events ORDER BY event_id"
    ).fetchall()
    data = bytearray()
    for row in rows:
        record = {
            "event_id": row["event_id"],
            "created_at": row["created_at"],
            "event_type": row["event_type"],
            "instance_id": row["instance_id"],
            "attempt_id": row["attempt_id"],
            "payload": json.loads(row["payload_json"]),
        }
        data.extend((json.dumps(record, sort_keys=True) + "\n").encode())
    atomic_write(run_dir / "events.jsonl", bytes(data))


def export_manifest_scheduler(run_dir: Path, conn: sqlite3.Connection) -> None:
    path = run_dir / "manifest.json"
    manifest = read_json(path)
    rows = conn.execute(
        "SELECT instance_id,state,outcome,evaluation_outcome,owner,lease_expires_at,"
        "retry_count,preparation_json FROM tasks ORDER BY ordinal"
    ).fetchall()
    for row in rows:
        task = manifest.get("tasks", {}).get(row["instance_id"])
        if not isinstance(task, dict):
            continue
        task["scheduler"] = {
            "state": row["state"],
            "outcome": row["outcome"],
            "evaluation_outcome": row["evaluation_outcome"],
            "owner": row["owner"],
            "lease_expires_at": row["lease_expires_at"],
            "retry_count": row["retry_count"],
            "preparation": json.loads(row["preparation_json"]) if row["preparation_json"] else None,
        }
    manifest["orchestration"] = {
        "schema_version": SCHEMA_VERSION,
        "authority": "state.sqlite",
        "journal_mode": "delete",
        "updated_at": utc_now(),
    }
    manifest["updated_at"] = utc_now()
    atomic_write(path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())


def export_all(run_dir: Path, conn: sqlite3.Connection) -> None:
    export_events(run_dir, conn)
    export_manifest_scheduler(run_dir, conn)


def migrate(conn: sqlite3.Connection) -> None:
    has_migrations = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if has_migrations:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        version = row[0] if row and row[0] is not None else 0
        if version > SCHEMA_VERSION:
            raise StateError(f"scheduler schema {version} is newer than supported {SCHEMA_VERSION}")
    # sqlite3.executescript() manages its own transaction boundary.  Run the
    # idempotent DDL first, then record the forward-only migration atomically.
    conn.executescript(SCHEMA)
    with immediate(conn):
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        version = row[0] if row and row[0] is not None else 0
        if version > SCHEMA_VERSION:
            raise StateError(f"scheduler schema {version} is newer than supported {SCHEMA_VERSION}")
        if version < 1:
            conn.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
                (1, utc_now()),
            )


def manifest_task_state(task: dict[str, Any]) -> tuple[str, str | None]:
    attempts = task.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return "planned", None
    latest = attempts[-1]
    if latest.get("state") == "cleaned":
        return "planned", None
    if latest.get("state") == "finalized":
        return "terminal", latest.get("outcome")
    return "retryable", "supervisor_lease_expired"


def initialize(run_dir: Path) -> sqlite3.Connection:
    manifest = read_json(run_dir / "manifest.json")
    run_id = manifest.get("run_id")
    if run_id != run_dir.name:
        raise StateError("manifest run id does not match directory")
    instance_ids = manifest.get("dataset", {}).get("instance_ids")
    tasks = manifest.get("tasks")
    if not isinstance(instance_ids, list) or not isinstance(tasks, dict):
        raise StateError("manifest lacks ordered tasks")
    conn = connect(run_dir, initialize=True)
    migrate(conn)
    now = utc_now()
    with immediate(conn):
        conn.execute(
            "INSERT OR IGNORE INTO runs(run_id,agent,profile,worker_count,state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                run_id,
                str(manifest.get("agent")),
                str(manifest.get("profile", "default")),
                1,
                "active",
                str(manifest.get("created_at", now)),
                now,
            ),
        )
        for ordinal, instance_id in enumerate(instance_ids):
            task_doc = tasks.get(instance_id)
            if not isinstance(instance_id, str) or not isinstance(task_doc, dict):
                raise StateError(f"invalid manifest task: {instance_id!r}")
            state, outcome = manifest_task_state(task_doc)
            conn.execute(
                "INSERT OR IGNORE INTO tasks(instance_id,ordinal,state,outcome,selected_attempt,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (instance_id, ordinal, state, outcome, task_doc.get("selected_attempt"), now),
            )
            conn.execute(
                "UPDATE tasks SET selected_attempt=COALESCE(?,selected_attempt) WHERE instance_id=?",
                (task_doc.get("selected_attempt"), instance_id),
            )
            for descriptor in task_doc.get("attempts", []):
                if not isinstance(descriptor, dict) or not descriptor.get("attempt_id"):
                    continue
                attempt_state = (
                    "finalized" if descriptor.get("state") == "finalized" else "interrupted"
                )
                conn.execute(
                    "INSERT OR IGNORE INTO attempts(instance_id,attempt_id,state,outcome,created_at,finalized_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        instance_id,
                        descriptor["attempt_id"],
                        attempt_state,
                        descriptor.get("outcome"),
                        descriptor.get("created_at") or now,
                        descriptor.get("finalized_at"),
                    ),
                )
        task_count = len(instance_ids)
        defaults = (
            ("model_attempts", task_count, "attempts"),
            ("infrastructure_retries", max(3, task_count // 10), "attempts"),
            ("runtime_seconds", int(os.environ.get("SWE_RUN_MAX_SECONDS", "0")), "seconds"),
        )
        for name, limit_value, unit in defaults:
            conn.execute(
                "INSERT OR IGNORE INTO budgets(name,limit_value,unit,updated_at) VALUES(?,?,?,?)",
                (name, limit_value, unit, now),
            )
        existing_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if not existing_events:
            emit(conn, "run_state_initialized", payload={"tasks": task_count})
    export_all(run_dir, conn)
    return conn


def require_task(conn: sqlite3.Connection, instance_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE instance_id=?", (instance_id,)).fetchone()
    if row is None:
        raise StateError(f"task is not planned in this run: {instance_id}")
    return row


def circuit_guard(conn: sqlite3.Connection) -> None:
    now = time.time()
    rows = conn.execute(
        "SELECT name,reason,retry_after FROM circuit_breakers WHERE state='open'"
    ).fetchall()
    blocked = []
    for row in rows:
        retry_after = row["retry_after"]
        if retry_after is not None and retry_after <= now:
            conn.execute(
                "UPDATE circuit_breakers SET state='closed',updated_at=? WHERE name=?",
                (utc_now(), row["name"]),
            )
        else:
            blocked.append(f"{row['name']}: {row['reason'] or 'open'}")
    if blocked:
        raise StateError("scheduling paused by circuit breaker(s): " + "; ".join(blocked))


def lease_expiry(seconds: int) -> float:
    if seconds <= 0:
        raise StateError("lease seconds must be positive")
    return time.time() + seconds


def cmd_init(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = initialize(run_dir)
    conn.close()
    print(state_path(run_dir))


def cmd_claim(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = initialize(run_dir)
    expiry = lease_expiry(args.lease_seconds)
    with immediate(conn):
        circuit_guard(conn)
        row = require_task(conn, args.instance_id)
        now_epoch = time.time()
        allowed = row["state"] == "planned" or (
            row["state"] == "retryable" and args.allow_retryable
        )
        if row["state"] in {"preparing", "running", "checkpointing"}:
            allowed = row["owner"] == args.owner or (
                row["lease_expires_at"] is not None and row["lease_expires_at"] <= now_epoch
            )
        if not allowed:
            raise StateError(
                f"task {args.instance_id} is {row['state']}; requested claim is not eligible"
            )
        conn.execute(
            "INSERT INTO leases(resource_type,resource_key,owner,expires_at,metadata_json,updated_at) "
            "VALUES('task',?,?,?,?,?) ON CONFLICT(resource_type,resource_key) DO UPDATE SET "
            "owner=excluded.owner,expires_at=excluded.expires_at,metadata_json=excluded.metadata_json,"
            "updated_at=excluded.updated_at",
            (args.instance_id, args.owner, expiry, "{}", utc_now()),
        )
        conn.execute(
            "UPDATE tasks SET state='preparing',outcome=NULL,owner=?,lease_expires_at=?,updated_at=? "
            "WHERE instance_id=?",
            (args.owner, expiry, utc_now(), args.instance_id),
        )
        emit(
            conn,
            "preparation_started",
            instance_id=args.instance_id,
            payload={"owner": args.owner, "lease_expires_at": expiry},
        )
    export_all(run_dir, conn)
    conn.close()
    print(json.dumps({"instance_id": args.instance_id, "state": "preparing", "lease_expires_at": expiry}))


def load_payload(value: str | None, file_value: str | None) -> dict[str, Any]:
    if value and file_value:
        raise StateError("use either --payload or --payload-file")
    if file_value:
        return read_json(Path(file_value))
    if value:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise StateError("payload must be a JSON object")
        return parsed
    return {}


def require_owner(row: sqlite3.Row, owner: str) -> None:
    if row["owner"] != owner:
        raise StateError(f"task lease is owned by {row['owner']!r}, not {owner!r}")
    if row["lease_expires_at"] is None or row["lease_expires_at"] <= time.time():
        raise StateError("task lease has expired")


def cmd_prepared(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    payload = load_payload(args.payload, args.payload_file)
    with immediate(conn):
        row = require_task(conn, args.instance_id)
        require_owner(row, args.owner)
        if row["state"] != "preparing":
            raise StateError(f"task is not preparing: {row['state']}")
        conn.execute(
            "UPDATE tasks SET preparation_json=?,updated_at=? WHERE instance_id=?",
            (json.dumps(payload, sort_keys=True), utc_now(), args.instance_id),
        )
        emit(conn, "preparation_completed", instance_id=args.instance_id, payload=payload)
    export_all(run_dir, conn)
    conn.close()


def consume_budget(conn: sqlite3.Connection, name: str, amount: int) -> None:
    row = conn.execute("SELECT * FROM budgets WHERE name=?", (name,)).fetchone()
    if row is None:
        raise StateError(f"unknown budget: {name}")
    new_value = row["used_value"] + amount
    if row["limit_value"] > 0 and new_value > row["limit_value"]:
        raise StateError(
            f"budget {name} exhausted: {new_value} {row['unit']} > {row['limit_value']}"
        )
    conn.execute(
        "UPDATE budgets SET used_value=?,updated_at=? WHERE name=?",
        (new_value, utc_now(), name),
    )


def cmd_start_attempt(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    expiry = lease_expiry(args.lease_seconds)
    with immediate(conn):
        row = require_task(conn, args.instance_id)
        require_owner(row, args.owner)
        if row["state"] != "preparing" or not row["preparation_json"]:
            raise StateError("attempt allocation requires completed preparation")
        consume_budget(conn, "model_attempts", 1)
        conn.execute(
            "INSERT INTO attempts(instance_id,attempt_id,state,owner,lease_expires_at,created_at) "
            "VALUES(?,?,'running',?,?,?)",
            (args.instance_id, args.attempt_id, args.owner, expiry, utc_now()),
        )
        conn.execute(
            "UPDATE tasks SET state='running',owner=?,lease_expires_at=?,updated_at=? WHERE instance_id=?",
            (args.owner, expiry, utc_now(), args.instance_id),
        )
        conn.execute(
            "UPDATE leases SET expires_at=?,updated_at=? WHERE resource_type='task' AND resource_key=?",
            (expiry, utc_now(), args.instance_id),
        )
        emit(
            conn,
            "attempt_started",
            instance_id=args.instance_id,
            attempt_id=args.attempt_id,
            payload={"owner": args.owner},
        )
    export_all(run_dir, conn)
    conn.close()


def cmd_checkpointing(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    with immediate(conn):
        row = require_task(conn, args.instance_id)
        require_owner(row, args.owner)
        conn.execute(
            "UPDATE tasks SET state='checkpointing',updated_at=? WHERE instance_id=?",
            (utc_now(), args.instance_id),
        )
        conn.execute(
            "UPDATE attempts SET state='checkpointing' WHERE instance_id=? AND attempt_id=?",
            (args.instance_id, args.attempt_id),
        )
        emit(conn, "checkpointing", instance_id=args.instance_id, attempt_id=args.attempt_id)
    export_all(run_dir, conn)
    conn.close()


def cmd_collected(args: argparse.Namespace) -> None:
    """Record that durable artifacts exist before terminal classification."""
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    with immediate(conn):
        row = require_task(conn, args.instance_id)
        require_owner(row, args.owner)
        if row["state"] not in {"running", "checkpointing"}:
            raise StateError(f"task cannot become collected from {row['state']}")
        conn.execute(
            "UPDATE tasks SET state='collected',updated_at=? WHERE instance_id=?",
            (utc_now(), args.instance_id),
        )
        conn.execute(
            "UPDATE attempts SET state='collected' WHERE instance_id=? AND attempt_id=?",
            (args.instance_id, args.attempt_id),
        )
        emit(conn, "artifacts_collected", instance_id=args.instance_id, attempt_id=args.attempt_id)
    export_all(run_dir, conn)
    conn.close()


def cmd_heartbeat(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    expiry = lease_expiry(args.lease_seconds)
    payload = load_payload(args.payload, args.payload_file)
    with immediate(conn):
        row = require_task(conn, args.instance_id)
        require_owner(row, args.owner)
        conn.execute(
            "UPDATE tasks SET lease_expires_at=?,updated_at=? WHERE instance_id=?",
            (expiry, utc_now(), args.instance_id),
        )
        conn.execute(
            "UPDATE attempts SET lease_expires_at=? WHERE instance_id=? AND attempt_id=?",
            (expiry, args.instance_id, args.attempt_id),
        )
        conn.execute(
            "UPDATE leases SET expires_at=?,updated_at=? WHERE resource_type='task' AND resource_key=?",
            (expiry, utc_now(), args.instance_id),
        )
        emit(
            conn,
            "heartbeat",
            instance_id=args.instance_id,
            attempt_id=args.attempt_id,
            payload=payload,
        )
    export_events(run_dir, conn)
    conn.close()


def finish_state(outcome: str, retryable: bool) -> str:
    return "retryable" if retryable else "terminal"


def cmd_finish(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    payload = load_payload(args.payload, args.payload_file)
    retryable = args.retryable or args.outcome in INFRASTRUCTURE_OUTCOMES
    with immediate(conn):
        row = require_task(conn, args.instance_id)
        require_owner(row, args.owner)
        if row["state"] != "collected":
            raise StateError(f"task must be collected before finish, not {row['state']}")
        state = finish_state(args.outcome, retryable)
        if retryable:
            consume_budget(conn, "infrastructure_retries", 1)
        conn.execute(
            "UPDATE attempts SET state='finalized',outcome=?,finalized_at=?,lease_expires_at=NULL "
            "WHERE instance_id=? AND attempt_id=?",
            (args.outcome, utc_now(), args.instance_id, args.attempt_id),
        )
        conn.execute(
            "UPDATE tasks SET state=?,outcome=?,owner=NULL,lease_expires_at=NULL,"
            "retry_count=retry_count+?,updated_at=? WHERE instance_id=?",
            (state, args.outcome, 1 if retryable else 0, utc_now(), args.instance_id),
        )
        conn.execute(
            "DELETE FROM leases WHERE resource_type='task' AND resource_key=?",
            (args.instance_id,),
        )
        emit(
            conn,
            "attempt_finished",
            instance_id=args.instance_id,
            attempt_id=args.attempt_id,
            payload={"outcome": args.outcome, "retryable": retryable, **payload},
        )
    export_all(run_dir, conn)
    conn.close()


def cmd_abandon(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    payload = load_payload(args.payload, args.payload_file)
    with immediate(conn):
        row = require_task(conn, args.instance_id)
        require_owner(row, args.owner)
        conn.execute(
            "UPDATE tasks SET state='retryable',outcome=?,owner=NULL,lease_expires_at=NULL,"
            "retry_count=retry_count+1,updated_at=? WHERE instance_id=?",
            (args.outcome, utc_now(), args.instance_id),
        )
        conn.execute(
            "DELETE FROM leases WHERE resource_type='task' AND resource_key=?",
            (args.instance_id,),
        )
        emit(
            conn,
            "preparation_failed",
            instance_id=args.instance_id,
            payload={"outcome": args.outcome, **payload},
        )
    export_all(run_dir, conn)
    conn.close()


def cmd_reclaim(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = initialize(run_dir)
    now_epoch = time.time()
    reclaimed: list[str] = []
    with immediate(conn):
        rows = conn.execute(
            "SELECT * FROM tasks WHERE state IN ('preparing','running','checkpointing') "
            "AND lease_expires_at IS NOT NULL AND lease_expires_at<=?",
            (now_epoch,),
        ).fetchall()
        for row in rows:
            instance_id = row["instance_id"]
            conn.execute(
                "UPDATE tasks SET state='retryable',outcome='supervisor_lease_expired',"
                "owner=NULL,lease_expires_at=NULL,retry_count=retry_count+1,updated_at=? "
                "WHERE instance_id=?",
                (utc_now(), instance_id),
            )
            conn.execute(
                "UPDATE attempts SET state='interrupted',outcome='supervisor_lease_expired',"
                "lease_expires_at=NULL WHERE instance_id=? AND state!='finalized'",
                (instance_id,),
            )
            conn.execute(
                "DELETE FROM leases WHERE resource_type='task' AND resource_key=?",
                (instance_id,),
            )
            emit(
                conn,
                "lease_reclaimed",
                instance_id=instance_id,
                payload={"previous_owner": row["owner"]},
            )
            reclaimed.append(instance_id)
        conn.execute("DELETE FROM image_leases WHERE expires_at<=?", (now_epoch,))
    export_all(run_dir, conn)
    conn.close()
    print(json.dumps({"reclaimed": reclaimed, "count": len(reclaimed)}))


def cmd_task_state(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = initialize(run_dir)
    row = require_task(conn, args.instance_id)
    print(row["state"])
    conn.close()


def cmd_status(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = initialize(run_dir)
    rows = conn.execute(
        "SELECT instance_id,state,outcome,evaluation_outcome,retry_count,selected_attempt,"
        "owner,lease_expires_at FROM tasks ORDER BY ordinal"
    ).fetchall()
    budgets = [dict(row) for row in conn.execute("SELECT * FROM budgets ORDER BY name")]
    circuits = [
        dict(row) for row in conn.execute("SELECT * FROM circuit_breakers ORDER BY name")
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_dir.name,
        "planned": len(rows),
        "counts": {
            state: sum(row["state"] == state for row in rows) for state in sorted(TASK_STATES)
        },
        "tasks": [dict(row) for row in rows],
        "budgets": budgets,
        "circuit_breakers": circuits,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    conn.close()


def cmd_emit(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    payload = load_payload(args.payload, args.payload_file)
    with immediate(conn):
        emit(
            conn,
            args.event_type,
            instance_id=args.instance_id,
            attempt_id=args.attempt_id,
            payload=payload,
        )
    export_events(run_dir, conn)
    conn.close()


def cmd_image_acquire(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    expiry = lease_expiry(args.lease_seconds)
    with immediate(conn):
        conn.execute("DELETE FROM image_leases WHERE expires_at<=?", (time.time(),))
        conn.execute(
            "INSERT INTO image_leases(digest,holder,purpose,expires_at,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(digest,holder) DO UPDATE SET purpose=excluded.purpose,"
            "expires_at=excluded.expires_at,updated_at=excluded.updated_at",
            (args.digest, args.holder, args.purpose, expiry, utc_now()),
        )
        emit(
            conn,
            "image_lease_acquired",
            instance_id=args.instance_id,
            attempt_id=args.attempt_id,
            payload={"digest": args.digest, "holder": args.holder, "purpose": args.purpose},
        )
    export_events(run_dir, conn)
    conn.close()


def cmd_image_release(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    with immediate(conn):
        conn.execute(
            "DELETE FROM image_leases WHERE digest=? AND holder=?", (args.digest, args.holder)
        )
        emit(
            conn,
            "image_lease_released",
            instance_id=args.instance_id,
            attempt_id=args.attempt_id,
            payload={"digest": args.digest, "holder": args.holder},
        )
    export_events(run_dir, conn)
    conn.close()


def cmd_image_holders(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    conn.execute("DELETE FROM image_leases WHERE expires_at<=?", (time.time(),))
    rows = conn.execute(
        "SELECT holder,purpose,expires_at FROM image_leases WHERE digest=? ORDER BY holder",
        (args.digest,),
    ).fetchall()
    print(json.dumps([dict(row) for row in rows], sort_keys=True))
    conn.close()


def cmd_lease_acquire(args: argparse.Namespace) -> None:
    """Acquire a generic expiring lease, used for single-flight registry seed."""
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    expiry = lease_expiry(args.lease_seconds)
    acquired = False
    with immediate(conn):
        now_epoch = time.time()
        row = conn.execute(
            "SELECT owner,expires_at FROM leases WHERE resource_type=? AND resource_key=?",
            (args.resource_type, args.resource_key),
        ).fetchone()
        if row is None or row["owner"] == args.owner or row["expires_at"] <= now_epoch:
            conn.execute(
                "INSERT INTO leases(resource_type,resource_key,owner,expires_at,metadata_json,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(resource_type,resource_key) DO UPDATE SET "
                "owner=excluded.owner,expires_at=excluded.expires_at,"
                "metadata_json=excluded.metadata_json,updated_at=excluded.updated_at",
                (
                    args.resource_type,
                    args.resource_key,
                    args.owner,
                    expiry,
                    json.dumps(load_payload(args.payload, args.payload_file), sort_keys=True),
                    utc_now(),
                ),
            )
            emit(
                conn,
                "lease_acquired",
                payload={
                    "resource_type": args.resource_type,
                    "resource_key": args.resource_key,
                    "owner": args.owner,
                    "expires_at": expiry,
                },
            )
            acquired = True
    export_events(run_dir, conn)
    conn.close()
    print(json.dumps({"acquired": acquired, "expires_at": expiry if acquired else None}))
    if not acquired:
        raise SystemExit(3)


def cmd_lease_release(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    with immediate(conn):
        row = conn.execute(
            "SELECT owner FROM leases WHERE resource_type=? AND resource_key=?",
            (args.resource_type, args.resource_key),
        ).fetchone()
        if row is not None and row["owner"] != args.owner:
            raise StateError(f"lease belongs to {row['owner']!r}, not {args.owner!r}")
        conn.execute(
            "DELETE FROM leases WHERE resource_type=? AND resource_key=?",
            (args.resource_type, args.resource_key),
        )
        emit(
            conn,
            "lease_released",
            payload={
                "resource_type": args.resource_type,
                "resource_key": args.resource_key,
                "owner": args.owner,
            },
        )
    export_events(run_dir, conn)
    conn.close()


def cmd_circuit(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    conn = connect(run_dir)
    retry_after = time.time() + args.retry_seconds if args.retry_seconds > 0 else None
    with immediate(conn):
        if args.action == "trip":
            conn.execute(
                "INSERT INTO circuit_breakers(name,state,reason,retry_after,trip_count,updated_at) "
                "VALUES(?,'open',?,?,1,?) ON CONFLICT(name) DO UPDATE SET state='open',"
                "reason=excluded.reason,retry_after=excluded.retry_after,"
                "trip_count=circuit_breakers.trip_count+1,updated_at=excluded.updated_at",
                (args.name, args.reason, retry_after, utc_now()),
            )
            emit(
                conn,
                "circuit_opened",
                payload={"name": args.name, "reason": args.reason, "retry_after": retry_after},
            )
        else:
            conn.execute(
                "INSERT INTO circuit_breakers(name,state,updated_at) VALUES(?,'closed',?) "
                "ON CONFLICT(name) DO UPDATE SET state='closed',reason=NULL,retry_after=NULL,"
                "updated_at=excluded.updated_at",
                (args.name, utc_now()),
            )
            emit(conn, "circuit_closed", payload={"name": args.name})
    export_events(run_dir, conn)
    conn.close()


def cmd_record_evaluation(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    record = read_json(Path(args.evaluation_file))
    outcomes = record.get("outcomes")
    if not isinstance(outcomes, dict):
        raise StateError("evaluation record lacks outcomes")
    conn = connect(run_dir)
    with immediate(conn):
        conn.execute(
            "INSERT INTO evaluations(evaluation_id,created_at,counts_json) VALUES(?,?,?)",
            (
                str(record.get("evaluation_id")),
                str(record.get("created_at", utc_now())),
                json.dumps(record.get("counts", {}), sort_keys=True),
            ),
        )
        for instance_id, outcome in outcomes.items():
            require_task(conn, instance_id)
            conn.execute(
                "UPDATE tasks SET evaluation_outcome=?,updated_at=? WHERE instance_id=?",
                (str(outcome), utc_now(), instance_id),
            )
        emit(
            conn,
            "evaluation_recorded",
            payload={"evaluation_id": record.get("evaluation_id"), "counts": record.get("counts", {})},
        )
    export_all(run_dir, conn)
    conn.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--run-dir", required=True)
    init.set_defaults(func=cmd_init)

    claim = sub.add_parser("claim")
    claim.add_argument("--run-dir", required=True)
    claim.add_argument("--instance-id", required=True)
    claim.add_argument("--owner", required=True)
    claim.add_argument("--lease-seconds", type=int, default=120)
    claim.add_argument("--allow-retryable", action="store_true")
    claim.set_defaults(func=cmd_claim)

    prepared = sub.add_parser("prepared")
    prepared.add_argument("--run-dir", required=True)
    prepared.add_argument("--instance-id", required=True)
    prepared.add_argument("--owner", required=True)
    prepared.add_argument("--payload")
    prepared.add_argument("--payload-file")
    prepared.set_defaults(func=cmd_prepared)

    start = sub.add_parser("start-attempt")
    start.add_argument("--run-dir", required=True)
    start.add_argument("--instance-id", required=True)
    start.add_argument("--attempt-id", required=True)
    start.add_argument("--owner", required=True)
    start.add_argument("--lease-seconds", type=int, default=120)
    start.set_defaults(func=cmd_start_attempt)

    checkpoint = sub.add_parser("checkpointing")
    checkpoint.add_argument("--run-dir", required=True)
    checkpoint.add_argument("--instance-id", required=True)
    checkpoint.add_argument("--attempt-id", required=True)
    checkpoint.add_argument("--owner", required=True)
    checkpoint.set_defaults(func=cmd_checkpointing)

    collected = sub.add_parser("collected")
    collected.add_argument("--run-dir", required=True)
    collected.add_argument("--instance-id", required=True)
    collected.add_argument("--attempt-id", required=True)
    collected.add_argument("--owner", required=True)
    collected.set_defaults(func=cmd_collected)

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--run-dir", required=True)
    heartbeat.add_argument("--instance-id", required=True)
    heartbeat.add_argument("--attempt-id", required=True)
    heartbeat.add_argument("--owner", required=True)
    heartbeat.add_argument("--lease-seconds", type=int, default=120)
    heartbeat.add_argument("--payload")
    heartbeat.add_argument("--payload-file")
    heartbeat.set_defaults(func=cmd_heartbeat)

    finish = sub.add_parser("finish")
    finish.add_argument("--run-dir", required=True)
    finish.add_argument("--instance-id", required=True)
    finish.add_argument("--attempt-id", required=True)
    finish.add_argument("--owner", required=True)
    finish.add_argument("--outcome", required=True)
    finish.add_argument("--retryable", action="store_true")
    finish.add_argument("--payload")
    finish.add_argument("--payload-file")
    finish.set_defaults(func=cmd_finish)

    abandon = sub.add_parser("abandon-preparation")
    abandon.add_argument("--run-dir", required=True)
    abandon.add_argument("--instance-id", required=True)
    abandon.add_argument("--owner", required=True)
    abandon.add_argument("--outcome", required=True, choices=sorted(INFRASTRUCTURE_OUTCOMES))
    abandon.add_argument("--payload")
    abandon.add_argument("--payload-file")
    abandon.set_defaults(func=cmd_abandon)

    reclaim = sub.add_parser("reclaim-expired")
    reclaim.add_argument("--run-dir", required=True)
    reclaim.set_defaults(func=cmd_reclaim)

    task_state = sub.add_parser("task-state")
    task_state.add_argument("--run-dir", required=True)
    task_state.add_argument("--instance-id", required=True)
    task_state.set_defaults(func=cmd_task_state)

    status = sub.add_parser("status")
    status.add_argument("--run-dir", required=True)
    status.set_defaults(func=cmd_status)

    event = sub.add_parser("emit")
    event.add_argument("--run-dir", required=True)
    event.add_argument("--event-type", required=True)
    event.add_argument("--instance-id")
    event.add_argument("--attempt-id")
    event.add_argument("--payload")
    event.add_argument("--payload-file")
    event.set_defaults(func=cmd_emit)

    image_acquire = sub.add_parser("image-acquire")
    image_acquire.add_argument("--run-dir", required=True)
    image_acquire.add_argument("--digest", required=True)
    image_acquire.add_argument("--holder", required=True)
    image_acquire.add_argument("--purpose", required=True)
    image_acquire.add_argument("--lease-seconds", type=int, default=7200)
    image_acquire.add_argument("--instance-id")
    image_acquire.add_argument("--attempt-id")
    image_acquire.set_defaults(func=cmd_image_acquire)

    image_release = sub.add_parser("image-release")
    image_release.add_argument("--run-dir", required=True)
    image_release.add_argument("--digest", required=True)
    image_release.add_argument("--holder", required=True)
    image_release.add_argument("--instance-id")
    image_release.add_argument("--attempt-id")
    image_release.set_defaults(func=cmd_image_release)

    image_holders = sub.add_parser("image-holders")
    image_holders.add_argument("--run-dir", required=True)
    image_holders.add_argument("--digest", required=True)
    image_holders.set_defaults(func=cmd_image_holders)

    lease_acquire = sub.add_parser("lease-acquire")
    lease_acquire.add_argument("--run-dir", required=True)
    lease_acquire.add_argument("--resource-type", required=True)
    lease_acquire.add_argument("--resource-key", required=True)
    lease_acquire.add_argument("--owner", required=True)
    lease_acquire.add_argument("--lease-seconds", type=int, default=300)
    lease_acquire.add_argument("--payload")
    lease_acquire.add_argument("--payload-file")
    lease_acquire.set_defaults(func=cmd_lease_acquire)

    lease_release = sub.add_parser("lease-release")
    lease_release.add_argument("--run-dir", required=True)
    lease_release.add_argument("--resource-type", required=True)
    lease_release.add_argument("--resource-key", required=True)
    lease_release.add_argument("--owner", required=True)
    lease_release.set_defaults(func=cmd_lease_release)

    circuit = sub.add_parser("circuit")
    circuit.add_argument("--run-dir", required=True)
    circuit.add_argument("action", choices=["trip", "reset"])
    circuit.add_argument("--name", required=True)
    circuit.add_argument("--reason", default="")
    circuit.add_argument("--retry-seconds", type=int, default=0)
    circuit.set_defaults(func=cmd_circuit)

    evaluation = sub.add_parser("record-evaluation")
    evaluation.add_argument("--run-dir", required=True)
    evaluation.add_argument("--evaluation-file", required=True)
    evaluation.set_defaults(func=cmd_record_evaluation)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        args.func(args)
    except (StateError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
