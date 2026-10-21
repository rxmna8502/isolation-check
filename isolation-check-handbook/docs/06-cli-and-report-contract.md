# 06 — CLI, Configuration & Report Contract

## Status of this interface

Everything in this chapter is a **proposed v0.1 contract**. The small `prototype.py` exercise has a different, explicitly limited interface. Do not publish these commands as installable until they are implemented and tested.

## CLI commands
```bash
# Discover worktree paths; print metadata, not secrets.
isolation-check discover --repo /path/to/repository

# Compare two explicit environments using supported default Compose discovery.
isolation-check check /path/to/feature-a /path/to/feature-b

# Machine-readable output.
isolation-check check /path/to/feature-a /path/to/feature-b --format json

# Treat incomplete/unknown selected checks as unsuccessful when there is no FAIL.
isolation-check check /path/to/feature-a /path/to/feature-b --strict

# Use a reviewed local configuration file.
isolation-check check --config ./isolation-check.toml
```

For the first CLI iteration, require one explicit `--config` when file lists, env files, profiles, or context differ. Do not add many asymmetric command-line flags until those cases are well tested.

`discover` does not automatically scan every discovered directory. Comparing more than two environments is a later extension; pairwise reasoning should work first.

## Proposed TOML input
```toml
schema_version = 1

[[environments]]
label = "login"
root = "../feature-login"
compose_files = ["compose.yaml", "compose.dev.yaml"]
env_files = [".env.local"]
project_name = "login_dev"
profiles = ["dev"]
docker_context = "default"
postgres_service = "api"
postgres_variable = "DATABASE_URL"

[[environments]]
label = "payments"
root = "../feature-payments"
compose_files = ["compose.yaml", "compose.dev.yaml"]
env_files = [".env.local"]
project_name = "payments_dev"
profiles = ["dev"]
docker_context = "default"
postgres_service = "api"
postgres_variable = "DATABASE_URL"

[[exceptions]]
rule_id = "VOL001"
environments = ["login", "payments"]
resource = "shared_dependency_cache"
reason = "Intentional local cache sharing; reviewed by maintainers"
```

Semantics:

- Environment roots are relative to the checker config file; child file paths are relative to their environment root.
- Compose files retain their declared order, because merging order matters.
- The project override maps to Compose `-p`; ordered environment files and profiles map to supported Compose options.
- A service environment variable is an explicit extraction source, not a request to search all files.
- `docker_context` selects the intended context; remote contexts are rejected or UNKNOWN in v0.1. Equal context names do not prove physical runtime identity.
- No raw connection strings or passwords belong in checker config, CLI arguments, committed fixtures, or reports.
- Exceptions require a recognized rule, exact pair, canonical resource reference and non-empty reason. Unknown rules and ambiguous exceptions are errors.

This file configures only the checker. It does not set up the app's database, change Compose files, or make a shared cache safe.

## Terminal report example
```txt
isolation-check — CONFIGURATION AUDIT
Scope: declared local engine; no live resource verification

FAIL  VOL001  Shared configured writable named volume: shared_pgdata
      left:  db -> db-data
      right: db -> db-data
      next: review storage separation before running either stack

WARN  PG001   Same explicit configured database target
      target: [redacted endpoint alias] / app_dev

UNKNOWN PORT001  Published port is dynamically allocated

Summary: 1 fail, 1 warn, 1 unknown
Coverage: incomplete — not a full isolation guarantee
```

Label example reports as examples in the README until they come from actual executed tooling.

## Versioned JSON report
```json
{
  "schema_version": "1.0",
  "tool_version": "0.1.0a1",
  "mode": "configuration-only",
  "complete": false,
  "environments": ["login", "payments"],
  "summary": {"fail": 1, "warn": 0, "pass": 0, "unknown": 1, "ignored": 0},
  "findings": [
    {
      "rule_id": "VOL001",
      "status": "fail",
      "severity": "high",
      "confidence": "high",
      "basis": "same configured resource within declared local engine scope",
      "resource_ref": "volume-1",
      "environments": ["login", "payments"],
      "evidence": [
        {"environment": "login", "source_ref": "services.db.volumes[0]"},
        {"environment": "payments", "source_ref": "services.db.volumes[0]"}
      ],
      "message": "Both environments configure writable access to the same named volume.",
      "recommendation": "Review intended sharing and separate storage if necessary; do not delete existing volumes."
    },
    {
      "rule_id": "COVERAGE",
      "status": "unknown",
      "severity": "info",
      "confidence": "high",
      "message": "A selected identity could not be resolved from supported configuration."
    }
  ]
}
```

Version the schema independently of the tool. Freeze required fields before release, document optional fields, reject unsupported major versions in consumers, and test output against a schema fixture.

## Exit-code contract

| Code | Meaning |
| --- | --- |
| 0 | Scan completed; no unignored FAIL. WARN/UNKNOWN may remain in non-strict mode |
| 1 | Scan completed and at least one unignored FAIL exists |
| 2 | Invalid input, fatal collection/processing error, or strict-mode UNKNOWN without a FAIL |
| 130 | User interruption on the supported platform |

Precedence: fatal processing errors return 2; otherwise FAIL returns 1; otherwise strict UNKNOWN returns 2; otherwise return 0. A failed optional rule is UNKNOWN unless it makes the report fundamentally unreliable. Ignored findings are counted separately. An ignored unknown does not magically establish complete evidence.


`complete` concerns evidence availability, not whether findings are favorable. A complete report can contain FAILs. Partial reports must say they are partial.


## Output and privacy rules

- JSON stdout must contain exactly one valid report; progress goes to stderr.
- Errors are structured and sanitized; never echo raw Compose stderr or database URIs.
- Use deterministic finding order so reports are diffable.
- Redact usernames, passwords, tokens, query parameters and sensitive host/path information by default.
- Use report-local resource aliases instead of unsalted secret-derived hashes.
- No persistent raw snapshots, uploads, telemetry or auto-written report files by default.
- A future explicit local-details mode must still never reveal credentials.
