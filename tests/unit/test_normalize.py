"""Unit tests for normalize/compose.py."""
import unittest

from isolation_check.models import EngineScope
from isolation_check.normalize.compose import normalize_compose_model


class TestNormalizeCompose(unittest.TestCase):
    def setUp(self):
        self.scope = EngineScope(scope_id="local-demo")

    def test_custom_resolved_volume_name(self):
        raw = {
            "name": "my_project",
            "services": {
                "db": {
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "db_data",
                            "target": "/var/lib/data",
                            "read_only": False,
                        }
                    ]
                }
            },
            "volumes": {
                "db_data": {
                    "name": "shared_pgdata",
                }
            },
        }
        env = normalize_compose_model(raw, "my_env", "/path/to/my_env", self.scope)
        self.assertEqual(env.label, "my_env")
        self.assertEqual(env.compose_project_name, "my_project")
        self.assertEqual(len(env.mounted_volumes), 1)

        mount = env.mounted_volumes[0]
        self.assertEqual(mount.service_name, "db")
        self.assertEqual(mount.logical_key, "db_data")
        self.assertEqual(mount.resolved_name, "shared_pgdata")
        self.assertFalse(mount.read_only)
        self.assertFalse(mount.is_anonymous)

    def test_project_scoped_volume_resolution(self):
        raw = {
            "name": "my_project",
            "services": {
                "db": {
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "db_data",
                            "target": "/var/lib/data",
                            "read_only": True,
                        }
                    ]
                }
            },
            "volumes": {
                "db_data": {
                    "name": "my_project_db_data",
                }
            },
        }
        env = normalize_compose_model(raw, "my_env", "/path/to/my_env", self.scope)
        mount = env.mounted_volumes[0]
        self.assertEqual(mount.resolved_name, "my_project_db_data")
        self.assertTrue(mount.read_only)

    def test_anonymous_volume_normalization(self):
        raw = {
            "name": "anon_proj",
            "services": {
                "worker": {
                    "volumes": [
                        {
                            "type": "volume",
                            "target": "/tmp/cache",
                            "read_only": False,
                        }
                    ]
                }
            },
            "volumes": {},
        }
        env = normalize_compose_model(raw, "anon_env", "/path", self.scope)
        self.assertEqual(len(env.mounted_volumes), 1)
        mount = env.mounted_volumes[0]
        self.assertTrue(mount.is_anonymous)
        self.assertIsNone(mount.resolved_name)
        self.assertEqual(len(env.uncertainties), 1)

    def test_unmounted_toplevel_volume_is_not_in_mounted_volumes(self):
        raw = {
            "name": "unmounted_proj",
            "services": {
                "web": {
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "active_vol",
                            "target": "/data",
                        }
                    ]
                }
            },
            "volumes": {
                "active_vol": {"name": "active_data"},
                "unused_vol": {"name": "dormant_shared_data"},
            },
        }
        env = normalize_compose_model(raw, "env", "/path", self.scope)
        self.assertEqual(len(env.mounted_volumes), 1)
        self.assertEqual(env.mounted_volumes[0].resolved_name, "active_data")
        self.assertIn("unused_vol", env.top_level_volumes)
        # Ensure unused_vol is NOT in mounted_volumes
        mounted_names = [m.resolved_name for m in env.mounted_volumes]
        self.assertNotIn("dormant_shared_data", mounted_names)

    def test_bind_mounts_are_not_added_to_volume_mounts(self):
        raw = {
            "name": "bind_proj",
            "services": {
                "web": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/host/path",
                            "target": "/container/path",
                        }
                    ]
                }
            },
            "volumes": {},
        }
        env = normalize_compose_model(raw, "env", "/path", self.scope)
        self.assertEqual(len(env.mounted_volumes), 0)


class TestNormalizePorts(unittest.TestCase):
    def setUp(self):
        self.scope = EngineScope(scope_id="local-demo")

    def test_missing_ports_and_services(self):
        raw = {}
        env = normalize_compose_model(raw, "empty", "/path", self.scope)
        self.assertEqual(len(env.port_bindings), 0)
        self.assertFalse(env.has_host_networking)

        raw_empty_services = {"services": {}}
        env2 = normalize_compose_model(raw_empty_services, "empty_svc", "/path", self.scope)
        self.assertEqual(len(env2.port_bindings), 0)

    def test_malformed_port_entries_handled_gracefully(self):
        raw = {
            "services": {
                "web": {
                    "ports": [
                        "not-a-port",
                        "999999:80",  # published out of range (1-65535)
                        None,
                        {},
                        12345,
                        {"published": "invalid", "target": "invalid"},
                    ]
                }
            }
        }
        env = normalize_compose_model(raw, "malformed", "/path", self.scope)
        self.assertEqual(len(env.port_bindings), 6)
        # All invalid published ports become dynamic/None rather than crashing
        for binding in env.port_bindings:
            if binding.published_port is not None:
                self.fail(f"Expected None published port, got {binding.published_port}")

    def test_ipv6_bracket_syntax_shortform(self):
        raw = {
            "services": {
                "web": {
                    "ports": [
                        "[::1]:8080:80",
                    ]
                }
            }
        }
        env = normalize_compose_model(raw, "ipv6_env", "/path", self.scope)
        self.assertEqual(len(env.port_bindings), 1)
        binding = env.port_bindings[0]
        self.assertTrue(binding.is_ipv6)
        self.assertEqual(binding.host_ip, "::1")
        self.assertEqual(binding.published_port, 8080)
        self.assertEqual(binding.target_port, 80)


if __name__ == "__main__":
    unittest.main()
