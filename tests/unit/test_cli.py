"""Unit tests for cli.py."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from isolation_check import cli
from isolation_check.version import get_package_version

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
        )

    def test_cli_down_rejects_v_flag(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["down", "feature-payments", "-v"])
            self.assertEqual(ctx.exception.code, 2)
        self.assertIn("unrecognized arguments: -v", err.getvalue())

    def test_cli_down_rejects_volumes_flag(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["down", "feature-payments", "--volumes"])
            self.assertEqual(ctx.exception.code, 2)
        self.assertIn("unrecognized arguments: --volumes", err.getvalue())

    def test_cli_down_help_does_not_contain_destructive_flags(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["down", "--help"])
            self.assertEqual(ctx.exception.code, 0)
        help_text = out.getvalue()
        self.assertNotIn("-v", help_text)
        self.assertNotIn("--volumes", help_text)
        self.assertIn("Stop a managed environment while preserving data volumes.", help_text)

    def test_exit_code_contract_vol001_fail_text_and_json(self):
        left = str(FIXTURES_JSON / "1_shared_custom_volume_a.json")
        right = str(FIXTURES_JSON / "1_shared_custom_volume_b.json")

        # Text mode
        out_txt = io.StringIO()
        with contextlib.redirect_stdout(out_txt):
            code_txt = cli.main(["check", left, right, "--format", "text"])
        self.assertEqual(code_txt, 1)

        # JSON mode
        out_json = io.StringIO()
        with contextlib.redirect_stdout(out_json):
            code_json = cli.main(["check", left, right, "--format", "json"])
        self.assertEqual(code_json, 1)

    def test_exit_code_contract_port001_fail_text_and_json(self):
        left = str(FIXTURES_JSON / "ports" / "conflict_exact_ip_a.json")
        right = str(FIXTURES_JSON / "ports" / "conflict_exact_ip_b.json")

        out_txt = io.StringIO()
        with contextlib.redirect_stdout(out_txt):
            code_txt = cli.main(["check", left, right, "--format", "text"])
        self.assertEqual(code_txt, 1)

        out_json = io.StringIO()
        with contextlib.redirect_stdout(out_json):
            code_json = cli.main(["check", left, right, "--format", "json"])
        self.assertEqual(code_json, 1)

    def test_exit_code_contract_multiple_failures_text_and_json(self):
        left = str(FIXTURES_JSON / "multi_conflict_a.json")
        right = str(FIXTURES_JSON / "multi_conflict_b.json")

        out_txt = io.StringIO()
        with contextlib.redirect_stdout(out_txt):
            code_txt = cli.main(["check", left, right, "--format", "text"])
        self.assertEqual(code_txt, 1)

        out_json = io.StringIO()
        with contextlib.redirect_stdout(out_json):
            code_json = cli.main(["check", left, right, "--format", "json"])
        self.assertEqual(code_json, 1)
        data = json.loads(out_json.getvalue())
        self.assertGreaterEqual(data["summary"]["fail"], 2)

    def test_exit_code_contract_pass_with_unknown_coverage_normal_mode(self):
        left = str(FIXTURES_JSON / "2_distinct_project_volumes_a.json")
        right = str(FIXTURES_JSON / "2_distinct_project_volumes_b.json")

        out_txt = io.StringIO()
        with contextlib.redirect_stdout(out_txt):
            code_txt = cli.main(["check", left, right, "--format", "text"])
        self.assertEqual(code_txt, 0)

        out_json = io.StringIO()
        with contextlib.redirect_stdout(out_json):
            code_json = cli.main(["check", left, right, "--format", "json"])
        self.assertEqual(code_json, 0)

    def test_exit_code_contract_unknown_in_strict_mode(self):
        left = str(FIXTURES_JSON / "2_distinct_project_volumes_a.json")
        right = str(FIXTURES_JSON / "2_distinct_project_volumes_b.json")

        out_txt = io.StringIO()
        with contextlib.redirect_stdout(out_txt):
            code_txt = cli.main(["check", left, right, "--strict", "--format", "text"])
        self.assertEqual(code_txt, 2)

        out_json = io.StringIO()
        with contextlib.redirect_stdout(out_json):
            code_json = cli.main(["check", left, right, "--strict", "--format", "json"])
        self.assertEqual(code_json, 2)

    def test_exit_code_contract_invalid_input_returns_2(self):
        # Missing inputs
        err_txt = io.StringIO()
        with contextlib.redirect_stderr(err_txt):
            code_missing = cli.main(["check"])
        self.assertEqual(code_missing, 2)

        # Nonexistent files in text mode
        err_txt = io.StringIO()
        with contextlib.redirect_stderr(err_txt):
            code_nonexistent_txt = cli.main(["check", "bad1.json", "bad2.json", "--format", "text"])
        self.assertEqual(code_nonexistent_txt, 2)

        # Nonexistent files in JSON mode
        out_json = io.StringIO()
        with contextlib.redirect_stdout(out_json):
            code_nonexistent_json = cli.main(["check", "bad1.json", "bad2.json", "--format", "json"])
        self.assertEqual(code_nonexistent_json, 2)

    def test_cli_version_flag(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--version"])
            self.assertEqual(ctx.exception.code, 0)
        self.assertIn("isolation-check", out.getvalue())
        self.assertIn(get_package_version(), out.getvalue())


if __name__ == "__main__":
    unittest.main()


