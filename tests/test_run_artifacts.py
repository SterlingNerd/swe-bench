#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "scripts" / "run_artifacts.py"


class RunArtifactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.runs = self.root / "runs"
        self.instances = self.root / "instances.txt"
        self.instances.write_text("example__repo-1\nexample__repo-2\n")
        self.run_dir = self.create_run("test-run")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(TOOL), *map(str, args)],
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def create_run(self, run_id: str) -> Path:
        result = self.cli(
            "create-run",
            "--runs-dir",
            self.runs,
            "--run-id",
            run_id,
            "--agent",
            "pi",
            "--profile",
            "baseline-local",
            "--dataset-name",
            "example/dataset",
            "--dataset-split",
            "test",
            "--dataset-sha256",
            "a" * 64,
            "--runner-commit",
            "b" * 40,
            "--timeout-seconds",
            "60",
            "--instances-file",
            self.instances,
        )
        return Path(result.stdout.strip())

    def begin(self, instance_id: str) -> Path:
        result = self.cli(
            "begin-attempt",
            "--run-dir",
            self.run_dir,
            "--instance-id",
            instance_id,
        )
        return Path(result.stdout.strip())

    def finalize(self, instance_id: str, attempt: Path) -> dict[str, object]:
        result = self.cli(
            "finalize-attempt",
            "--run-dir",
            self.run_dir,
            "--instance-id",
            instance_id,
            "--attempt-id",
            attempt.name,
        )
        return json.loads(result.stdout)

    @staticmethod
    def write_result(attempt: Path, patch: str, status: str = "patch_collected") -> None:
        (attempt / "patch.diff").write_text(patch)
        (attempt / "result.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": status,
                    "patch_bytes": len(patch.encode()),
                    "checkpointed": True,
                }
            )
        )

    def test_attempts_are_one_way_and_first_eligible_selection_is_stable(self) -> None:
        first = self.begin("example__repo-1")
        self.write_result(first, "first patch\n")
        finalized = self.finalize("example__repo-1", first)
        self.assertTrue(finalized["eval_eligible"])

        second = self.begin("example__repo-1")
        self.assertEqual(second.name, "attempt-0002")
        self.write_result(second, "second patch\n")
        self.finalize("example__repo-1", second)

        manifest = json.loads((self.run_dir / "manifest.json").read_text())
        task = manifest["tasks"]["example__repo-1"]
        self.assertEqual(task["selected_attempt"], "attempt-0001")
        self.assertEqual(len(task["attempts"]), 2)

        predictions = self.root / "predictions.jsonl"
        selections = self.root / "selections.json"
        self.cli(
            "build-predictions",
            "--run-dir",
            self.run_dir,
            "--output",
            predictions,
            "--selection-output",
            selections,
        )
        prediction = json.loads(predictions.read_text())
        self.assertEqual(prediction["model_patch"], "first patch\n")

    def test_evaluation_is_an_overlay_and_does_not_mutate_attempt_result(self) -> None:
        attempt = self.begin("example__repo-1")
        self.write_result(attempt, "selected patch\n")
        self.finalize("example__repo-1", attempt)
        result_before = (attempt / "result.json").read_bytes()

        eval_dir = self.run_dir / "reports" / "evaluations" / "eval-one"
        eval_dir.mkdir(parents=True)
        predictions = eval_dir / "predictions.jsonl"
        selections = eval_dir / "selected-attempts.json"
        self.cli(
            "build-predictions",
            "--run-dir",
            self.run_dir,
            "--output",
            predictions,
            "--selection-output",
            selections,
        )
        report = eval_dir / "official.json"
        report.write_text(
            json.dumps(
                {
                    "resolved_ids": ["example__repo-1"],
                    "unresolved_ids": [],
                    "error_ids": [],
                }
            )
        )
        self.cli(
            "record-evaluation",
            "--run-dir",
            self.run_dir,
            "--evaluation-id",
            "eval-one",
            "--report-file",
            report,
            "--selection-file",
            selections,
        )

        self.assertEqual((attempt / "result.json").read_bytes(), result_before)
        overlay = json.loads((eval_dir / "evaluation.json").read_text())
        self.assertEqual(overlay["outcomes"], {"example__repo-1": "resolved"})
        summary = json.loads(
            self.cli("summary", "--run-dir", self.run_dir).stdout
        )
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(summary["selected"], 1)
        overlay["counts"]["resolved"] = 0
        (eval_dir / "evaluation.json").write_text(json.dumps(overlay))
        tampered = self.cli("summary", "--run-dir", self.run_dir, check=False)
        self.assertEqual(tampered.returncode, 2)
        self.assertIn("evaluation record changed", tampered.stderr)

    def test_digest_tampering_fails_closed(self) -> None:
        attempt = self.begin("example__repo-1")
        self.write_result(attempt, "original patch\n")
        self.finalize("example__repo-1", attempt)
        (attempt / "patch.diff").write_text("tampered patch\n")

        result = self.cli(
            "build-predictions",
            "--run-dir",
            self.run_dir,
            "--output",
            self.root / "predictions.jsonl",
            "--selection-output",
            self.root / "selections.json",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("changed after finalization", result.stderr)

    def test_result_tampering_fails_closed(self) -> None:
        attempt = self.begin("example__repo-1")
        self.write_result(attempt, "original patch\n")
        self.finalize("example__repo-1", attempt)
        result_doc = json.loads((attempt / "result.json").read_text())
        result_doc["elapsed_seconds"] = 999
        (attempt / "result.json").write_text(json.dumps(result_doc))

        result = self.cli(
            "build-predictions",
            "--run-dir",
            self.run_dir,
            "--output",
            self.root / "predictions.jsonl",
            "--selection-output",
            self.root / "selections.json",
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("result changed after finalization", result.stderr)

    def test_cleanup_is_manifest_bounded_and_dry_run_by_default(self) -> None:
        finalized = self.begin("example__repo-1")
        self.write_result(finalized, "keep me\n")
        self.finalize("example__repo-1", finalized)
        partial = self.begin("example__repo-2")
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("safe")

        dry_run = self.cli("cleanup-partial", "--run-dir", self.run_dir)
        self.assertIn("DRY-RUN: 1", dry_run.stdout)
        self.assertTrue(partial.exists())
        self.assertTrue(finalized.exists())
        self.assertTrue(sentinel.exists())

        applied = self.cli(
            "cleanup-partial", "--run-dir", self.run_dir, "--apply"
        )
        self.assertIn("APPLY: 1", applied.stdout)
        self.assertFalse(partial.exists())
        self.assertTrue(finalized.exists())
        self.assertTrue(sentinel.exists())
        state = self.cli(
            "task-state",
            "--run-dir",
            self.run_dir,
            "--instance-id",
            "example__repo-2",
        )
        self.assertEqual(state.stdout.strip(), "pending")


if __name__ == "__main__":
    unittest.main()
