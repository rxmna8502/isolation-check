"""Real Docker lifecycle smoke test for two environments running simultaneously.

Runs only when an active Docker daemon and Docker Compose CLI are available.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from isolation_check.create.manager import execute_create
from isolation_check.create.spec import load_spec_from_cli
from isolation_check.create.worktree import run_git
from isolation_check.runtime.lifecycle import execute_down, execute_status, execute_up


def _is_docker_daemon_running() -> bool:
    """Checks whether the local Docker daemon is running and reachable."""
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


# Minimal local compose fixture that does not download heavy images
COMPOSE_MINIMAL_SMOKE = """
services:
  web:
    image: alpine:latest
    command: ["sh", "-c", "nc -l -p 3000"]
    expose:
      - "3000"
  db:
    image: alpine:latest
    command: ["sh", "-c", "sleep 3600"]
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
    name: smoke_test_shared_data
"""


class TestRealDockerLifecycleSmoke(unittest.TestCase):
    """Disposable real-Docker smoke test executing two managed environments simultaneously."""

    def setUp(self):
        if not _is_docker_daemon_running():
            self.skipTest("Docker daemon is not running; skipping live container execution test.")

        self.td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.td.name)

        # 1. Initialize disposable Git repository
        self.repo_dir = self.tmp_path / "smoke-repo"
        self.repo_dir.mkdir()
        run_git(self.repo_dir, ["init", "-b", "main", "."])
        run_git(self.repo_dir, ["config", "user.email", "smoke@example.com"])
        run_git(self.repo_dir, ["config", "user.name", "SmokeTester"])

        # 2. Add compose file and config
        self.compose_file = self.repo_dir / "docker-compose.yaml"
        self.compose_file.write_text(COMPOSE_MINIMAL_SMOKE, encoding="utf-8")

        self.config_file = self.repo_dir / "isolation-check.toml"
        self.config_file.write_text(
            """
[create]
compose_files = ["docker-compose.yaml"]
app_service = "web"
db_service = "db"
db_env_var = "DATABASE_URL"
app_port = 3000
publish_db = false
""",
            encoding="utf-8",
        )

        run_git(self.repo_dir, ["add", "."])
        run_git(self.repo_dir, ["commit", "-m", "Initial smoke commit"])

    def tearDown(self):
        # Best effort cleanup if test fails mid-run
        if _is_docker_daemon_running():
            try:
                execute_down("env-smoke-a", repo_hint=self.repo_dir)
            except Exception:
                pass
            try:
                execute_down("env-smoke-b", repo_hint=self.repo_dir)
            except Exception:
                pass
        self.td.cleanup()

    def test_two_environments_running_simultaneously(self):
        """Executes the full 8-step smoke test verifying simultaneous operation and volume preservation:

        1. Creates two managed worktrees.
        2. Starts both environments simultaneously.
        3. Confirms that their app URLs use different host ports.
        4. Confirms both application bindings use 127.0.0.1.
        5. Confirms PostgreSQL is not host-published.
        6. Confirms each project uses its own PostgreSQL volume.
        7. Stops both environments.
        8. Confirms their named database volumes remain.
        """
        # Step 1: Create two managed worktrees
        spec_a = load_spec_from_cli(
            name="env-smoke-a",
            repo_arg=str(self.repo_dir),
            config_path_arg=str(self.config_file),
        )
        self.assertEqual(execute_create(spec_a), 0)

        spec_b = load_spec_from_cli(
            name="env-smoke-b",
            repo_arg=str(self.repo_dir),
            config_path_arg=str(self.config_file),
        )
        self.assertEqual(execute_create(spec_b), 0)

        # Step 2: Start both environments simultaneously
        code_up_a = execute_up("env-smoke-a", repo_hint=self.repo_dir)
        self.assertEqual(code_up_a, 0)

        code_up_b = execute_up("env-smoke-b", repo_hint=self.repo_dir)
        self.assertEqual(code_up_b, 0)

        # Query status of env-smoke-a
        out_a = io.StringIO()
        with contextlib.redirect_stdout(out_a):
            st_a_code = execute_status("env-smoke-a", repo_hint=self.repo_dir, output_json=True)
        self.assertEqual(st_a_code, 0)
        data_a = json.loads(out_a.getvalue())

        # Query status of env-smoke-b
        out_b = io.StringIO()
        with contextlib.redirect_stdout(out_b):
            st_b_code = execute_status("env-smoke-b", repo_hint=self.repo_dir, output_json=True)
        self.assertEqual(st_b_code, 0)
        data_b = json.loads(out_b.getvalue())

        # Step 3: Confirms that their app URLs use different host ports
        port_a = data_a.get("host_port")
        port_b = data_b.get("host_port")
        self.assertIsNotNone(port_a)
        self.assertIsNotNone(port_b)
        self.assertNotEqual(port_a, port_b, f"Expected distinct ports, got {port_a} and {port_b}")

        # Step 4: Confirms both application bindings use 127.0.0.1
        self.assertTrue(str(data_a.get("url", "")).startswith("http://127.0.0.1:"))
        self.assertTrue(str(data_b.get("url", "")).startswith("http://127.0.0.1:"))

        # Step 5: Confirms PostgreSQL is not host-published
        for c in data_a.get("containers", []):
            if c.get("service") == "db":
                ports = str(c.get("Ports") or "")
                self.assertNotIn("5432->", ports)
        for c in data_b.get("containers", []):
            if c.get("service") == "db":
                ports = str(c.get("Ports") or "")
                self.assertNotIn("5432->", ports)

        # Step 6: Confirms each project uses its own PostgreSQL volume
        vol_inspect_a = subprocess.run(
            ["docker", "volume", "inspect", "env-smoke-a_pgdata"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(vol_inspect_a.returncode, 0)

        vol_inspect_b = subprocess.run(
            ["docker", "volume", "inspect", "env-smoke-b_pgdata"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(vol_inspect_b.returncode, 0)

        # Step 7: Stops both environments
        self.assertEqual(execute_down("env-smoke-a", repo_hint=self.repo_dir), 0)
        self.assertEqual(execute_down("env-smoke-b", repo_hint=self.repo_dir), 0)

        # Step 8: Confirms their named database volumes remain
        vol_after_a = subprocess.run(
            ["docker", "volume", "inspect", "env-smoke-a_pgdata"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(vol_after_a.returncode, 0, "Dedicated volume for env-a was deleted!")

        vol_after_b = subprocess.run(
            ["docker", "volume", "inspect", "env-smoke-b_pgdata"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(vol_after_b.returncode, 0, "Dedicated volume for env-b was deleted!")

        # Clean up the smoke volumes after successful test
        subprocess.run(["docker", "volume", "rm", "env-smoke-a_pgdata", "env-smoke-b_pgdata"], capture_output=True, check=False)


if __name__ == "__main__":
    unittest.main()
