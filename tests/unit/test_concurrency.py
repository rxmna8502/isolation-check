"""Unit tests for concurrency lock and atomic write operations."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from isolation_check.runtime.concurrency import (
    ConcurrencyLockError,
    acquire_environment_lock,
    atomic_write_text,
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

    def test_environment_lock_acquisition_and_release(self):
        lock_file = self.tmp_path / "env.lock"
        self.assertFalse(lock_file.exists())

        with acquire_environment_lock(lock_file, "env-test", "up"):
            self.assertTrue(lock_file.exists())
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            self.assertEqual(data["environment"], "env-test")
            self.assertEqual(data["command"], "up")
            self.assertEqual(data["pid"], os.getpid())

        # Automatically removed upon context exit
        self.assertFalse(lock_file.exists())

    def test_environment_lock_prevents_concurrent_access(self):
        lock_file = self.tmp_path / "env.lock"

        with acquire_environment_lock(lock_file, "env-test", "up"):
            # Second attempt while lock is active must fail
            with self.assertRaises(ConcurrencyLockError) as ctx:
                with acquire_environment_lock(lock_file, "env-test", "up"):
                    pass
            self.assertIn("is locked by PID", str(ctx.exception))
            self.assertIn("running 'up'", str(ctx.exception))

    def test_environment_lock_stale_lock_recovery_dead_pid(self):
        lock_file = self.tmp_path / "env.lock"

        # Write a fake lock file with a non-existent PID (e.g. 99999999)
        lock_data = {
            "pid": 99999999,
            "command": "up",
            "environment": "env-test",
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        lock_file.write_text(json.dumps(lock_data), encoding="utf-8")

        # Must detect stale PID and recover
        with acquire_environment_lock(lock_file, "env-test", "up"):
            self.assertTrue(lock_file.exists())
            current_data = json.loads(lock_file.read_text(encoding="utf-8"))
            self.assertEqual(current_data["pid"], os.getpid())

    def test_environment_lock_stale_lock_recovery_timeout(self):
        lock_file = self.tmp_path / "env.lock"

        # Write a fake lock file with an old timestamp (e.g. 1 hour ago)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        lock_data = {
            "pid": os.getpid(),
            "command": "up",
            "environment": "env-test",
            "acquired_at": old_time,
        }
        lock_file.write_text(json.dumps(lock_data), encoding="utf-8")

        # Must detect stale age and recover
        with acquire_environment_lock(lock_file, "env-test", "down", stale_timeout=10.0):
            self.assertTrue(lock_file.exists())
            current_data = json.loads(lock_file.read_text(encoding="utf-8"))
            self.assertEqual(current_data["command"], "down")


if __name__ == "__main__":
    unittest.main()
