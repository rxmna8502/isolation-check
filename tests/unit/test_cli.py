"""Unit tests for cli.py."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from isolation_check import cli

FIXTURES_JSON = Path(__file__).parent.parent / "fixtures" / "json"


class TestCLI(unittest.TestCase):
    def test_shared_writable_exits_1(self):
        left = str(FIXTURES_JSON / "1_shared_custom_volume_a.json")
        right = str(FIXTURES_JSON / "1_shared_custom_volume_b.json")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", left, right])
        self.assertEqual(code, 1)
        self.assertIn("FAIL    VOL001", out.getvalue())

    def test_distinct_volumes_exits_0(self):
        left = str(FIXTURES_JSON / "2_distinct_project_volumes_a.json")
        right = str(FIXTURES_JSON / "2_distinct_project_volumes_b.json")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", left, right])
        self.assertEqual(code, 0)
        self.assertIn("PASS    VOL001", out.getvalue())

    def test_strict_mode_with_unknown_exits_2(self):
        left = str(FIXTURES_JSON / "2_distinct_project_volumes_a.json")
        right = str(FIXTURES_JSON / "2_distinct_project_volumes_b.json")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", left, right, "--strict"])
        # In strict mode, because COVERAGE is unknown, it exits 2 when there is no FAIL
        self.assertEqual(code, 2)

    def test_readonly_volume_exits_0(self):
        left = str(FIXTURES_JSON / "3_shared_readonly_volume_a.json")
        right = str(FIXTURES_JSON / "3_shared_readonly_volume_b.json")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", left, right])
        self.assertEqual(code, 0)
        self.assertIn("WARN    VOL001", out.getvalue())

    def test_missing_environment_inputs_exits_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(["check"])
        self.assertEqual(code, 2)
        self.assertIn("Two environment paths", err.getvalue())

    def test_nonexistent_file_exits_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(["check", "nonexistent_a.json", "nonexistent_b.json"])
        self.assertEqual(code, 2)
        self.assertIn("Target path does not exist", err.getvalue())

    def test_json_output_format(self):
        left = str(FIXTURES_JSON / "1_shared_custom_volume_a.json")
        right = str(FIXTURES_JSON / "1_shared_custom_volume_b.json")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", left, right, "--format", "json"])
        self.assertEqual(code, 1)

        data = json.loads(out.getvalue())
        self.assertEqual(data["schema_version"], "1.0")
        self.assertEqual(data["summary"]["fail"], 1)
        self.assertFalse(data["complete"])

    def test_cli_ports_exact_ip_conflict_exits_1(self):
        left = str(FIXTURES_JSON / "ports" / "conflict_exact_ip_a.json")
        right = str(FIXTURES_JSON / "ports" / "conflict_exact_ip_b.json")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", left, right])
        self.assertEqual(code, 1)
        self.assertIn("FAIL    PORT001", out.getvalue())
        self.assertIn("127.0.0.1:3000/TCP", out.getvalue())

    def test_cli_ports_distinct_exits_0(self):
        left = str(FIXTURES_JSON / "ports" / "distinct_ports_a.json")
        right = str(FIXTURES_JSON / "ports" / "distinct_ports_b.json")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", left, right])
        self.assertEqual(code, 0)
        self.assertIn("PASS    PORT001", out.getvalue())

    def test_cli_ports_no_shared_host_returns_unknown(self):
        left = str(FIXTURES_JSON / "ports" / "conflict_exact_ip_a.json")
        right = str(FIXTURES_JSON / "ports" / "conflict_exact_ip_b.json")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", left, right, "--no-shared-host"])
        self.assertEqual(code, 0)
        self.assertIn("UNKNOWN PORT001", out.getvalue())

    def test_cli_ports_no_shared_host_strict_exits_2(self):
        left = str(FIXTURES_JSON / "ports" / "conflict_exact_ip_a.json")
        right = str(FIXTURES_JSON / "ports" / "conflict_exact_ip_b.json")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", left, right, "--no-shared-host", "--strict"])
        self.assertEqual(code, 2)

    @patch("isolation_check.runtime.lifecycle.execute_up")
    def test_cli_up_dispatches(self, mock_up):
        mock_up.return_value = 0
        code = cli.main(["up", "feature-payments", "--json"])
        self.assertEqual(code, 0)
        mock_up.assert_called_once_with(
            env_name="feature-payments",
            repo_hint=None,
            output_json=True,
        )

    @patch("isolation_check.runtime.lifecycle.execute_status")
    def test_cli_status_dispatches(self, mock_status):
        mock_status.return_value = 0
        code = cli.main(["status", "feature-payments"])
        self.assertEqual(code, 0)
        mock_status.assert_called_once_with(
            env_name="feature-payments",
            repo_hint=None,
            output_json=False,
        )

    @patch("isolation_check.runtime.lifecycle.execute_down")
    def test_cli_down_dispatches(self, mock_down):
        mock_down.return_value = 0
        code = cli.main(["down", "feature-payments"])
        self.assertEqual(code, 0)
        mock_down.assert_called_once_with(
            env_name="feature-payments",
            repo_hint=None,
            output_json=False,
            destructive=False,
        )

    @patch("isolation_check.runtime.lifecycle.execute_down")
    def test_cli_down_with_volumes_flag_passes_destructive(self, mock_down):
        mock_down.return_value = 2
        code = cli.main(["down", "feature-payments", "-v"])
        self.assertEqual(code, 2)
        mock_down.assert_called_once_with(
            env_name="feature-payments",
            repo_hint=None,
            output_json=False,
            destructive=True,
        )


if __name__ == "__main__":
    unittest.main()

