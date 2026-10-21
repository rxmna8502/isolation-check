"""Unit tests for runtime lifecycle (up, status, down) commands."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from isolation_check.runtime.lifecycle import (
    execute_down,
    execute_status,
    execute_up,
)


class TestRuntimeLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / ".isolation-check" / "environments").mkdir(parents=True)

        self.wt = Path(self.tmp.name) / "wt"
        self.wt.mkdir()
        (self.wt / ".isolation-check").mkdir(parents=True)

        # Base compose file in worktree
        (self.wt / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        # Override file in worktree
        (self.wt / ".isolation-check" / "compose.override.yaml").write_text("services: {}\n", encoding="utf-8")
        # Env file in worktree containing secret password
        (self.wt / ".isolation-check" / "isolation.env").write_text(
            "DATABASE_URL=postgresql://postgres:secret_pass_123@db:5432/app_dev\n",
            encoding="utf-8",
        )

        self.manifest_file = self.wt / ".isolation-check" / "manifest.json"
        self.manifest_data = {
            "tool": "isolation-check",
            "tool_version": "0.2.0a1",
            "manifest_schema_version": 1,
            "environment": {"name": "feature-x"},
            "compose": {
                "project_name": "feature-x",
                "base_compose_files": ["compose.yaml"],
                "override_file": ".isolation-check/compose.override.yaml",
                "env_file": ".isolation-check/isolation.env",
            },
            "database": {
                "service_name": "db",
                "volume_name": "feature-x_pgdata",
                "host_ports_published": False,
            },
            "application": {
                "service_name": "web",
                "app_port": 3000,
                "database_env_var": "DATABASE_URL",
                "binding_policy": "loopback-only",
                "bind_address": "127.0.0.1",
            },
            "managed_files": [
                ".isolation-check/compose.override.yaml",
                ".isolation-check/isolation.env",
                ".isolation-check/manifest.json",
            ],
        }
        self.manifest_file.write_text(json.dumps(self.manifest_data), encoding="utf-8")

        self.rec_file = self.repo / ".isolation-check" / "environments" / "feature-x.json"
        self.rec_data = {
            "name": "feature-x",
            "worktree_path": str(self.wt),
            "compose_project_name": "feature-x",
            "manifest_file": str(self.manifest_file),
        }
        self.rec_file.write_text(json.dumps(self.rec_data), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    @patch("isolation_check.runtime.lifecycle.verify_compose_capabilities")
    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_up_success(self, mock_cmd, mock_wt_list, mock_repo_root, mock_caps):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        # Call 1: initial inspect ps -> returns empty list (not running)
        # Call 2: docker compose up -d -> returns 0
        # Call 3: post-up inspect ps -> returns running container with port
        mock_cmd.side_effect = [
            (0, "[]", ""),
            (0, "", ""),
            (
                0,
                json.dumps([
                    {
                        "Name": "feature-x-web-1",
                        "Service": "web",
                        "State": "running",
                        "Status": "Up 2 seconds",
                        "Ports": "127.0.0.1:10001->3000/tcp",
                        "Publishers": [{"TargetPort": 3000, "PublishedPort": 10001, "URL": "127.0.0.1"}],
                        "Labels": {"com.docker.compose.project": "feature-x"},
                    }
                ]),
                "",
            ),
        ]

        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = execute_up("feature-x", repo_hint=self.repo)
            self.assertEqual(code, 0)
            output = out.getvalue()
            self.assertIn("ENVIRONMENT STARTED", output)
            self.assertIn("127.0.0.1:10001", output)
            self.assertIn("Internal container network only, host unpublished", output)

        state_file = self.wt / ".isolation-check" / "runtime-state.json"
        self.assertTrue(state_file.is_file())
        state = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "RUNNING")
        self.assertEqual(state["host_port"], 10001)
        self.assertEqual(state["url"], "http://127.0.0.1:10001")

    @patch("isolation_check.runtime.lifecycle.verify_compose_capabilities")
    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_up_idempotency_when_already_running(self, mock_cmd, mock_wt_list, mock_repo_root, mock_caps):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        # Initial inspect ps returns already running container
        mock_cmd.return_value = (
            0,
            json.dumps([
                {
                    "Name": "feature-x-web-1",
                    "Service": "web",
                    "State": "running",
                    "Publishers": [{"TargetPort": 3000, "PublishedPort": 10001, "URL": "127.0.0.1"}],
                    "Labels": {"com.docker.compose.project": "feature-x"},
                }
            ]),
            "",
        )

        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = execute_up("feature-x", repo_hint=self.repo)
            self.assertEqual(code, 0)
            output = out.getvalue()
            self.assertIn("ALREADY ACTIVE", output)
            self.assertIn("http://127.0.0.1:10001", output)

        # Did not run `docker compose up -d`
        self.assertEqual(mock_cmd.call_count, 1)

    @patch("isolation_check.runtime.lifecycle.verify_compose_capabilities")
    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_up_retries_on_port_conflict(self, mock_cmd, mock_wt_list, mock_repo_root, mock_caps):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        mock_cmd.side_effect = [
            (0, "[]", ""),  # initial inspect ps (not running)
            (1, "", "Error: bind: address already in use (port 10001)"),  # attempt 1 fails on port collision
            (0, "", ""),  # attempt 2 succeeds!
            (
                0,
                json.dumps([
                    {
                        "Name": "feature-x-web-1",
                        "Service": "web",
                        "State": "running",
                        "Publishers": [{"TargetPort": 3000, "PublishedPort": 10002, "URL": "127.0.0.1"}],
                        "Labels": {"com.docker.compose.project": "feature-x"},
                    }
                ]),
                "",
            ),
        ]

        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = execute_up("feature-x", repo_hint=self.repo)
            self.assertEqual(code, 0)
            output = out.getvalue()
            self.assertIn("ENVIRONMENT STARTED", output)

    @patch("isolation_check.runtime.lifecycle.verify_compose_capabilities")
    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_up_failure_preserves_worktree_and_files(self, mock_cmd, mock_wt_list, mock_repo_root, mock_caps):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        mock_cmd.side_effect = [
            (0, "[]", ""),  # initial inspect ps
            (1, "", "ImagePullBackOff: network timeout pulling postgres:16"),  # up fails
        ]

        with patch("sys.stderr", new_callable=io.StringIO) as err:
            code = execute_up("feature-x", repo_hint=self.repo)
            self.assertEqual(code, 1)
            err_output = err.getvalue()
            self.assertIn("have been preserved", err_output)

        # Confirm worktree and generated files are intact
        self.assertTrue(self.wt.is_dir())
        self.assertTrue(self.manifest_file.is_file())
        self.assertTrue((self.wt / ".isolation-check" / "compose.override.yaml").is_file())

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_status_running_and_healthy(self, mock_cmd, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        mock_cmd.return_value = (
            0,
            json.dumps([
                {
                    "Name": "feature-x-web-1",
                    "Service": "web",
                    "State": "running",
                    "Status": "Up 10 seconds (healthy)",
                    "Publishers": [{"TargetPort": 3000, "PublishedPort": 10001, "URL": "127.0.0.1"}],
                    "Labels": {"com.docker.compose.project": "feature-x"},
                }
            ]),
            "",
        )

        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = execute_status("feature-x", repo_hint=self.repo, output_json=True)
            self.assertEqual(code, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["status"], "RUNNING")
            self.assertEqual(data["health"], "HEALTHY")
            self.assertEqual(data["url"], "http://127.0.0.1:10001")

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_status_running_distinguished_from_healthy_unknown(self, mock_cmd, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        mock_cmd.return_value = (
            0,
            json.dumps([
                {
                    "Name": "feature-x-web-1",
                    "Service": "web",
                    "State": "running",
                    "Status": "Up 5 seconds",
                    "Publishers": [{"TargetPort": 3000, "PublishedPort": 10001, "URL": "127.0.0.1"}],
                    "Labels": {"com.docker.compose.project": "feature-x"},
                }
            ]),
            "",
        )

        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = execute_status("feature-x", repo_hint=self.repo, output_json=True)
            self.assertEqual(code, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["status"], "RUNNING")
            # Without healthcheck configured, must report UNKNOWN rather than healthy
            self.assertEqual(data["health"], "UNKNOWN")

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_status_stopped_and_stale_detection(self, mock_cmd, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        # Write runtime-state claiming RUNNING
        state_file = self.wt / ".isolation-check" / "runtime-state.json"
        state_file.write_text(
            json.dumps({
                "status": "RUNNING",
                "host_port": 10001,
                "url": "http://127.0.0.1:10001",
            }),
            encoding="utf-8",
        )

        # But Docker reports empty (stopped)
        mock_cmd.return_value = (0, "[]", "")

        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = execute_status("feature-x", repo_hint=self.repo, output_json=True)
            self.assertEqual(code, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["status"], "STOPPED")
            self.assertTrue(data["stale_runtime_state"])
            # Stale URL must NOT be presented
            self.assertIsNone(data["url"])
            self.assertIsNone(data["host_port"])

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_down_non_destructive_and_preserves_volumes(self, mock_cmd, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        # Call 1: inspect ps shows active containers
        # Call 2: docker compose down
        mock_cmd.side_effect = [
            (
                0,
                json.dumps([
                    {
                        "Name": "feature-x-web-1",
                        "Service": "web",
                        "State": "running",
                        "Labels": {"com.docker.compose.project": "feature-x"},
                    }
                ]),
                "",
            ),
            (0, "", ""),
        ]

        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = execute_down("feature-x", repo_hint=self.repo)
            self.assertEqual(code, 0)
            output = out.getvalue()
            self.assertIn("ENVIRONMENT STOPPED", output)
            self.assertIn("Dedicated volume 'feature-x_pgdata' was PRESERVED", output)

        # Verify command sent to compose
        down_cmd = mock_cmd.call_args_list[1][0][0]
        self.assertIn("down", down_cmd)
        self.assertNotIn("-v", down_cmd)
        self.assertNotIn("--volumes", down_cmd)

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_down_idempotency_when_already_stopped(self, mock_cmd, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        # Containers already stopped
        mock_cmd.return_value = (0, "[]", "")

        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = execute_down("feature-x", repo_hint=self.repo)
            self.assertEqual(code, 0)
            output = out.getvalue()
            self.assertIn("already stopped", output)

        # Did not run `docker compose down` again
        self.assertEqual(mock_cmd.call_count, 1)

    def test_down_refuses_destructive_flag(self):
        with patch("sys.stderr", new_callable=io.StringIO) as err:
            code = execute_down("feature-x", repo_hint=self.repo, destructive=True)
            self.assertEqual(code, 2)
            self.assertIn("Destructive flags are not supported", err.getvalue())


if __name__ == "__main__":
    unittest.main()
