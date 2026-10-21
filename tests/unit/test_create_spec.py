"""Unit tests for create specification and input loading."""
from pathlib import Path
import tempfile
import unittest

from isolation_check.create.spec import CreateSpec, SpecError, load_spec_from_cli


class TestCreateSpec(unittest.TestCase):
    def test_missing_explicit_inputs_raises_spec_error(self):
        """Missing required configuration mappings must not be guessed."""
        with self.assertRaises(SpecError) as ctx:
            load_spec_from_cli(name="feature-payments")
        err = str(ctx.exception)
        self.assertIn("Compose files in order", err)
        self.assertIn("Application service", err)
        self.assertIn("PostgreSQL service", err)
        self.assertIn("Database connection environment variable", err)

    def test_invalid_environment_name(self):
        with self.assertRaises(SpecError):
            load_spec_from_cli(name="invalid/name", app_service="web", db_service="db", db_env_var="DB", compose_files_arg=["c.yml"])
        with self.assertRaises(SpecError):
            load_spec_from_cli(name="..", app_service="web", db_service="db", db_env_var="DB", compose_files_arg=["c.yml"])
        with self.assertRaises(SpecError):
            load_spec_from_cli(name="", app_service="web", db_service="db", db_env_var="DB", compose_files_arg=["c.yml"])

    def test_explicit_cli_arguments_build_spec(self):
        spec = load_spec_from_cli(
            name="feature-payments",
            repo_arg="/path/to/repo",
            base_ref_arg="main",
            branch_arg="feature-payments",
            worktree_path_arg="/path/to/worktree",
            compose_files_arg=["compose.yaml", "compose.dev.yaml"],
            app_service_arg="web",
            db_service_arg="db",
            db_env_var_arg="DATABASE_URL",
            publish_db_arg=True,
            dry_run=True,
            output_format="json",
        )
        self.assertEqual(spec.name, "feature-payments")
        self.assertEqual(spec.base_ref, "main")
        self.assertEqual(spec.branch, "feature-payments")
        self.assertEqual(spec.compose_files, ["compose.yaml", "compose.dev.yaml"])
        self.assertEqual(spec.app_service, "web")
        self.assertEqual(spec.db_service, "db")
        self.assertEqual(spec.db_env_var, "DATABASE_URL")
        self.assertTrue(spec.publish_db)
        self.assertTrue(spec.dry_run)
        self.assertEqual(spec.format, "json")
        self.assertEqual(spec.sanitize_compose_project_name(), "feature-payments")
        self.assertEqual(spec.dedicated_volume_name(), "feature-payments_pgdata")

    def test_loading_from_toml_config_file(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "isolation-check.toml"
            cfg_path.write_text(
                """
[create]
compose_files = ["docker-compose.yaml"]
app_service = "api"
db_service = "postgres"
db_env_var = "DATABASE_URL"
publish_db = false
""",
                encoding="utf-8",
            )
            spec = load_spec_from_cli(
                name="feature-login",
                repo_arg=td,
                config_path_arg=str(cfg_path),
            )
            self.assertEqual(spec.compose_files, ["docker-compose.yaml"])
            self.assertEqual(spec.app_service, "api")
            self.assertEqual(spec.db_service, "postgres")
            self.assertEqual(spec.db_env_var, "DATABASE_URL")
            self.assertFalse(spec.publish_db)

    def test_cli_flags_override_toml_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "isolation-check.toml"
            cfg_path.write_text(
                """
[create]
compose_files = ["base.yaml"]
app_service = "api"
db_service = "postgres"
db_env_var = "OLD_URL"
""",
                encoding="utf-8",
            )
            spec = load_spec_from_cli(
                name="feature-login",
                repo_arg=td,
                config_path_arg=str(cfg_path),
                db_env_var_arg="NEW_URL",
            )
            self.assertEqual(spec.db_env_var, "NEW_URL")
            self.assertEqual(spec.app_service, "api")


if __name__ == "__main__":
    unittest.main()
