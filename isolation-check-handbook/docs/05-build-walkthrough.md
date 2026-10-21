# 05 — Build Walkthrough & Tested Teaching Prototype

## What this chapter gives you

A small executable Python example that compares **already normalized named-volume snapshots**. It is an educational stepping stone, not the Docker/Git collector or a released version of isolation-check.

The exact example was executed in the documentation workspace with eight unit tests; all passed. That validates only the example's tested cases, not the proposed full project, supported platforms, or production security.

## Step 1 — Create a disposable exercise folder
```bash
mkdir isolation-check-exercise
cd isolation-check-exercise
```

Save the following code as `prototype.py`. It uses only the Python standard library.

## Step 2 — Understand the snapshot format

`scope_id` is an explicit user assertion that both snapshots refer to one engine scope. The prototype does not verify that assertion, inspect Docker, or establish physical volume identity.

A snapshot name must be the normalized engine-facing volume name, not an arbitrary YAML key. The production collector will be responsible for deriving that name.

Save this as `left.json`:
```json
{
  "schema_version": "demo-v1",
  "scope_id": "local-demo",
  "volumes": [
    {"name": "shared_pgdata", "read_only": false}
  ]
}
```

Save this as `right.json`:
```json
{
  "schema_version": "demo-v1",
  "scope_id": "local-demo",
  "volumes": [
    {"name": "shared_pgdata", "read_only": false}
  ]
}
```

These fixtures contain no secrets and do not require a running database.

## Step 3 — Teaching implementation
```py
"""Teaching prototype: compare declared, normalized named-volume snapshots only."""
import argparse
import json
from pathlib import Path

LIMIT = 1_048_576


def load_snapshot(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("snapshot exceeds size limit")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema_version") != "demo-v1":
        raise ValueError("unsupported snapshot schema")
    scope = data.get("scope_id")
    if scope is not None and (not isinstance(scope, str) or not scope or len(scope) > 128):
        raise ValueError("invalid declared scope")
    volumes = data.get("volumes")
    if not isinstance(volumes, list) or len(volumes) > 128:
        raise ValueError("invalid volume list")
    for volume in volumes:
        if not isinstance(volume, dict):
            raise ValueError("invalid volume entry")
        name = volume.get("name")
        if not isinstance(name, str) or not name or len(name) > 256:
            raise ValueError("invalid volume name")
        if type(volume.get("read_only")) is not bool:
            raise ValueError("read_only must be a boolean")
    return data


def compare(left, right):
    checks = []
    scope = left.get("scope_id")
    if scope is None or right.get("scope_id") is None or scope != right["scope_id"]:
        checks.append({"rule_id": "VOL001", "status": "unknown",
                       "message": "No matching declared engine scope; names alone are insufficient."})
    else:
        shared = False
        for a in left["volumes"]:
            for b in right["volumes"]:
                if a["name"] != b["name"]:
                    continue
                shared = True
                writable = not a["read_only"] or not b["read_only"]
                checks.append({"rule_id": "VOL001",
                               "status": "fail" if writable else "warn",
                               "message": "Shared configured named volume in declared scope.",
                               "volume": a["name"], "basis": "user-declared scope",
                               "writable_by_either": writable})
        if not shared:
            checks.append({"rule_id": "VOL001", "status": "pass",
                           "message": "No equal named-volume references in the supplied declared scope."})
    checks.append({"rule_id": "COVERAGE", "status": "unknown",
                   "message": "Ports, project names, bind mounts, databases and runtime identity are not checked."})
    return {"schema_version": "demo-report-v1", "complete": False, "checks": checks}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = compare(load_snapshot(args.left), load_snapshot(args.right))
    except (OSError, ValueError, RecursionError):
        print(json.dumps({"schema_version": "demo-report-v1", "complete": False,
                          "error": "Unable to read or validate snapshot; raw input omitted."}))
        return 2
    print(json.dumps(report, indent=2))
    if any(check["status"] == "fail" for check in report["checks"]):
        return 1
    if args.strict and any(check["status"] == "unknown" for check in report["checks"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## Step 4 — Run the exercise
```bash
python3 prototype.py left.json right.json
# Expected exit code: 1; shared configured writable named volume.
```

Change only the right-hand volume name to `payments_pgdata` and run again:
```bash
python3 prototype.py left.json right.json
# Expected exit code: 0, but complete remains false.

python3 prototype.py left.json right.json --strict
# Expected exit code: 2, because unimplemented coverage remains UNKNOWN.
```

The non-strict exit code 0 does not mean full isolation. Read the coverage check. Changing the name is only an educational input edit, not an automatic migration of existing data or proof of a safe real-world fix.

## Step 5 — Save and run the tests

Save the following as `test_prototype.py` in the same folder:
```py
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
```
```bash
python3 -m unittest -v
```

The eight tests cover shared writable volumes, distinct names, different/absent scopes, read-only sharing, one writable side, exit behavior, and rejection of string booleans.

## Step 6 — Add a real offline Compose fixture

The next input below is a deliberately risky **configuration example**. Do not start two PostgreSQL containers against the same data directory. No runtime is necessary to inspect this configuration.

Save it as `compose.yaml` in each of two disposable fixture directories, changing the top-level project name in the second copy:
```yaml
name: feature_login
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: app_dev
      POSTGRES_PASSWORD: demo-only-not-a-real-secret
    volumes:
      - db-data:/var/lib/postgresql/data
volumes:
  db-data:
    external: true
    name: shared_pgdata
```

Both copies still refer to `shared_pgdata`, despite different project names. The image tag is only an example; choose and pin a maintained, tested image for any future runtime integration tests.

On a trusted Linux development machine with the Compose plugin installed, inspect the model without starting services:
```bash
docker compose --project-directory ./fixture-a -f ./fixture-a/compose.yaml config --format json
```

This manual command prints resolved configuration, including its example password. Use only sanitized fixtures. The production collector must capture and redact values instead of dumping raw output.

## Step 7 — Extend in this order

1. Parse Compose canonical JSON into the normalized volume model.
2. Track engine-scope assumptions and mount provenance.
3. Replace the teaching snapshots with collector output.
4. Implement VOL001 unit tests against real sanitized Compose fixtures.
5. Add project names and fixed host ports.
6. Add explicit supported PostgreSQL targets.
7. Add redacted text output, versioned JSON, and the documented CLI.
8. Add integration tests, security review, and packaging only after the core rules work.

## Prototype limitations

It has no discovery, Compose parser, network namespace resolver, bind-mount logic, live observation, exceptions, or full reporting schema. It trusts declared snapshot scope, accepts only its teaching schema, and always marks broader coverage incomplete. It is not designed to process hostile input as a security boundary.
