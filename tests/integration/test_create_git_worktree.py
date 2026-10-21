"""Integration tests for environment creation with disposable temporary Git repositories."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from isolation_check import cli
from isolation_check.create.manager import execute_create, execute_dry_run
from isolation_check.create.spec import CreateSpec, load_spec_from_cli
from isolation_check.create.worktree import branch_exists, run_git


def _docker_compose_available() -> bool:
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


COMPOSE_BASE_CONTENT = """
services:
  web:
    image: node:20-alpine
    environment:
      - DATABASE_URL=postgresql://postgres:postgres@db:5432/app_dev
    expose:
      - "3000"
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


class TestCreateGitWorktreeIntegration(unittest.TestCase):
    def setUp(self):
        if not _docker_compose_available():
            self.skipTest("Docker Compose CLI is not available on this machine.")

        self.td = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.td.name)

        # Initialize disposable git repository
        self.repo_dir = self.tmp_path / "main-repo"
        self.repo_dir.mkdir()
        run_git(self.repo_dir, ["init", "-b", "main", "."])
        run_git(self.repo_dir, ["config", "user.email", "tester@example.com"])
        run_git(self.repo_dir, ["config", "user.name", "Tester"])

        # Create base Compose file
        self.compose_file = self.repo_dir / "docker-compose.yaml"
        self.compose_file.write_text(COMPOSE_BASE_CONTENT, encoding="utf-8")

        # Create isolation-check.toml config file
        self.config_file = self.repo_dir / "isolation-check.toml"
        self.config_file.write_text(
            """
[create]
compose_files = ["docker-compose.yaml"]
app_service = "web"
db_service = "db"
db_env_var = "DATABASE_URL"
publish_db = false
""",
            encoding="utf-8",
        )

        run_git(self.repo_dir, ["add", "."])
        run_git(self.repo_dir, ["commit", "-m", "Initial commit"])

    def tearDown(self):
        self.td.cleanup()

    def test_dry_run_makes_no_changes(self):
        """Dry-run must validate configuration and print plan without making changes."""
        spec = load_spec_from_cli(
            name="feature-payments",
            repo_arg=str(self.repo_dir),
            dry_run=True,
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = execute_dry_run(spec)

        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn("feature-payments_pgdata", out.getvalue())
        self.assertIn("VALIDATION SUCCESSFUL", out.getvalue())

        # Verify nothing was created
        self.assertFalse(spec.worktree_path.exists())
        self.assertFalse(branch_exists(self.repo_dir, "feature-payments"))

    def test_create_generates_isolated_worktree_environment(self):
        """Create must build worktree, override compose, manifest, and keep repo clean."""
        spec = load_spec_from_cli(
            name="feature-payments",
            repo_arg=str(self.repo_dir),
            dry_run=False,
        )

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = execute_create(spec)

        self.assertEqual(code, 0)
        self.assertIn("ENVIRONMENT CREATED", out.getvalue())

        # 1. Worktree exists and branch exists
        self.assertTrue(spec.worktree_path.is_dir())
        self.assertTrue(branch_exists(self.repo_dir, "feature-payments"))

        # 2. Main repo branch has NOT changed
        proc = run_git(self.repo_dir, ["branch", "--show-current"])
        self.assertEqual(proc.stdout.strip(), "main")

        # 3. Generated files exist
        override_file = spec.worktree_path / ".isolation-check" / "compose.override.yaml"
        env_file = spec.worktree_path / ".isolation-check" / "isolation.env"
        manifest_file = spec.worktree_path / ".isolation-check" / "manifest.json"

        self.assertTrue(override_file.is_file())
        self.assertTrue(env_file.is_file())
        self.assertTrue(manifest_file.is_file())

        # 4. Manifest contains expected version and redacted DB URL
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data["schema_version"], "1.0")
        self.assertEqual(manifest_data["database"]["volume_name"], "feature-payments_pgdata")
        self.assertIn("[REDACTED]", manifest_data["application"]["database_url_redacted"])
        self.assertNotIn("secretpassword", manifest_file.read_text(encoding="utf-8"))

        # 5. Untracked isolation: worktree status is clean
        status_proc = run_git(spec.worktree_path, ["status", "--porcelain"])
        self.assertEqual(status_proc.stdout.strip(), "")

    def test_refuses_existing_destination_path(self):
        spec = load_spec_from_cli(
            name="feature-payments",
            repo_arg=str(self.repo_dir),
        )
        spec.worktree_path.mkdir(parents=True)

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = execute_create(spec)
        self.assertEqual(code, 2)
        self.assertIn("Destination worktree path already exists", err.getvalue())

    def test_refuses_existing_branch(self):
        run_git(self.repo_dir, ["branch", "feature-payments"])
        spec = load_spec_from_cli(
            name="feature-payments",
            repo_arg=str(self.repo_dir),
        )

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = execute_create(spec)
        self.assertEqual(code, 2)
        self.assertIn("already exists in repository", err.getvalue())

    def test_refuses_existing_managed_environment(self):
        spec = load_spec_from_cli(
            name="feature-payments",
            repo_arg=str(self.repo_dir),
        )
        code1 = execute_create(spec)
        self.assertEqual(code1, 0)

        # Attempt to create again with another branch but same env name
        spec2 = load_spec_from_cli(
            name="feature-payments",
            repo_arg=str(self.repo_dir),
            branch_arg="feature-payments-2",
            worktree_path_arg=str(self.tmp_path / "wt2"),
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code2 = execute_create(spec2)
        self.assertEqual(code2, 2)
        self.assertIn("A managed environment record for 'feature-payments' already exists", err.getvalue())

    def test_two_generated_environments_are_completely_isolated(self):
        """Two generated environments must receive distinct volumes and project identities, passing isolation-check."""
        spec_a = load_spec_from_cli(name="feature-a", repo_arg=str(self.repo_dir))
        spec_b = load_spec_from_cli(name="feature-b", repo_arg=str(self.repo_dir))

        code_a = execute_create(spec_a)
        code_b = execute_create(spec_b)
        self.assertEqual(code_a, 0)
        self.assertEqual(code_b, 0)

        # Inspect manifests
        manifest_a = json.loads(
            (spec_a.worktree_path / ".isolation-check" / "manifest.json").read_text(encoding="utf-8")
        )
        manifest_b = json.loads(
            (spec_b.worktree_path / ".isolation-check" / "manifest.json").read_text(encoding="utf-8")
        )

        self.assertEqual(manifest_a["compose"]["project_name"], "feature-a")
        self.assertEqual(manifest_b["compose"]["project_name"], "feature-b")
        self.assertEqual(manifest_a["database"]["volume_name"], "feature-a_pgdata")
        self.assertEqual(manifest_b["database"]["volume_name"], "feature-b_pgdata")
        self.assertNotEqual(
            manifest_a["database"]["volume_name"],
            manifest_b["database"]["volume_name"],
        )

        # Run our checker directly on the two generated worktrees!
        check_out = io.StringIO()
        with contextlib.redirect_stdout(check_out):
            check_code = cli.main([
                "check",
                str(spec_a.worktree_path),
                str(spec_b.worktree_path),
            ])
        self.assertEqual(check_code, 0)
        self.assertIn("PASS    VOL001", check_out.getvalue())
        self.assertIn("PASS    PORT001", check_out.getvalue())

    def test_partial_failures_preserve_existing_user_work(self):
        """If worktree has acquired user changes, rollback must not destroy user work."""
        spec = load_spec_from_cli(name="feature-preserve", repo_arg=str(self.repo_dir))

        # We simulate creating the worktree and the user adding work to it
        run_git(self.repo_dir, ["worktree", "add", "-b", "feature-preserve", str(spec.worktree_path), "HEAD"])
        user_file = spec.worktree_path / "user_notes.txt"
        user_file.write_text("Crucial user work that must not be deleted", encoding="utf-8")

        # Now attempting create will fail due to existing worktree path
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = execute_create(spec)

        self.assertEqual(code, 2)
        # Verify the user file was NOT touched or deleted
        self.assertTrue(user_file.is_file())
        self.assertEqual(user_file.read_text(encoding="utf-8"), "Crucial user work that must not be deleted")

    def test_cli_dry_run_and_create_commands(self):
        """CLI invocation of isolation-check create <name> --dry-run and create <name>."""
        # 1. Dry run via CLI
        out_dry = io.StringIO()
        with contextlib.redirect_stdout(out_dry):
            code_dry = cli.main(["create", "feature-cli", "--repo", str(self.repo_dir), "--dry-run"])
        self.assertEqual(code_dry, 0)
        self.assertIn("DRY RUN", out_dry.getvalue())

        # 2. Create via CLI
        out_create = io.StringIO()
        with contextlib.redirect_stdout(out_create):
            code_create = cli.main(["create", "feature-cli", "--repo", str(self.repo_dir)])
        self.assertEqual(code_create, 0)
        self.assertIn("ENVIRONMENT CREATED", out_create.getvalue())
        self.assertTrue((self.tmp_path / "feature-cli").is_dir())


if __name__ == "__main__":
    unittest.main()
