"""Unit tests for create generator and manifest redaction."""
import json
from pathlib import Path
import tempfile
import unittest

from isolation_check.create.generator import (
    build_override_compose_content,
    plan_generated_environment,
    write_generated_environment_files,
)
from isolation_check.create.spec import CreateSpec


class TestCreateGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.wt = Path(self.tmp.name) / "wt"
        self.wt.mkdir()
        self.spec = CreateSpec(
            name="feature-payments",
            repo=self.repo,
            base_ref="main",
            branch="feature-payments",
            worktree_path=self.wt,
            compose_files=["compose.yaml"],
            app_service="web",
            db_service="db",
            db_env_var="DATABASE_URL",
            publish_db=False,
        )
        self.raw_compose = {
            "services": {
                "web": {"image": "node:20"},
                "db": {
                    "image": "postgres:16",
                    "ports": ["5432:5432"],
                    "environment": {
                        "POSTGRES_USER": "myuser",
                        "POSTGRES_DB": "mydb",
                        "POSTGRES_PASSWORD": "supersecretpassword",
                    },
                },
            }
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_generated_environment(self):
        plan = plan_generated_environment(self.spec, self.raw_compose)
        self.assertEqual(plan.project_name, "feature-payments")
        self.assertEqual(plan.dedicated_volume, "feature-payments_pgdata")
        self.assertEqual(plan.db_user, "myuser")
        self.assertEqual(plan.db_name, "mydb")
        self.assertEqual(plan.db_password, "supersecretpassword")
        self.assertEqual(
            plan.database_url,
            "postgresql://myuser:supersecretpassword@db:5432/mydb",
        )
        self.assertEqual(
            plan.redacted_database_url,
            "postgresql://myuser:[REDACTED]@db:5432/mydb",
        )

    def test_build_override_compose_content(self):
        plan = plan_generated_environment(self.spec, self.raw_compose)
        content = build_override_compose_content(self.spec, plan, self.raw_compose)

        self.assertIn("name: feature-payments", content)
        self.assertIn("feature-payments_pgdata:/var/lib/postgresql/data", content)
        self.assertIn("DATABASE_URL: postgresql://myuser:supersecretpassword@db:5432/mydb", content)
        self.assertIn("ports: !reset []", content)
        self.assertIn("feature-payments_pgdata:\n    name: feature-payments_pgdata", content)

    def test_manifest_redaction_and_untracked_files(self):
        plan = plan_generated_environment(self.spec, self.raw_compose)
        created_paths = write_generated_environment_files(
            self.spec, plan, self.raw_compose, base_commit="abcdef123456"
        )

        manifest_file = self.wt / plan.manifest_rel_path
        self.assertTrue(manifest_file.is_file())

        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data["schema_version"], "1.0")
        self.assertEqual(manifest_data["database"]["volume_name"], "feature-payments_pgdata")
        self.assertEqual(
            manifest_data["application"]["database_url_redacted"],
            "postgresql://myuser:[REDACTED]@db:5432/mydb",
        )

        # CRITICAL TEST: Plaintext password must NOT appear anywhere in the manifest!
        manifest_text = manifest_file.read_text(encoding="utf-8")
        self.assertNotIn("supersecretpassword", manifest_text)


if __name__ == "__main__":
    unittest.main()
