"""Unit tests verifying that secrets and credentials never leak into outputs or reports."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from isolation_check import cli
from isolation_check.models import EngineScope
from isolation_check.normalize.compose import normalize_compose_model
from isolation_check.report import build_report, format_json_report, format_text_report
from isolation_check.rules.volumes import check_volumes

SECRET_SENTINEL_PW = "SECRET_PASSWORD_SENTINEL_XYZ98765"
SECRET_SENTINEL_TOKEN = "SECRET_API_TOKEN_SENTINEL_ABC12345"


class TestSecurityRedaction(unittest.TestCase):
    def test_secrets_in_compose_env_do_not_leak_into_reports(self):
        raw_a = {
            "name": "project_a",
            "services": {
                "db": {
                    "image": "postgres:16",
                    "environment": {
                        "POSTGRES_PASSWORD": SECRET_SENTINEL_PW,
                        "DATABASE_URL": f"postgres://user:{SECRET_SENTINEL_PW}@localhost:5432/db",
                        "TOKEN": SECRET_SENTINEL_TOKEN,
                    },
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "db_data",
                            "target": "/var/lib/postgresql/data",
                            "read_only": False,
                        }
                    ],
                }
            },
            "volumes": {
                "db_data": {
                    "name": "shared_pgdata",
                }
            },
        }

        raw_b = {
            "name": "project_b",
            "services": {
                "db": {
                    "image": "postgres:16",
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "db_data",
                            "target": "/var/lib/postgresql/data",
                            "read_only": False,
                        }
                    ],
                }
            },
            "volumes": {
                "db_data": {
                    "name": "shared_pgdata",
                }
            },
        }

        scope = EngineScope(scope_id="local-test")
        env_a = normalize_compose_model(raw_a, "env_a", "/path/a", scope)
        env_b = normalize_compose_model(raw_b, "env_b", "/path/b", scope)

        findings = check_volumes(env_a, env_b)
        report = build_report(findings, ["env_a", "env_b"], scope)

        text_output = format_text_report(report)
        json_output = format_json_report(report)

        # Assert secret sentinels are completely absent from text and JSON output
        self.assertNotIn(SECRET_SENTINEL_PW, text_output)
        self.assertNotIn(SECRET_SENTINEL_TOKEN, text_output)
        self.assertNotIn(SECRET_SENTINEL_PW, json_output)
        self.assertNotIn(SECRET_SENTINEL_TOKEN, json_output)

    def test_cli_error_does_not_leak_secrets(self):
        with tempfile.TemporaryDirectory() as td:
            bad_json = Path(td) / "bad_with_secret.json"
            bad_json.write_text(f'{{"invalid_json": "{SECRET_SENTINEL_PW}"')  # Malformed JSON

            valid_json = Path(td) / "valid.json"
            valid_json.write_text('{"name": "valid", "services": {}, "volumes": {}}')

            err = io.StringIO()
            out = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
                code = cli.main(["check", str(bad_json), str(valid_json)])

            self.assertEqual(code, 2)
            self.assertNotIn(SECRET_SENTINEL_PW, err.getvalue())
            self.assertNotIn(SECRET_SENTINEL_PW, out.getvalue())


if __name__ == "__main__":
    unittest.main()
