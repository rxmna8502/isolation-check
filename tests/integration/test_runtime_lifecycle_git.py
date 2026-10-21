"""Integration tests for runtime validation, status, down, and Compose rendering in real Git repositories."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from isolation_check import cli
from isolation_check.create.manager import execute_create
from isolation_check.create.spec import load_spec_from_cli
from isolation_check.create.worktree import run_git
from isolation_check.runtime.lifecycle import execute_down, execute_status
from isolation_check.runtime.validation import locate_and_validate_environment

COMPOSE_BASE_CONTENT = """
services:
  web:
    image: node:20-alpine
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/app_dev
    ports:
      - "3000:3000"
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: secretpassword
      POSTGRES_DB: app_dev
    ports:
      - "5432:5432"
    volumes:
      - db_data:/var/lib/postgresql/data

volumes:
  db_data:
    name: shared_base_pgdata
"""


def _docker_compose_cli_available() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


class TestRuntimeGitIntegration(unittest.TestCase):
    """Git integration and Compose rendering tests without requiring active Docker daemon."""

    def setUp(self):
        if not _docker_compose_cli_available():
            self.skipTest("Docker Compose CLI is not installed on PATH.")

        self.td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.td.name)

        # 1. Initialize disposable Git repository
        self.repo_dir = self.tmp_path / "main-repo"
        self.repo_dir.mkdir()
        run_git(self.repo_dir, ["init", "-b", "main", "."])
        run_git(self.repo_dir, ["config", "user.email", "tester@example.com"])
        run_git(self.repo_dir, ["config", "user.name", "Tester"])

        # 2. Add compose file and config
        self.compose_file = self.repo_dir / "docker-compose.yaml"
        self.compose_file.write_text(COMPOSE_BASE_CONTENT, encoding="utf-8")

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
        run_git(self.repo_dir, ["commit", "-m", "Initial commit"])

    def tearDown(self):
        self.td.cleanup()

    def test_real_git_worktree_creation_and_ownership_validation(self):
        """Creates two real worktrees and validates git registration, manifests, and rendering."""
        # Create env 1
        spec_a = load_spec_from_cli(
            name="env-alpha",
            repo_arg=str(self.repo_dir),
            config_path_arg=str(self.config_file),
        )
        code_a = execute_create(spec_a)
        self.assertEqual(code_a, 0)

        # Create env 2
        spec_b = load_spec_from_cli(
            name="env-beta",
            repo_arg=str(self.repo_dir),
            config_path_arg=str(self.config_file),
        )
        code_b = execute_create(spec_b)
        self.assertEqual(code_b, 0)

        # Validate ownership of env-alpha
        rec_f, rec_d, man_f, man_d = locate_and_validate_environment(self.repo_dir, "env-alpha")
        self.assertEqual(rec_d["name"], "env-alpha")
        self.assertEqual(man_d["application"]["app_port"], 3000)
        self.assertEqual(man_d["application"]["binding_policy"], "loopback-only")

        # Validate ownership of env-beta
        rec_f2, rec_d2, man_f2, man_d2 = locate_and_validate_environment(self.repo_dir, "env-beta")
        self.assertEqual(rec_d2["name"], "env-beta")
        self.assertEqual(man_d2["application"]["app_port"], 3000)

        # Verify Compose config rendering for env-alpha with docker compose config
        wt_a = Path(rec_d["worktree_path"])
        cmd_render = [
            "docker", "compose",
            "-p", rec_d["compose_project_name"],
            "--project-directory", str(wt_a),
            "-f", str(wt_a / "docker-compose.yaml"),
            "-f", str(wt_a / ".isolation-check" / "compose.override.yaml"),
            "config",
        ]
        res = subprocess.run(cmd_render, cwd=str(wt_a), capture_output=True, text=True, check=False)
        self.assertEqual(res.returncode, 0, f"docker compose config failed: {res.stderr}")
        self.assertIn("env-alpha_pgdata", res.stdout)
        self.assertIn("127.0.0.1", res.stdout)

        # Test execute_status (read-only)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            st_code = execute_status("env-alpha", repo_hint=self.repo_dir, output_json=True)
        self.assertEqual(st_code, 0)
        st_data = json.loads(out.getvalue())
        self.assertEqual(st_data["status"], "STOPPED")
        self.assertTrue(st_data["ownership_verified"])

        # Test execute_down when already stopped (idempotent, safe repeatability)
        out_down = io.StringIO()
        with contextlib.redirect_stdout(out_down):
            dn_code = execute_down("env-alpha", repo_hint=self.repo_dir, output_json=True)
        self.assertEqual(dn_code, 0)
        dn_data = json.loads(out_down.getvalue())
        self.assertEqual(dn_data["status"], "STOPPED")
        self.assertEqual(dn_data["volume_preserved"], "env-alpha_pgdata")


if __name__ == "__main__":
    unittest.main()
