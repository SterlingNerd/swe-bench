#!/usr/bin/env python3
"""Focused smoke coverage for the P1A transactional state foundation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_state", ROOT / "scripts" / "run_state.py")
assert SPEC and SPEC.loader
run_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_state)


def arguments(**values: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "payload": None,
        "payload_file": None,
        "lease_seconds": 60,
        "allow_retryable": False,
        "retryable": False,
    }
    defaults.update(values)
    return argparse.Namespace(**defaults)


class RunStateSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="swe-run-state-")
        self.run_dir = Path(self.temp.name) / "smoke-run"
        self.run_dir.mkdir()
        self.instance_ids = ["django__django-1", "matplotlib__matplotlib-2"]
        manifest = {
            "schema_version": 1,
            "run_id": self.run_dir.name,
            "agent": "codex",
            "profile": "default",
            "created_at": "2026-07-19T00:00:00Z",
            "updated_at": "2026-07-19T00:00:00Z",
            "dataset": {"instance_ids": self.instance_ids},
            "tasks": {
                instance_id: {"instance_id": instance_id, "attempts": [], "selected_attempt": None}
                for instance_id in self.instance_ids
            },
        }
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.run_dir / "state.sqlite")
        connection.row_factory = sqlite3.Row
        return connection

    def test_initialize_migrates_and_exports_without_wal(self) -> None:
        connection = run_state.initialize(self.run_dir)
        self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual(connection.execute("SELECT version FROM schema_migrations").fetchone()[0], 1)
        run = connection.execute("SELECT worker_count FROM runs").fetchone()
        self.assertEqual(run[0], 1)
        states = [row[0] for row in connection.execute("SELECT state FROM tasks ORDER BY ordinal")]
        self.assertEqual(states, ["planned", "planned"])
        connection.close()

        exported = json.loads((self.run_dir / "manifest.json").read_text())
        self.assertEqual(exported["orchestration"]["authority"], "state.sqlite")
        self.assertEqual(exported["orchestration"]["journal_mode"], "delete")
        self.assertTrue((self.run_dir / "events.jsonl").is_file())

    def test_lifecycle_requires_preparation_and_collection(self) -> None:
        run_state.initialize(self.run_dir).close()
        common = {
            "run_dir": str(self.run_dir),
            "instance_id": self.instance_ids[0],
            "owner": "worker-a",
        }
        run_state.cmd_claim(arguments(**common))
        with self.assertRaises(run_state.StateError):
            run_state.cmd_start_attempt(arguments(**common, attempt_id="attempt-0001"))
        run_state.cmd_prepared(arguments(**common, payload='{"image":"ready"}'))
        run_state.cmd_start_attempt(arguments(**common, attempt_id="attempt-0001"))
        run_state.cmd_checkpointing(arguments(**common, attempt_id="attempt-0001"))
        run_state.cmd_collected(arguments(**common, attempt_id="attempt-0001"))
        run_state.cmd_finish(
            arguments(**common, attempt_id="attempt-0001", outcome="patch_collected")
        )

        connection = self.connect()
        task = connection.execute(
            "SELECT state,outcome,owner FROM tasks WHERE instance_id=?", (self.instance_ids[0],)
        ).fetchone()
        self.assertEqual(tuple(task), ("terminal", "patch_collected", None))
        attempt = connection.execute(
            "SELECT state,outcome FROM attempts WHERE attempt_id='attempt-0001'"
        ).fetchone()
        self.assertEqual(tuple(attempt), ("finalized", "patch_collected"))
        connection.close()

    def test_existing_finalized_attempt_is_reconciled_but_not_rewritten(self) -> None:
        manifest_path = self.run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        descriptor = {
            "attempt_id": "attempt-0001",
            "state": "finalized",
            "outcome": "no_patch",
            "created_at": "2026-07-19T00:01:00Z",
            "finalized_at": "2026-07-19T00:02:00Z",
            "patch_sha256": "a" * 64,
        }
        manifest["tasks"][self.instance_ids[0]]["attempts"] = [descriptor]
        manifest["tasks"][self.instance_ids[0]]["selected_attempt"] = "attempt-0001"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        expected = deepcopy(descriptor)

        run_state.initialize(self.run_dir).close()
        exported = json.loads(manifest_path.read_text())
        self.assertEqual(exported["tasks"][self.instance_ids[0]]["attempts"][0], expected)
        connection = self.connect()
        task = connection.execute(
            "SELECT state,outcome,selected_attempt FROM tasks WHERE instance_id=?",
            (self.instance_ids[0],),
        ).fetchone()
        self.assertEqual(tuple(task), ("terminal", "no_patch", "attempt-0001"))
        connection.close()

    def test_newer_schema_is_rejected_before_schema_changes(self) -> None:
        database = self.run_dir / "state.sqlite"
        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_migrations VALUES(99, 'future')")
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(run_state.StateError, "newer than supported"):
            run_state.initialize(self.run_dir)
        connection = sqlite3.connect(database)
        tasks_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        self.assertIsNone(tasks_table)
        connection.close()


if __name__ == "__main__":
    unittest.main()
