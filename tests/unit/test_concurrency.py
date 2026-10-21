"""Unit tests for concurrency lock, port allocation synchronization, and atomic write operations."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest

from isolation_check.runtime.concurrency import (
    ConcurrencyLockError,
    acquire_environment_lock,
    atomic_write_text,
    get_process_start_time,
    is_lock_abandoned,
)
from isolation_check.runtime.port_allocator import (
    allocate_candidate_loopback_port,
    release_port_reservation,
    reserve_loopback_port,
)


class TestConcurrency(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def test_atomic_write_text_success(self):
        target = self.tmp_path / "test.json"
        data = {"hello": "world"}
        atomic_write_text(target, json.dumps(data), mode=0o600)

        self.assertTrue(target.is_file())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), data)

        # Overwrite atomically
        new_data = {"hello": "universe"}
        atomic_write_text(target, json.dumps(new_data), mode=0o600)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), new_data)

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions only")
    def test_atomic_write_text_mode_0600_posix(self):
        target = self.tmp_path / "isolation.env"
        atomic_write_text(target, "SECRET=12345\n", mode=0o600)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

        # Ensure replacement does not broaden permissions
        atomic_write_text(target, "SECRET=67890\n", mode=0o600)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_environment_lock_acquisition_and_release(self):
        lock_file = self.tmp_path / "env.lock"
        self.assertFalse(lock_file.exists())

        with acquire_environment_lock(lock_file, "env-test", "up"):
            self.assertTrue(lock_file.exists())
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            self.assertEqual(data["environment"], "env-test")
            self.assertEqual(data["command"], "up")
            self.assertEqual(data["pid"], os.getpid())
            self.assertTrue(len(data.get("owner_token", "")) > 0)
            self.assertEqual(data["host"], socket.gethostname())

        # Automatically removed upon context exit
        self.assertFalse(lock_file.exists())

    def test_live_owner_older_than_stale_threshold_not_reclaimed(self):
        """A lock must NEVER be reclaimed solely because it is old if owner process is still alive."""
        lock_file = self.tmp_path / "env.lock"
        old_time = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        current_pid = os.getpid()
        proc_start = get_process_start_time(current_pid)

        initial_payload = {
            "pid": current_pid,
            "process_start_time": proc_start,
            "host": socket.gethostname(),
            "owner_token": "live-process-token-12345",
            "command": "up",
            "environment": "env-test",
            "created_at": old_time,
        }
        lock_file.write_text(json.dumps(initial_payload, indent=2), encoding="utf-8")

        # Because current process is alive and identity matches, it must NOT be reclaimed even if 3 hours old
        with self.assertRaises(ConcurrencyLockError) as ctx:
            with acquire_environment_lock(lock_file, "env-test", "up", timeout=0.05):
                pass

        self.assertIn(f"PID {current_pid}", str(ctx.exception))
        self.assertIn("Recovery procedure", str(ctx.exception))
        # Verify original active lock was preserved and not stolen
        current_content = json.loads(lock_file.read_text(encoding="utf-8"))
        self.assertEqual(current_content["owner_token"], "live-process-token-12345")

    def test_dead_owner_is_reclaimed(self):
        """A lock whose owner PID is no longer running on the host must be safely reclaimed."""
        lock_file = self.tmp_path / "env.lock"
        dead_payload = {
            "pid": 99999999,
            "process_start_time": "12345",
            "host": socket.gethostname(),
            "owner_token": "dead-token",
            "command": "up",
            "environment": "env-test",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        lock_file.write_text(json.dumps(dead_payload), encoding="utf-8")

        with acquire_environment_lock(lock_file, "env-test", "up"):
            self.assertTrue(lock_file.exists())
            current_data = json.loads(lock_file.read_text(encoding="utf-8"))
            self.assertEqual(current_data["pid"], os.getpid())

    def test_pid_reuse_mismatched_process_identity_is_reclaimed(self):
        """If a process ID is alive but has a mismatched process start identity, PID reuse is recognized and reclaimed."""
        current_pid = os.getpid()
        real_start_time = get_process_start_time(current_pid)
        if real_start_time is None:
            self.skipTest("Process start time tracking not supported on this platform.")

        lock_file = self.tmp_path / "env.lock"
        mismatched_payload = {
            "pid": current_pid,
            "process_start_time": "mismatched-start-identity-99999999",
            "host": socket.gethostname(),
            "owner_token": "reused-pid-token",
            "command": "up",
            "environment": "env-test",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        lock_file.write_text(json.dumps(mismatched_payload), encoding="utf-8")

        # Must detect that the recorded owner identity does not match the current process with that PID
        with acquire_environment_lock(lock_file, "env-test", "up"):
            self.assertTrue(lock_file.exists())
            current_data = json.loads(lock_file.read_text(encoding="utf-8"))
            self.assertEqual(current_data["process_start_time"], real_start_time)

    def test_lock_owner_token_prevents_deleting_another_process_lock(self):
        """A process must never delete another process's lock upon exiting its context manager."""
        lock_file = self.tmp_path / "env.lock"

        with acquire_environment_lock(lock_file, "env-test", "up"):
            self.assertTrue(lock_file.exists())
            # Simulate another process rewriting or replacing the lock with a different token
            foreign_payload = {
                "pid": os.getpid(),
                "process_start_time": get_process_start_time(os.getpid()),
                "host": socket.gethostname(),
                "owner_token": "foreign-process-token-9876",
                "command": "up",
                "environment": "env-test",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            lock_file.write_text(json.dumps(foreign_payload), encoding="utf-8")

        # Exiting context manager must NOT delete the foreign lock
        self.assertTrue(lock_file.exists())
        current_data = json.loads(lock_file.read_text(encoding="utf-8"))
        self.assertEqual(current_data["owner_token"], "foreign-process-token-9876")

    def test_acquisition_timeout_does_not_mutate_active_lock(self):
        """Timing out waiting for a lock must leave the existing active lock completely unmutated."""
        lock_file = self.tmp_path / "env.lock"
        active_payload = {
            "pid": os.getpid(),
            "process_start_time": get_process_start_time(os.getpid()),
            "host": socket.gethostname(),
            "owner_token": "active-token-stay-untouched",
            "command": "up",
            "environment": "env-test",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        raw_before = json.dumps(active_payload, indent=2)
        lock_file.write_text(raw_before, encoding="utf-8")

        with self.assertRaises(ConcurrencyLockError):
            with acquire_environment_lock(lock_file, "env-test", "up", timeout=0.1, poll_interval=0.02):
                pass

        # The active lock must be completely unmutated
        raw_after = lock_file.read_text(encoding="utf-8")
        self.assertEqual(raw_before, raw_after)

    def test_concurrent_port_allocations_different_environments(self):
        """Intentionally run allocations for different environments concurrently using barriers.

        Confirms they cannot receive the same port.
        """
        barrier = threading.Barrier(2)
        allocated_ports: list[int] = []
        errors: list[Exception] = []

        def worker(env_name: str):
            try:
                barrier.wait(timeout=5.0)
                port = allocate_candidate_loopback_port(self.tmp_path, env_name=env_name)
                allocated_ports.append(port)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=("feature-a",))
        t2 = threading.Thread(target=worker, args=("feature-b",))
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Errors during concurrent allocation: {errors}")
        self.assertEqual(len(allocated_ports), 2)
        self.assertNotEqual(allocated_ports[0], allocated_ports[1], "Both environments received the same port!")
        self.assertTrue(10001 <= allocated_ports[0] <= 65535)
        self.assertTrue(10001 <= allocated_ports[1] <= 65535)


if __name__ == "__main__":
    unittest.main()
