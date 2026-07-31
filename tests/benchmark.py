"""Simple benchmarks for the SWE-bench orchestrator.

Run with: python tests/benchmark.py
"""

import time
from pathlib import Path


def benchmark_dataset_cache():
    """Benchmark DatasetCache operations."""
    from swebench_orchestrator.dataset import DatasetCache
    import json
    import tempfile

    # Create a large cache file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        data = [
            {
                "instance_id": f"repo__repo-{i}",
                "repo": f"repo{i % 10}/repo",
                "version": f"{i % 5}.{i % 3}.0",
                "testbed": "testbed",
                "problem_statement": f"Problem {i}",
                "hint_string": "",
                "base_commit": f"commit{i}",
                "patch": "",
                "test_patch": "",
                "failure_log": "",
                "created_at": "2024-01-01T00:00:00Z",
                "difficulty": "medium",
                "environment_commit_hash": f"hash{i}",
                "repo_directory": "/testbed",
            }
            for i in range(1000)
        ]
        json.dump(data, f)
        cache_file = f.name

    try:
        cache = DatasetCache(Path(cache_file))

        # Benchmark save
        start = time.perf_counter()
        cache.save(data)
        save_time = time.perf_counter() - start

        # Benchmark load
        start = time.perf_counter()
        _ = cache.data
        load_time = time.perf_counter() - start

        # Benchmark list with filter
        start = time.perf_counter()
        list(cache.list_instances(filter_str="repo5"))
        filter_time = time.perf_counter() - start

        print(f"DatasetCache benchmarks (1000 instances):")
        print(f"  Save:     {save_time*1000:.2f}ms")
        print(f"  Load:     {load_time*1000:.2f}ms")
        print(f"  Filter:   {filter_time*1000:.2f}ms")

    finally:
        Path(cache_file).unlink()


def benchmark_manifest_operations():
    """Benchmark manifest operations."""
    from swebench_orchestrator.manifest import RunManager
    import tempfile
    import shutil

    runs_dir = Path(tempfile.mkdtemp())

    try:
        manager = RunManager(runs_dir)

        # Benchmark run creation
        start = time.perf_counter()
        for i in range(10):
            manager.create_run(agent=f"agent{i % 3}", timeout=3600)
        create_time = time.perf_counter() - start

        # Benchmark attempt creation
        run = manager.create_run(agent="test")
        start = time.perf_counter()
        for i in range(100):
            manager.create_attempt(run.run_id, f"instance-{i}")
        attempt_time = time.perf_counter() - start

        print(f"Manifest benchmarks:")
        print(f"  Create run (10):    {create_time*1000:.2f}ms")
        print(f"  Create attempts (100): {attempt_time*1000:.2f}ms")

    finally:
        shutil.rmtree(runs_dir, ignore_errors=True)


def benchmark_summarize():
    """Benchmark summarize_results."""
    from swebench_orchestrator.runner import summarize_results
    import tempfile
    import json
    import shutil

    output_dir = Path(tempfile.mkdtemp()) / "test-agent"
    output_dir.mkdir(parents=True)

    # Create 100 instance directories with results
    for i in range(100):
        iid = f"repo__repo-{i}"
        instance_dir = output_dir / iid
        instance_dir.mkdir()
        (instance_dir / "result.json").write_text(json.dumps({
            "status": "patch_collected",
            "local_eval": "resolved" if i % 3 == 0 else ("failed" if i % 3 == 1 else None),
            "patch_bytes": 42,
            "elapsed_seconds": 10,
        }))

    try:
        start = time.perf_counter()
        summary = summarize_results(output_dir, agent="test-agent")
        summarize_time = time.perf_counter() - start

        print(f"Summarize benchmark (100 instances):")
        print(f"  Summarize: {summarize_time*1000:.2f}ms")
        print(f"  Total: {summary['total']}, Resolved: {summary['resolved']}")

    finally:
        shutil.rmtree(output_dir.parent, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("SWE-bench Orchestrator Benchmarks")
    print("=" * 60)
    print()

    benchmark_dataset_cache()
    print()
    benchmark_manifest_operations()
    print()
    benchmark_summarize()
    print()
    print("=" * 60)
