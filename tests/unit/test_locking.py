"""Unit tests for the single-instance lock module."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


class TestLockFile:
    """Tests for the LockFile class."""

    def test_acquire_and_release(self, tmp_path):
        """Lock can be acquired and released cleanly."""
        from swebench_orchestrator.locking import LockFile

        lock_file = tmp_path / "test.lock"
        lock = LockFile(lock_file)
        lock.acquire()
        # Should not raise — lock is held
        lock.release()
        # Should not raise — release is idempotent

    def test_acquire_twice_raises(self, tmp_path):
        """Acquiring an already-held lock raises RuntimeError."""
        from swebench_orchestrator.locking import LockFile

        lock_file = tmp_path / "test.lock"
        lock1 = LockFile(lock_file)
        lock1.acquire()
        lock2 = LockFile(lock_file)
        with pytest.raises(RuntimeError, match="Another instance is already running"):
            lock2.acquire()
        lock1.release()

    def test_lock_released_on_context_manager_exit(self, tmp_path):
        """Lock is released when exiting the context manager."""
        from swebench_orchestrator.locking import LockFile

        lock_file = tmp_path / "test.lock"
        with LockFile(lock_file):
            # Lock is held inside the block
            lock2 = LockFile(lock_file)
            with pytest.raises(RuntimeError, match="Another instance is already running"):
                lock2.acquire()
        # Lock should be released now
        lock3 = LockFile(lock_file)
        lock3.acquire()  # Should not raise
        lock3.release()

    def test_lock_file_created_on_acquire(self, tmp_path):
        """Lock file is created when acquired."""
        from swebench_orchestrator.locking import LockFile

        lock_file = tmp_path / "subdir" / "test.lock"
        assert not lock_file.exists()
        lock = LockFile(lock_file)
        lock.acquire()
        assert lock_file.exists()
        lock.release()

    def test_lock_file_parent_created(self, tmp_path):
        """Parent directories of lock file are created if needed."""
        from swebench_orchestrator.locking import LockFile

        lock_file = tmp_path / "deep" / "nested" / "dir" / "test.lock"
        assert not lock_file.parent.exists()
        lock = LockFile(lock_file)
        lock.acquire()
        assert lock_file.parent.exists()
        lock.release()


class TestAcquireLock:
    """Tests for the acquire_lock convenience function."""

    def test_acquire_lock_returns_file_handle(self, tmp_path):
        """acquire_lock returns a file handle that can be used."""
        from swebench_orchestrator.locking import acquire_lock

        lock_file = tmp_path / "test.lock"
        fh = acquire_lock(lock_file)
        assert fh is not None
        fh.close()

    def test_acquire_lock_raises_when_locked(self, tmp_path):
        """acquire_lock raises RuntimeError when lock is held."""
        from swebench_orchestrator.locking import LockFile, acquire_lock

        lock_file = tmp_path / "test.lock"
        # Hold a lock via LockFile
        outer = LockFile(lock_file)
        outer.acquire()
        with pytest.raises(RuntimeError, match="Another instance is already running"):
            acquire_lock(lock_file)
        outer.release()


class TestIntegrationConcurrent:
    """Integration tests for concurrent process locking."""

    def test_second_process_blocked(self, tmp_path):
        """Second process trying to acquire the same lock fails."""
        from swebench_orchestrator.locking import LockFile

        lock_file = tmp_path / "concurrent.lock"

        # First process acquires lock
        lock1 = LockFile(lock_file)
        lock1.acquire()

        # Second process should fail
        lock2 = LockFile(lock_file)
        with pytest.raises(RuntimeError, match="Another instance is already running"):
            lock2.acquire()

        lock1.release()

    def test_lock_released_on_process_exit(self, tmp_path):
        """Lock is released when the holding process exits (even abnormally)."""
        from swebench_orchestrator.locking import LockFile

        lock_file = tmp_path / "process.lock"

        # Start a subprocess that holds the lock briefly
        script = f"""
import sys
sys.path.insert(0, "{Path('/home/josh/Projects/swe-bench/src')}")
from swebench_orchestrator.locking import LockFile
lock = LockFile(r"{lock_file}")
lock.acquire()
import time
time.sleep(2)
# Process exits without explicit release
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            timeout=10,
        )

        # After process exits, lock should be released
        lock2 = LockFile(lock_file)
        lock2.acquire()  # Should not raise
        lock2.release()

    def test_lock_error_message_includes_path(self, tmp_path):
        """Error message includes the lock file path."""
        from swebench_orchestrator.locking import LockFile

        lock_file = tmp_path / "named.lock"
        lock1 = LockFile(lock_file)
        lock1.acquire()

        lock2 = LockFile(lock_file)
        with pytest.raises(RuntimeError) as exc_info:
            lock2.acquire()

        assert str(lock_file) in str(exc_info.value)
        lock1.release()


class TestLockWithSignalHandling:
    """Tests for lock behavior with signal handling."""

    def test_lock_released_on_interrupt(self, tmp_path):
        """Lock is released when process receives SIGINT."""
        from swebench_orchestrator.locking import LockFile

        lock_file = tmp_path / "interrupt.lock"

        # Start a subprocess that holds the lock and waits for signal
        script = f"""
import sys, signal, time
sys.path.insert(0, "{Path('/home/josh/Projects/swe-bench/src')}")
from swebench_orchestrator.locking import LockFile
lock = LockFile(r"{lock_file}")
lock.acquire()

def handler(sig, frame):
    # Process exits — lock should be released by OS
    sys.exit(0)

signal.signal(signal.SIGUSR1, handler)
import os
os.kill(os.getpid(), signal.SIGUSR1)
"""
        subprocess.run([sys.executable, "-c", script], timeout=10)

        # Lock should be released
        lock2 = LockFile(lock_file)
        lock2.acquire()  # Should not raise
        lock2.release()
