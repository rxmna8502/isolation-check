"""Unit tests for rules/volumes.py."""
from pathlib import Path
import unittest

from isolation_check.collect.compose import load_compose_json_file
from isolation_check.models import EngineScope, NormalizedEnvironment, VolumeMount
from isolation_check.normalize.compose import normalize_compose_model
from isolation_check.rules.volumes import check_volumes

FIXTURES_JSON = Path(__file__).parent.parent / "fixtures" / "json"


def _make_env(name: str, volumes: list[VolumeMount], scope_id: str | None = "local-demo") -> NormalizedEnvironment:
    return NormalizedEnvironment(
        label=name,
        root=f"/workspace/{name}",
        compose_project_name=name,
        engine_scope=EngineScope(scope_id=scope_id),
        mounted_volumes=volumes,
    )


class TestRulesVolumes(unittest.TestCase):
    def test_fixture_1_shared_writable_volume_fails(self):
        """Fixture 1: Two different Compose projects referencing same custom volume name (writable)."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "1_shared_custom_volume_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "1_shared_custom_volume_b.json")
        scope = EngineScope(scope_id="local-demo")

        left = normalize_compose_model(left_raw, "alpha", "/path/a", scope)
        right = normalize_compose_model(right_raw, "beta", "/path/b", scope)

        findings = check_volumes(left, right)
        vol_findings = [f for f in findings if f.rule_id == "VOL001"]

        self.assertEqual(len(vol_findings), 1)
        self.assertEqual(vol_findings[0].status, "fail")
        self.assertEqual(vol_findings[0].severity, "high")
        self.assertEqual(vol_findings[0].resource_ref, "shared_pgdata")
        self.assertIn("at least one mount is writable", vol_findings[0].message)

    def test_fixture_2_distinct_project_volumes_pass(self):
        """Fixture 2: Two projects using distinct project-scoped volumes."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "2_distinct_project_volumes_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "2_distinct_project_volumes_b.json")
        scope = EngineScope(scope_id="local-demo")

        left = normalize_compose_model(left_raw, "alpha", "/path/a", scope)
        right = normalize_compose_model(right_raw, "beta", "/path/b", scope)

        findings = check_volumes(left, right)
        vol_findings = [f for f in findings if f.rule_id == "VOL001"]

        self.assertEqual(len(vol_findings), 1)
        self.assertEqual(vol_findings[0].status, "pass")
        self.assertIn("No shared mounted named volumes detected", vol_findings[0].message)

    def test_fixture_3_shared_readonly_volume_warns(self):
        """Fixture 3: Shared volume mounted read-only on both sides."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "3_shared_readonly_volume_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "3_shared_readonly_volume_b.json")
        scope = EngineScope(scope_id="local-demo")

        left = normalize_compose_model(left_raw, "alpha", "/path/a", scope)
        right = normalize_compose_model(right_raw, "beta", "/path/b", scope)

        findings = check_volumes(left, right)
        vol_findings = [f for f in findings if f.rule_id == "VOL001"]

        self.assertEqual(len(vol_findings), 1)
        self.assertEqual(vol_findings[0].status, "warn")
        self.assertEqual(vol_findings[0].severity, "medium")
        self.assertEqual(vol_findings[0].resource_ref, "shared_fixtures")
        self.assertIn("all mounts are read-only", vol_findings[0].message)

    def test_fixture_4_missing_or_anonymous_identity_unknown(self):
        """Fixture 4: Missing or anonymous volume identity information."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "4_missing_identity_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "4_missing_identity_b.json")
        scope = EngineScope(scope_id="local-demo")

        left = normalize_compose_model(left_raw, "alpha", "/path/a", scope)
        right = normalize_compose_model(right_raw, "beta", "/path/b", scope)

        findings = check_volumes(left, right)
        vol_unknowns = [f for f in findings if f.rule_id == "VOL001" and f.status == "unknown"]

        self.assertGreaterEqual(len(vol_unknowns), 1)
        self.assertIn("anonymous or unresolved volume", vol_unknowns[0].basis)

    def test_fixture_5_unmounted_toplevel_volume_does_not_fail(self):
        """Fixture 5: A volume declared at top level but not mounted by selected service."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "5_unmounted_toplevel_volume_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "5_unmounted_toplevel_volume_b.json")
        scope = EngineScope(scope_id="local-demo")

        left = normalize_compose_model(left_raw, "alpha", "/path/a", scope)
        right = normalize_compose_model(right_raw, "beta", "/path/b", scope)

        findings = check_volumes(left, right)
        vol_findings = [f for f in findings if f.rule_id == "VOL001"]

        # Only one VOL001 finding, which is pass because beta does not mount shared_backup_volume
        self.assertEqual(len(vol_findings), 1)
        self.assertEqual(vol_findings[0].status, "pass")

    def test_one_side_writable_one_side_readonly_fails(self):
        """If one side is writable and the other is read-only, it is still a write collision."""
        mount_ro = VolumeMount(
            service_name="app",
            logical_key="vol",
            resolved_name="shared_vol",
            target="/data",
            read_only=True,
            source_ref="services.app.volumes[0]",
        )
        mount_rw = VolumeMount(
            service_name="db",
            logical_key="vol",
            resolved_name="shared_vol",
            target="/data",
            read_only=False,
            source_ref="services.db.volumes[0]",
        )
        env_ro = _make_env("ro_env", [mount_ro])
        env_rw = _make_env("rw_env", [mount_rw])

        findings = check_volumes(env_ro, env_rw)
        vol_findings = [f for f in findings if f.rule_id == "VOL001"]
        self.assertEqual(len(vol_findings), 1)
        self.assertEqual(vol_findings[0].status, "fail")

    def test_mismatched_scope_yields_unknown(self):
        mount = VolumeMount(
            service_name="db",
            logical_key="vol",
            resolved_name="shared_vol",
            target="/data",
            read_only=False,
            source_ref="services.db.volumes[0]",
        )
        env_a = _make_env("a", [mount], scope_id="engine-1")
        env_b = _make_env("b", [mount], scope_id="engine-2")

        findings = check_volumes(env_a, env_b)
        vol_findings = [f for f in findings if f.rule_id == "VOL001"]
        self.assertEqual(len(vol_findings), 1)
        self.assertEqual(vol_findings[0].status, "unknown")
        self.assertIn("engine scopes do not match", vol_findings[0].message)

    def test_missing_scope_yields_unknown(self):
        mount = VolumeMount(
            service_name="db",
            logical_key="vol",
            resolved_name="shared_vol",
            target="/data",
            read_only=False,
            source_ref="services.db.volumes[0]",
        )
        env_a = _make_env("a", [mount], scope_id=None)
        env_b = _make_env("b", [mount], scope_id="local-demo")

        findings = check_volumes(env_a, env_b)
        vol_findings = [f for f in findings if f.rule_id == "VOL001"]
        self.assertEqual(len(vol_findings), 1)
        self.assertEqual(vol_findings[0].status, "unknown")

    def test_coverage_check_always_present(self):
        env_a = _make_env("a", [])
        env_b = _make_env("b", [])

        findings = check_volumes(env_a, env_b)
        coverage = [f for f in findings if f.rule_id == "COVERAGE"]
        self.assertEqual(len(coverage), 1)
        self.assertEqual(coverage[0].status, "unknown")
        self.assertIn("Exit 0 does not prove complete environment isolation", coverage[0].message)


if __name__ == "__main__":
    unittest.main()
