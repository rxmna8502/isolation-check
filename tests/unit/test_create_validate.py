"""Unit tests for create validation and unsupported arrangement rejection."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from isolation_check.create.spec import CreateSpec
from isolation_check.create.validate import ValidationError, validate_spec_and_config


class TestCreateValidate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.wt = Path(self.tmp.name) / "wt"
        self.spec = CreateSpec(
            name="payments",
            repo=self.repo,
            base_ref="HEAD",
            branch="payments",
            worktree_path=self.wt,
            compose_files=["compose.yaml"],
            app_service="web",
            db_service="db",
            db_env_var="DATABASE_URL",
        )

    def tearDown(self):
        self.tmp.cleanup()

    @patch("isolation_check.create.validate.is_git_repository", return_value=False)
    def test_rejects_non_git_repository(self, _):
        with self.assertRaises(ValidationError) as ctx:
            validate_spec_and_config(self.spec)
        self.assertIn("not a valid Git repository", str(ctx.exception))

    @patch("isolation_check.create.validate.is_git_repository", return_value=True)
    @patch("isolation_check.create.validate.resolve_commit_hash", return_value="12345678")
    def test_rejects_existing_worktree_path(self, *_):
        self.wt.mkdir()
        with self.assertRaises(ValidationError) as ctx:
            validate_spec_and_config(self.spec)
        self.assertIn("Destination worktree path already exists", str(ctx.exception))

    @patch("isolation_check.create.validate.is_git_repository", return_value=True)
    @patch("isolation_check.create.validate.resolve_commit_hash", return_value="12345678")
    @patch("isolation_check.create.validate.branch_exists", return_value=True)
    def test_rejects_existing_branch(self, *_):
        with self.assertRaises(ValidationError) as ctx:
            validate_spec_and_config(self.spec)
        self.assertIn("already exists in repository", str(ctx.exception))

    @patch("isolation_check.create.validate.is_git_repository", return_value=True)
    @patch("isolation_check.create.validate.resolve_commit_hash", return_value="12345678")
    @patch("isolation_check.create.validate.branch_exists", return_value=False)
    @patch("isolation_check.create.validate.collect_raw_compose_config")
    def test_rejects_missing_app_or_db_service(self, mock_compose, *_):
        mock_compose.return_value = {
            "services": {"other": {"image": "nginx"}}
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_spec_and_config(self.spec)
        self.assertIn("application service 'web' was not found", str(ctx.exception))

    @patch("isolation_check.create.validate.is_git_repository", return_value=True)
    @patch("isolation_check.create.validate.resolve_commit_hash", return_value="12345678")
    @patch("isolation_check.create.validate.branch_exists", return_value=False)
    @patch("isolation_check.create.validate.collect_raw_compose_config")
    def test_rejects_custom_database_entrypoint(self, mock_compose, *_):
        mock_compose.return_value = {
            "services": {
                "web": {"image": "node:20"},
                "db": {
                    "image": "postgres:16",
                    "entrypoint": ["/entrypoint.sh"],
                },
            }
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_spec_and_config(self.spec)
        self.assertIn("defines a custom entrypoint", str(ctx.exception))

    @patch("isolation_check.create.validate.is_git_repository", return_value=True)
    @patch("isolation_check.create.validate.resolve_commit_hash", return_value="12345678")
    @patch("isolation_check.create.validate.branch_exists", return_value=False)
    @patch("isolation_check.create.validate.collect_raw_compose_config")
    def test_rejects_fixed_container_names(self, mock_compose, *_):
        mock_compose.return_value = {
            "services": {
                "web": {"image": "node:20", "container_name": "fixed-web"},
                "db": {"image": "postgres:16"},
            }
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_spec_and_config(self.spec)
        self.assertIn("specifies fixed container_name 'fixed-web'", str(ctx.exception))

    @patch("isolation_check.create.validate.is_git_repository", return_value=True)
    @patch("isolation_check.create.validate.resolve_commit_hash", return_value="12345678")
    @patch("isolation_check.create.validate.branch_exists", return_value=False)
    @patch("isolation_check.create.validate.collect_raw_compose_config")
    def test_rejects_external_storage_drivers(self, mock_compose, *_):
        mock_compose.return_value = {
            "services": {
                "web": {"image": "node:20"},
                "db": {"image": "postgres:16"},
            },
            "volumes": {
                "db_data": {"driver": "nfs"},
            },
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_spec_and_config(self.spec)
        self.assertIn("uses custom storage driver 'nfs'", str(ctx.exception))

    @patch("isolation_check.create.validate.is_git_repository", return_value=True)
    @patch("isolation_check.create.validate.resolve_commit_hash", return_value="12345678")
    @patch("isolation_check.create.validate.branch_exists", return_value=False)
    @patch("isolation_check.create.validate.collect_raw_compose_config")
    def test_rejects_shared_network_mode_host(self, mock_compose, *_):
        mock_compose.return_value = {
            "services": {
                "web": {"image": "node:20", "network_mode": "host"},
                "db": {"image": "postgres:16"},
            }
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_spec_and_config(self.spec)
        self.assertIn("uses 'network_mode: host'", str(ctx.exception))

    @patch("isolation_check.create.validate.is_git_repository", return_value=True)
    @patch("isolation_check.create.validate.resolve_commit_hash", return_value="12345678")
    @patch("isolation_check.create.validate.branch_exists", return_value=False)
    @patch("isolation_check.create.validate.collect_raw_compose_config")
    def test_rejects_external_networks(self, mock_compose, *_):
        mock_compose.return_value = {
            "services": {
                "web": {"image": "node:20"},
                "db": {"image": "postgres:16"},
            },
            "networks": {
                "shared_net": {"external": True},
            },
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_spec_and_config(self.spec)
        self.assertIn("is declared as external", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
