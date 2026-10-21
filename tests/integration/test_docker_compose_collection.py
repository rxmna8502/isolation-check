"""Integration tests validating collection using real `docker compose config` on sanitized fixtures."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import unittest

from isolation_check import cli

FIXTURES_COMPOSE = Path(__file__).parent.parent / "fixtures" / "compose"
HAS_DOCKER = shutil.which("docker") is not None


@unittest.skipUnless(HAS_DOCKER, "Docker is not available in PATH")
class TestDockerComposeIntegration(unittest.TestCase):
    # Volume tests
    def test_fixture_1_compose_yaml_fails(self):
        env_a = str(FIXTURES_COMPOSE / "1_shared_custom_volume" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "1_shared_custom_volume" / "env_b")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", env_a, env_b, "--format", "json"])

        self.assertEqual(code, 1)
        data = json.loads(out.getvalue())
        self.assertEqual(data["summary"]["fail"], 1)
        vol_fails = [f for f in data["findings"] if f["rule_id"] == "VOL001" and f["status"] == "fail"]
        self.assertEqual(len(vol_fails), 1)
        self.assertEqual(vol_fails[0]["resource_ref"], "shared_pgdata")

    def test_fixture_2_compose_yaml_passes(self):
        env_a = str(FIXTURES_COMPOSE / "2_distinct_project_volumes" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "2_distinct_project_volumes" / "env_b")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", env_a, env_b, "--format", "json"])

        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["summary"]["fail"], 0)
        vol_passes = [f for f in data["findings"] if f["rule_id"] == "VOL001" and f["status"] == "pass"]
        self.assertEqual(len(vol_passes), 1)

    def test_fixture_3_compose_yaml_warns(self):
        env_a = str(FIXTURES_COMPOSE / "3_shared_readonly_volume" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "3_shared_readonly_volume" / "env_b")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", env_a, env_b, "--format", "json"])

        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["summary"]["fail"], 0)
        self.assertEqual(data["summary"]["warn"], 1)
        vol_warns = [f for f in data["findings"] if f["rule_id"] == "VOL001" and f["status"] == "warn"]
        self.assertEqual(len(vol_warns), 1)
        self.assertEqual(vol_warns[0]["resource_ref"], "shared_fixtures")

    def test_fixture_5_compose_yaml_unmounted_toplevel(self):
        env_a = str(FIXTURES_COMPOSE / "5_unmounted_toplevel_volume" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "5_unmounted_toplevel_volume" / "env_b")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", env_a, env_b, "--format", "json"])

        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["summary"]["fail"], 0)
        vol_passes = [f for f in data["findings"] if f["rule_id"] == "VOL001" and f["status"] == "pass"]
        self.assertEqual(len(vol_passes), 1)

    # Port tests
    def test_ports_conflict_exact_ip_fails(self):
        env_a = str(FIXTURES_COMPOSE / "ports" / "conflict_exact_ip" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "ports" / "conflict_exact_ip" / "env_b")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", env_a, env_b, "--format", "json"])

        self.assertEqual(code, 1)
        data = json.loads(out.getvalue())
        port_fails = [f for f in data["findings"] if f["rule_id"] == "PORT001" and f["status"] == "fail"]
        self.assertEqual(len(port_fails), 1)
        self.assertEqual(port_fails[0]["resource_ref"], "TCP/3000")

    def test_ports_conflict_wildcard_ip_fails(self):
        env_a = str(FIXTURES_COMPOSE / "ports" / "conflict_wildcard_ip" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "ports" / "conflict_wildcard_ip" / "env_b")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", env_a, env_b, "--format", "json"])

        self.assertEqual(code, 1)
        data = json.loads(out.getvalue())
        port_fails = [f for f in data["findings"] if f["rule_id"] == "PORT001" and f["status"] == "fail"]
        self.assertEqual(len(port_fails), 1)
        self.assertEqual(port_fails[0]["resource_ref"], "TCP/3000")

    def test_ports_distinct_passes(self):
        env_a = str(FIXTURES_COMPOSE / "ports" / "distinct_ports" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "ports" / "distinct_ports" / "env_b")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", env_a, env_b, "--format", "json"])

        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        port_passes = [f for f in data["findings"] if f["rule_id"] == "PORT001" and f["status"] == "pass"]
        self.assertEqual(len(port_passes), 1)

    def test_ports_different_protocols_passes(self):
        env_a = str(FIXTURES_COMPOSE / "ports" / "different_protocols" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "ports" / "different_protocols" / "env_b")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", env_a, env_b, "--format", "json"])

        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        port_passes = [f for f in data["findings"] if f["rule_id"] == "PORT001" and f["status"] == "pass"]
        self.assertEqual(len(port_passes), 1)

    def test_ports_same_internal_different_published_passes(self):
        env_a = str(FIXTURES_COMPOSE / "ports" / "same_internal_different_published" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "ports" / "same_internal_different_published" / "env_b")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["check", env_a, env_b, "--format", "json"])

        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        port_passes = [f for f in data["findings"] if f["rule_id"] == "PORT001" and f["status"] == "pass"]
        self.assertEqual(len(port_passes), 1)

    def test_ports_dynamic_port_unknown(self):
        env_a = str(FIXTURES_COMPOSE / "ports" / "dynamic_port" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "ports" / "dynamic_port" / "env_b")

        out_normal = io.StringIO()
        with contextlib.redirect_stdout(out_normal):
            code_normal = cli.main(["check", env_a, env_b, "--format", "json"])
        self.assertEqual(code_normal, 0)

        out_strict = io.StringIO()
        with contextlib.redirect_stdout(out_strict):
            code_strict = cli.main(["check", env_a, env_b, "--strict"])
        self.assertEqual(code_strict, 2)

    def test_ports_host_network_unknown(self):
        env_a = str(FIXTURES_COMPOSE / "ports" / "host_network" / "env_a")
        env_b = str(FIXTURES_COMPOSE / "ports" / "host_network" / "env_b")

        out_normal = io.StringIO()
        with contextlib.redirect_stdout(out_normal):
            code_normal = cli.main(["check", env_a, env_b, "--format", "json"])
        self.assertEqual(code_normal, 0)

        out_strict = io.StringIO()
        with contextlib.redirect_stdout(out_strict):
            code_strict = cli.main(["check", env_a, env_b, "--strict"])
        self.assertEqual(code_strict, 2)


if __name__ == "__main__":
    unittest.main()
