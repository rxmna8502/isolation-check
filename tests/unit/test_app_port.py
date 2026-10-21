"""Unit tests for application port contract, parsing, inference, and override generation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from isolation_check.create.generator import (
    build_override_compose_content,
    plan_generated_environment,
    write_generated_environment_files,
)
from isolation_check.create.spec import CreateSpec, SpecError, load_spec_from_cli
from isolation_check.create.validate import (
    ValidationError,
    extract_candidate_container_ports,
    infer_or_validate_app_port,
    validate_spec_and_config,
)


class TestAppPortSpecAndInference(unittest.TestCase):
    def test_app_port_from_cli(self):
        spec = load_spec_from_cli(
            name="payments",
            repo_arg="/path/to/repo",
            compose_files_arg=["compose.yaml"],
            app_service_arg="web",
            db_service_arg="db",
            db_env_var_arg="DATABASE_URL",
            app_port_arg=3000,
        )
        self.assertEqual(spec.app_port, 3000)

    def test_app_port_from_toml(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "isolation-check.toml"
            cfg.write_text(
                """
[create]
compose_files = ["compose.yaml"]
app_service = "web"
db_service = "db"
db_env_var = "DATABASE_URL"
app_port = 4000
""",
                encoding="utf-8",
            )
            spec = load_spec_from_cli(name="payments", repo_arg=td, config_path_arg=str(cfg))
            self.assertEqual(spec.app_port, 4000)

    def test_app_port_cli_overrides_toml(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "isolation-check.toml"
            cfg.write_text(
                """
[create]
compose_files = ["compose.yaml"]
app_service = "web"
db_service = "db"
db_env_var = "DATABASE_URL"
app_port = 4000
""",
                encoding="utf-8",
            )
            spec = load_spec_from_cli(
                name="payments",
                repo_arg=td,
                config_path_arg=str(cfg),
                app_port_arg=5000,
            )
            self.assertEqual(spec.app_port, 5000)

    def test_app_port_invalid_values(self):
        for invalid_port in [0, 65536, -1, 70000]:
            with self.assertRaises(SpecError) as ctx:
                load_spec_from_cli(
                    name="payments",
                    repo_arg="/path/to/repo",
                    compose_files_arg=["compose.yaml"],
                    app_service_arg="web",
                    db_service_arg="db",
                    db_env_var_arg="DATABASE_URL",
                    app_port_arg=invalid_port,
                )
            self.assertIn("must be an integer between 1 and 65535", str(ctx.exception))

    def test_extract_candidate_container_ports(self):
        # Short syntax string
        service_short = {"ports": ["8080:3000", "5000"]}
        self.assertEqual(extract_candidate_container_ports(service_short), {3000, 5000})

        # Expose list
        service_expose = {"expose": [4000, "4001"]}
        self.assertEqual(extract_candidate_container_ports(service_expose), {4000, 4001})

        # Long syntax dict
        service_long = {"ports": [{"target": 8000, "published": "8080"}]}
        self.assertEqual(extract_candidate_container_ports(service_long), {8000})

        # Empty
        self.assertEqual(extract_candidate_container_ports({}), set())

    def test_infer_single_port_success(self):
        service = {"ports": ["3000:3000"]}
        inferred = infer_or_validate_app_port(service, "web", None)
        self.assertEqual(inferred, 3000)

    def test_infer_zero_ports_refusal(self):
        service = {"image": "node:20"}
        with self.assertRaises(ValidationError) as ctx:
            infer_or_validate_app_port(service, "web", None)
        self.assertIn("does not publish or expose any container ports", str(ctx.exception))
        self.assertIn("Please explicitly configure 'app_port'", str(ctx.exception))

    def test_infer_multiple_ports_refusal(self):
        service = {"ports": ["3000:3000", "8080:8080"]}
        with self.assertRaises(ValidationError) as ctx:
            infer_or_validate_app_port(service, "web", None)
        self.assertIn("exposes multiple candidate container ports", str(ctx.exception))
        self.assertIn("Please explicitly configure 'app_port'", str(ctx.exception))

    def test_explicit_app_port_validates_regardless_of_service_ports(self):
        service = {"ports": ["3000:3000", "8080:8080"]}
        validated = infer_or_validate_app_port(service, "web", 3000)
        self.assertEqual(validated, 3000)

        # Even with zero ports in compose, explicit app_port is accepted
        empty_service = {}
        validated2 = infer_or_validate_app_port(empty_service, "web", 9000)
        self.assertEqual(validated2, 9000)

    @patch("isolation_check.create.validate.is_git_repository", return_value=True)
    @patch("isolation_check.create.validate.resolve_commit_hash", return_value="12345678")
    @patch("isolation_check.create.validate.branch_exists", return_value=False)
    @patch("isolation_check.create.validate.collect_raw_compose_config")
    def test_effective_merged_compose_inference(self, mock_compose, *_):
        """Inference runs against the effective merged Compose model, not raw unmerged files."""
        # Simulated merged result where port 8080 was provided in an overlay compose file
        mock_compose.return_value = {
            "services": {
                "web": {"image": "node:20", "ports": ["8080:8080"]},
                "db": {"image": "postgres:16"},
            }
        }
        spec = CreateSpec(
            name="payments",
            repo=Path("/fake/repo"),
            base_ref="HEAD",
            branch="payments",
            worktree_path=Path("/fake/wt"),
            compose_files=["base.yaml", "dev.yaml"],
            app_service="web",
            db_service="db",
            db_env_var="DATABASE_URL",
            app_port=None,  # should be inferred
        )
        validated = validate_spec_and_config(spec)
        self.assertEqual(validated.app_port, 8080)


class TestComposeOverridePortPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.worktree = Path(self.tmp.name)
        self.spec = CreateSpec(
            name="payments",
            repo=Path("/dummy/repo"),
            base_ref="HEAD",
            branch="payments",
            worktree_path=self.worktree,
            compose_files=["compose.yaml"],
            app_service="web",
            db_service="db",
            db_env_var="DATABASE_URL",
            app_port=3000,
            publish_db=False,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_loopback_only_app_port_and_unexposed_postgres(self):
        plan = plan_generated_environment(self.spec, {}, resolved_app_port=3000)
        raw_compose = {"services": {"db": {"ports": ["5432:5432"]}}}
        override_text = build_override_compose_content(self.spec, plan, raw_compose)

        # Application service has 127.0.0.1 loopback binding with !override
        self.assertIn("ports: !override", override_text)
        self.assertIn('"127.0.0.1:0:3000"', override_text)

        # Database service has ports reset (!reset [])
        self.assertIn("ports: !reset []", override_text)

    def test_published_postgres_when_explicitly_requested(self):
        import dataclasses
        spec_pub = dataclasses.replace(self.spec, publish_db=True)
        plan = plan_generated_environment(spec_pub, {}, resolved_app_port=3000)
        raw_compose = {"services": {"db": {"ports": ["5432:5432"]}}}
        override_text = build_override_compose_content(spec_pub, plan, raw_compose)

        # Database service does NOT reset ports if publish_db is True
        self.assertNotIn("ports: !reset []", override_text)


if __name__ == "__main__":
    unittest.main()
