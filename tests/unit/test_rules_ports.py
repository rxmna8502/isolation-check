"""Unit tests for rules/ports.py covering fixed IPv4 host-port collision detection."""
from pathlib import Path
import unittest

from isolation_check.collect.compose import load_compose_json_file
from isolation_check.models import EngineScope, HostScope, NormalizedEnvironment, PortBinding
from isolation_check.normalize.compose import normalize_compose_model
from isolation_check.rules.ports import check_ports

FIXTURES_JSON = Path(__file__).parent.parent / "fixtures" / "json" / "ports"


def _make_env_with_ports(name: str, ports: list[PortBinding], host_scope_id: str | None = "local-host", has_host_net: bool = False) -> NormalizedEnvironment:
    return NormalizedEnvironment(
        label=name,
        root=f"/workspace/{name}",
        compose_project_name=name,
        engine_scope=EngineScope(scope_id="local-demo"),
        host_scope=HostScope(scope_id=host_scope_id),
        port_bindings=ports,
        has_host_networking=has_host_net,
    )


class TestRulesPorts(unittest.TestCase):
    def test_example_1_exact_ip_conflict_fails(self):
        """127.0.0.1:3000 TCP versus 127.0.0.1:3000 TCP -> conflict (FAIL)."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "conflict_exact_ip_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "conflict_exact_ip_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        port_fails = [f for f in findings if f.rule_id == "PORT001" and f.status == "fail"]

        self.assertEqual(len(port_fails), 1)
        self.assertEqual(port_fails[0].resource_ref, "TCP/3000")
        self.assertIn("overlapping fixed TCP host port 3000", port_fails[0].message)

    def test_example_2_wildcard_overlap_conflict_fails(self):
        """0.0.0.0:3000 TCP versus 127.0.0.1:3000 TCP -> conflict (FAIL)."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "conflict_wildcard_ip_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "conflict_wildcard_ip_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        port_fails = [f for f in findings if f.rule_id == "PORT001" and f.status == "fail"]

        self.assertEqual(len(port_fails), 1)
        self.assertEqual(port_fails[0].resource_ref, "TCP/3000")
        self.assertIn("0.0.0.0 vs 127.0.0.1", port_fails[0].message)

    def test_example_3_distinct_ports_passes(self):
        """Host port 3000 versus host port 3001 -> no conflict (PASS)."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "distinct_ports_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "distinct_ports_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        port_passes = [f for f in findings if f.rule_id == "PORT001" and f.status == "pass"]

        self.assertEqual(len(port_passes), 1)
        self.assertIn("No overlapping fixed IPv4 host port bindings detected", port_passes[0].message)

    def test_example_4_different_protocols_passes(self):
        """TCP 3000 versus UDP 3000 -> no conflict (PASS)."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "different_protocols_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "different_protocols_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        port_passes = [f for f in findings if f.rule_id == "PORT001" and f.status == "pass"]

        self.assertEqual(len(port_passes), 1)

    def test_example_5_same_internal_different_published_passes(self):
        """Two containers both using internal port 3000, with different published ports -> no conflict (PASS)."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "same_internal_different_published_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "same_internal_different_published_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        port_passes = [f for f in findings if f.rule_id == "PORT001" and f.status == "pass"]

        self.assertEqual(len(port_passes), 1)

    def test_dynamic_port_allocation_returns_unknown(self):
        """Target-only allocation without published port returns UNKNOWN."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "dynamic_port_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "dynamic_port_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        unknowns = [f for f in findings if f.rule_id == "PORT001" and f.status == "unknown"]

        self.assertGreaterEqual(len(unknowns), 1)
        self.assertIn("dynamic host port allocation", unknowns[0].basis)

    def test_port_range_returns_unknown(self):
        """Published port range returns UNKNOWN."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "port_range_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "port_range_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        unknowns = [f for f in findings if f.rule_id == "PORT001" and f.status == "unknown"]

        self.assertGreaterEqual(len(unknowns), 1)
        self.assertIn("published port range", unknowns[0].basis)

    def test_ipv6_binding_returns_unknown(self):
        """IPv6 binding returns UNKNOWN."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "ipv6_binding_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "ipv6_binding_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        unknowns = [f for f in findings if f.rule_id == "PORT001" and f.status == "unknown"]

        self.assertGreaterEqual(len(unknowns), 1)
        self.assertIn("unsupported IPv6 address family", unknowns[0].basis)

    def test_host_network_mode_returns_unknown(self):
        """network_mode: host returns UNKNOWN."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "host_network_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "host_network_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        unknowns = [f for f in findings if f.rule_id == "PORT001" and f.status == "unknown"]

        self.assertGreaterEqual(len(unknowns), 1)
        self.assertIn("host network mode", unknowns[0].basis)

    def test_unestablished_host_scope_returns_unknown(self):
        """Missing or mismatched host scope returns UNKNOWN."""
        binding = PortBinding(
            service_name="web",
            published_port=3000,
            target_port=3000,
            protocol="tcp",
            host_ip="127.0.0.1",
            is_wildcard=False,
        )
        env_a = _make_env_with_ports("a", [binding], host_scope_id="host-1")
        env_b = _make_env_with_ports("b", [binding], host_scope_id="host-2")

        findings = check_ports(env_a, env_b)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].status, "unknown")
        self.assertIn("host scopes do not match", findings[0].message)

        env_none = _make_env_with_ports("c", [binding], host_scope_id=None)
        findings_none = check_ports(env_a, env_none)
        self.assertEqual(findings_none[0].status, "unknown")

    def test_expose_only_ignored_for_host_ports(self):
        """expose entries are not published to the host and do not collide."""
        left_raw = load_compose_json_file(FIXTURES_JSON / "expose_only_a.json")
        right_raw = load_compose_json_file(FIXTURES_JSON / "expose_only_b.json")
        scope_e = EngineScope("demo")
        scope_h = HostScope("local-host")

        left = normalize_compose_model(left_raw, "a", "/a", scope_e, scope_h)
        right = normalize_compose_model(right_raw, "b", "/b", scope_e, scope_h)

        findings = check_ports(left, right)
        self.assertEqual(len(left.port_bindings), 0)
        self.assertEqual(len(right.port_bindings), 0)
        port_passes = [f for f in findings if f.rule_id == "PORT001" and f.status == "pass"]
        self.assertEqual(len(port_passes), 1)


if __name__ == "__main__":
    unittest.main()
