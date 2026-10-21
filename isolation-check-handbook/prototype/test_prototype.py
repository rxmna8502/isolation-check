import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import prototype as p


def snapshot(name="shared_pgdata", scope="local-demo", read_only=False):
    return {"schema_version": "demo-v1", "scope_id": scope,
            "volumes": [{"name": name, "read_only": read_only}]}


class Tests(unittest.TestCase):
    def test_shared_writable(self):
        self.assertEqual(p.compare(snapshot(), snapshot())["checks"][0]["status"], "fail")

    def test_different_names(self):
        self.assertEqual(p.compare(snapshot(), snapshot(name="other"))["checks"][0]["status"], "pass")

    def test_different_scope(self):
        self.assertEqual(p.compare(snapshot(), snapshot(scope="other"))["checks"][0]["status"], "unknown")

    def test_missing_scope(self):
        self.assertEqual(p.compare(snapshot(scope=None), snapshot())["checks"][0]["status"], "unknown")

    def test_read_only(self):
        self.assertEqual(p.compare(snapshot(read_only=True), snapshot(read_only=True))["checks"][0]["status"], "warn")

    def test_one_writer(self):
        self.assertEqual(p.compare(snapshot(read_only=True), snapshot())["checks"][0]["status"], "fail")

    def test_cli_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            left, right = [Path(folder) / name for name in ("a.json", "b.json")]
            left.write_text(json.dumps(snapshot()))
            right.write_text(json.dumps(snapshot()))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(p.main([str(left), str(right)]), 1)
                right.write_text(json.dumps(snapshot(name="other")))
                self.assertEqual(p.main([str(left), str(right)]), 0)
                self.assertEqual(p.main([str(left), str(right), "--strict"]), 2)
                right.write_text("not json")
                self.assertEqual(p.main([str(left), str(right)]), 2)

    def test_reject_string_boolean(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            data = snapshot()
            data["volumes"][0]["read_only"] = "false"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                p.load_snapshot(path)


if __name__ == "__main__":
    unittest.main()
