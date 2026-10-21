"""Unit tests for models.py."""
import unittest

from isolation_check.models import (
    EngineScope,
    Finding,
    NormalizedEnvironment,
    Report,
    VolumeMount,
)


class TestModels(unittest.TestCase):
    def test_engine_scope_compatibility(self):
        scope_a = EngineScope(scope_id="local-demo")
        scope_b = EngineScope(scope_id="local-demo")
        scope_other = EngineScope(scope_id="other-engine")
        scope_none = EngineScope(scope_id=None)

        self.assertTrue(scope_a.is_compatible_with(scope_b))
        self.assertFalse(scope_a.is_compatible_with(scope_other))
        self.assertFalse(scope_a.is_compatible_with(scope_none))
        self.assertFalse(scope_none.is_compatible_with(scope_none))

    def test_volume_mount_to_dict(self):
        mount = VolumeMount(
            service_name="db",
            logical_key="data",
            resolved_name="custom_data",
            target="/var/lib/data",
            read_only=True,
            source_ref="services.db.volumes[0]",
        )
        d = mount.to_dict()
        self.assertEqual(d["service_name"], "db")
        self.assertEqual(d["logical_key"], "data")
        self.assertEqual(d["resolved_name"], "custom_data")
        self.assertEqual(d["target"], "/var/lib/data")
        self.assertTrue(d["read_only"])
        self.assertFalse(d["is_anonymous"])

    def test_finding_to_dict(self):
        finding = Finding(
            rule_id="VOL001",
            status="fail",
            severity="high",
            confidence="high",
            message="Shared writable volume",
            basis="same declared engine scope",
            resource_ref="shared_vol",
            environments=["env_a", "env_b"],
            evidence=[{"env": "a"}, {"env": "b"}],
            recommendation="Separate storage",
        )
        d = finding.to_dict()
        self.assertEqual(d["rule_id"], "VOL001")
        self.assertEqual(d["status"], "fail")
        self.assertEqual(d["resource_ref"], "shared_vol")
        self.assertEqual(d["recommendation"], "Separate storage")

    def test_report_summary_and_serialization(self):
        finding_fail = Finding(
            rule_id="VOL001",
            status="fail",
            severity="high",
            confidence="high",
            message="failure",
            basis="basis",
        )
        finding_unknown = Finding(
            rule_id="COVERAGE",
            status="unknown",
            severity="info",
            confidence="high",
            message="incomplete",
            basis="basis",
        )
        report = Report(
            findings=[finding_fail, finding_unknown],
            environments=["env_a", "env_b"],
            summary={"fail": 1, "warn": 0, "pass": 0, "unknown": 1, "ignored": 0},
        )
        d = report.to_dict()
        self.assertEqual(d["schema_version"], "1.0")
        self.assertFalse(d["complete"])
        self.assertEqual(len(d["findings"]), 2)
        self.assertEqual(d["summary"]["fail"], 1)
        self.assertEqual(d["summary"]["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
