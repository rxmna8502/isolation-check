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
