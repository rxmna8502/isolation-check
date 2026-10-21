"""Unit tests for runtime ownership and path validation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from isolation_check.runtime.lifecycle import _inspect_compose_containers
from isolation_check.runtime.validation import OwnershipError, locate_and_validate_environment


class TestRuntimeOwnershipValidation(unittest.TestCase):
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
        # Env file in worktree
        (self.wt / ".isolation-check" / "isolation.env").write_text("DB_URL=test\n", encoding="utf-8")

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

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    def test_locate_and_validate_success(self, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]

        rec_path, rec_d, man_path, man_d = locate_and_validate_environment(self.repo, "feature-x")
        self.assertEqual(rec_d["name"], "feature-x")
        self.assertEqual(man_d["environment"]["name"], "feature-x")
        self.assertEqual(man_path, self.manifest_file)

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    def test_missing_environment_record_rejected(self, mock_repo_root):
        mock_repo_root.return_value = self.repo
        with self.assertRaises(OwnershipError) as ctx:
            locate_and_validate_environment(self.repo, "nonexistent")
        self.assertIn("not found in repository records", str(ctx.exception))

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    def test_record_name_mismatch_rejected(self, mock_repo_root):
        mock_repo_root.return_value = self.repo
        self.rec_data["name"] = "other-name"
        self.rec_file.write_text(json.dumps(self.rec_data), encoding="utf-8")

        with self.assertRaises(OwnershipError) as ctx:
            locate_and_validate_environment(self.repo, "feature-x")
        self.assertIn("Record mismatch", str(ctx.exception))

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    def test_missing_worktree_dir_rejected(self, mock_repo_root):
        mock_repo_root.return_value = self.repo
        self.rec_data["worktree_path"] = str(Path(self.tmp.name) / "does-not-exist")
        self.rec_file.write_text(json.dumps(self.rec_data), encoding="utf-8")

        with self.assertRaises(OwnershipError) as ctx:
            locate_and_validate_environment(self.repo, "feature-x")
        self.assertIn("Worktree directory for environment 'feature-x' does not exist", str(ctx.exception))

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    def test_unregistered_git_worktree_rejected(self, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = []  # worktree not registered with git

        with self.assertRaises(OwnershipError) as ctx:
            locate_and_validate_environment(self.repo, "feature-x")
        self.assertIn("is not registered with Git repository", str(ctx.exception))

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    def test_missing_manifest_rejected(self, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]
        self.manifest_file.unlink()

        with self.assertRaises(OwnershipError) as ctx:
            locate_and_validate_environment(self.repo, "feature-x")
        self.assertIn("missing ownership manifest", str(ctx.exception))

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    def test_manifest_tool_mismatch_rejected(self, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]
        self.manifest_data["tool"] = "other-tool"
        self.manifest_file.write_text(json.dumps(self.manifest_data), encoding="utf-8")

        with self.assertRaises(OwnershipError) as ctx:
            locate_and_validate_environment(self.repo, "feature-x")
        self.assertIn("is not owned by isolation-check", str(ctx.exception))

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    def test_project_name_mismatch_rejected(self, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]
        self.manifest_data["compose"]["project_name"] = "different-project"
        self.manifest_file.write_text(json.dumps(self.manifest_data), encoding="utf-8")

        with self.assertRaises(OwnershipError) as ctx:
            locate_and_validate_environment(self.repo, "feature-x")
        self.assertIn("Compose project mismatch", str(ctx.exception))

    @patch("isolation_check.runtime.validation._resolve_repo_root")
    @patch("isolation_check.runtime.validation._get_registered_git_worktrees")
    def test_path_traversal_escape_rejected(self, mock_wt_list, mock_repo_root):
        mock_repo_root.return_value = self.repo
        mock_wt_list.return_value = [self.wt]
        # Attempt traversal outside worktree
        self.manifest_data["compose"]["base_compose_files"] = ["../../etc/passwd"]
        self.manifest_file.write_text(json.dumps(self.manifest_data), encoding="utf-8")

        with self.assertRaises(OwnershipError) as ctx:
            locate_and_validate_environment(self.repo, "feature-x")
        self.assertIn("Path traversal detected", str(ctx.exception))

    @patch("isolation_check.runtime.lifecycle._run_compose_cmd")
    def test_foreign_compose_project_containers_rejected(self, mock_cmd):
        mock_cmd.return_value = (
            0,
            json.dumps([
                {
                    "Name": "rogue-container",
                    "Service": "web",
                    "Labels": {"com.docker.compose.project": "foreign-project"},
                }
            ]),
            "",
        )
        with self.assertRaises(OwnershipError) as ctx:
            _inspect_compose_containers(
                self.wt,
                "feature-x",
                ["compose.yaml"],
                ".isolation-check/compose.override.yaml",
            )
        self.assertIn("belongs to project 'foreign-project', not expected project 'feature-x'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
