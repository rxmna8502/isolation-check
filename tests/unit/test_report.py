"""Unit tests for report.py."""
import json
import unittest

from isolation_check.models import EngineScope, Finding
from isolation_check.report import build_report, format_json_report, format_text_report


class TestReport(unittest.TestCase):
    def setUp(self):
        self.scope = EngineScope(scope_id="local-docker-engine", basis="explicit shared local-engine assumption")
        self.finding_fail = Finding(
            rule_id="VOL001",
            status="fail",
            severity="high",
            confidence="high",
            message="Both environments configure access to shared volume 'shared_pgdata'",
            basis="same resolved volume name",
            resource_ref="shared_pgdata",
            environments=["env_a", "env_b"],
            evidence=[
                {
                    "environment": "env_a",
                    "service": "db",
                    "logical_key": "db_data",
                    "resolved_name": "shared_pgdata",
                    "read_only": False,
                    "target": "/var/lib/data",
                }
            ],
            recommendation="Separate storage",
        )
        self.finding_coverage = Finding(
            rule_id="COVERAGE",
            status="unknown",
            severity="info",
            confidence="high",
            message="Ports, bind mounts, and databases not evaluated.",
            basis="partial evaluation",
            environments=["env_a", "env_b"],
        )

    def test_build_report_summary(self):
        report = build_report([self.finding_fail, self.finding_coverage], ["env_a", "env_b"], self.scope)
        self.assertEqual(report.summary["fail"], 1)
        self.assertEqual(report.summary["unknown"], 1)
        self.assertEqual(report.summary["warn"], 0)
        self.assertEqual(report.summary["pass"], 0)
        self.assertFalse(report.complete)

    def test_format_text_report(self):
        report = build_report([self.finding_fail, self.finding_coverage], ["env_a", "env_b"], self.scope)
        text = format_text_report(report)

        self.assertIn("isolation-check -- CONFIGURATION AUDIT", text)
        self.assertIn("Scope: local-docker-engine", text)
        self.assertIn("FAIL    VOL001", text)
        self.assertIn("shared_pgdata", text)
        self.assertIn("UNKNOWN COVERAGE", text)
        self.assertIn("Summary: 1 fail, 0 warn, 0 pass, 1 unknown, 0 ignored", text)
        self.assertIn("Coverage: incomplete", text)

    def test_format_json_report(self):
        report = build_report([self.finding_fail, self.finding_coverage], ["env_a", "env_b"], self.scope)
        json_str = format_json_report(report)
        data = json.loads(json_str)

        from isolation_check.version import get_package_version
        self.assertEqual(data["schema_version"], "1.0")
        self.assertEqual(data["tool_version"], get_package_version())
        self.assertEqual(data["mode"], "configuration-only")
        self.assertFalse(data["complete"])
        self.assertEqual(data["summary"]["fail"], 1)
        self.assertEqual(data["summary"]["unknown"], 1)
        self.assertEqual(len(data["findings"]), 2)
        self.assertEqual(data["findings"][0]["rule_id"], "VOL001")
        self.assertEqual(data["findings"][0]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
