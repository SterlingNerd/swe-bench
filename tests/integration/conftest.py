"""Shared fixtures for integration tests."""

import json
import shutil
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture()
def test_workspace(tmp_path: Path) -> Path:
    """Create a clean test workspace with agents and outputs directories."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "harnesses").mkdir()
    (workspace / "outputs").mkdir()
    (workspace / "runs").mkdir()
    return workspace


@pytest.fixture()
def mock_agent(test_workspace: Path) -> Path:
    """Create a minimal test agent with build script and bundle.

    Returns path to the agent directory.
    """
    agent_dir = test_workspace / "harnesses" / "test-agent"
    bundle_dir = agent_dir / "bundle"
    bundle_dir.mkdir(parents=True)

    # Create build script
    build_script = agent_dir / "build_bundle.sh"
    build_script.write_text("""#!/bin/bash
set -euo pipefail
BUNDLE_DIR="${1:-./bundle}"
mkdir -p "$BUNDLE_DIR/bin"
echo '#!/bin/bash' > "$BUNDLE_DIR/bin/node"
echo 'echo mock' >> "$BUNDLE_DIR/bin/node"
chmod +x "$BUNDLE_DIR/bin/node"
""")
    build_script.chmod(0o755)

    # Create a dummy file in bundle so it's non-empty
    (bundle_dir / "dummy.txt").write_text("bundle content")

    return agent_dir


@pytest.fixture()
def cache_file(test_workspace: Path) -> Path:
    """Create a dataset cache file with test instances."""
    cache = test_workspace / "cache.json"
    data = [
        {
            "instance_id": "django__django-11039",
            "repo": "django/django",
            "version": "4.2",
            "testbed": "testbed",
            "problem_statement": "Test problem 1",
            "hint_string": "",
            "base_commit": "abc123",
            "patch": "",
            "test_patch": "",
            "failure_log": "",
            "created_at": "2024-01-01T00:00:00Z",
            "difficulty": "medium",
            "environment_commit_hash": "def456",
            "repo_directory": "/testbed",
        },
        {
            "instance_id": "flask__flask-1000",
            "repo": "pallets/flask",
            "version": "2.3",
            "testbed": "testbed",
            "problem_statement": "Test problem 2",
            "hint_string": "",
            "base_commit": "ghi789",
            "patch": "",
            "test_patch": "",
            "failure_log": "",
            "created_at": "2024-01-01T00:00:00Z",
            "difficulty": "easy",
            "environment_commit_hash": "jkl012",
            "repo_directory": "/testbed",
        },
    ]
    cache.write_text(json.dumps(data))
    return cache


@pytest.fixture()
def valid_cache(test_workspace: Path) -> Path:
    """Create a valid dataset cache file."""
    cache = test_workspace / "cache.json"
    data = [
        {
            "instance_id": "test__test-1",
            "repo": "test/test",
            "version": "1.0",
            "testbed": "testbed",
            "problem_statement": "Test",
            "hint_string": "",
            "base_commit": "abc",
            "patch": "",
            "test_patch": "",
            "failure_log": "",
            "created_at": "2024-01-01T00:00:00Z",
            "difficulty": "easy",
            "environment_commit_hash": "def",
            "repo_directory": "/testbed",
        },
    ]
    cache.write_text(json.dumps(data))
    return cache


@pytest.fixture()
def empty_cache(test_workspace: Path) -> Path:
    """Create an empty dataset cache file."""
    cache = test_workspace / "cache.json"
    cache.write_text("[]")
    return cache


@pytest.fixture()
def invalid_cache(test_workspace: Path) -> Path:
    """Create an invalid (corrupted) dataset cache file."""
    cache = test_workspace / "cache.json"
    cache.write_text("not valid json")
    return cache


@pytest.fixture()
def nonexistent_cache(test_workspace: Path) -> Path:
    """Path to a cache file that doesn't exist."""
    return test_workspace / "nonexistent_cache.json"


@pytest.fixture()
def agent_output_dir(test_workspace: Path) -> Path:
    """Path to an agent's output directory."""
    return test_workspace / "outputs" / "test-agent"


@pytest.fixture()
def instance_output_dir(agent_output_dir: Path, request) -> Path:
    """Path to a specific instance's output directory."""
    iid = getattr(request, "instance_id", "django__django-11039")
    return agent_output_dir / iid


@pytest.fixture()
def mock_docker_outputs(tmp_path: Path) -> Path:
    """Create mock docker output files as if copied from a container.

    Returns path to the temp directory containing nested instance output.
    """
    outputs = tmp_path / "docker_cp_output"
    return outputs


@pytest.fixture(autouse=True)
def cleanup_test_agents(test_workspace: Path):
    """Clean up any test agents created during tests."""
    yield
    # Cleanup is handled by tmp_path automatic cleanup
