# 07 — Testing, Security & Release Acceptance

## Test strategy

Treat the rule engine as a pure function over normalized inputs. Most tests should not need Git, Docker, an internet connection, or PostgreSQL.

Separate **unit tests**, **fixture-based collector tests**, and **explicitly opt-in runtime integration tests**. The eight teaching tests in chapter 5 are only the first tiny layer.

## Required test matrix

| Area | Positive case | Negative case | Uncertainty/error case |
| --- | --- | --- | --- |
| Project | Same resolved project in declared same scope | Different resolved names | Unknown context/invocation |
| Ports | Same fixed TCP bind or IPv4 wildcard overlap | Different published ports; TCP vs UDP | Dynamic publish, IPv6 ambiguity, host networking |
| Named volume | Same resolved mounted name, either writable | Project-scoped distinct names | Unknown driver/backend/scope |
| Bind mount | Same canonical writable host path | Distinct supported paths | Missing path, denied path, symlink escape |
| Postgres | Same explicit supported endpoint/namespace/database | Different database in supported comparison | Missing defaults, aliases, socket/multi-host URI |
| Profiles | Active shared-resource service | Unselected service does not participate | Unsupported effective service selection |
| Secrets | Sentinel absent from every output channel | Safe redacted provenance remains | Secret included in external command error |
| CLI | Stable JSON and expected code 1 | No FAIL yields documented code 0 | Strict UNKNOWN and fatal error yield 2 |

## High-value regression fixtures

- Worktree directory with spaces, Unicode, newline, or leading dash.
- Bare/detached/locked/prunable Git worktree records.
- Compose file overrides and explicit `-p` precedence.
- Empty environment value versus missing variable.
- Same YAML volume key but different resolved names.
- Same custom volume name with different project names.
- Unused top-level volume declaration.
- Shared volume where both mounts are read-only; then one side writable.
- Identical container ports with different published ports.
- `localhost` in different containers.
- PostgreSQL percent-encoded database name, IPv6 literal, multi-host URI, query override, and omitted port.
- Malformed/oversized JSON, long stderr, timeout, denied files and unavailable Compose.

## Secret-leak tests

Put a distinctive fake sentinel into each sensitive location: URI password, username, service environment variable, env-file content, stderr, query parameter and malformed input.

Assert the sentinel never appears in text, JSON, exceptions, debug logs or temporary retained files. Redaction must happen before data reaches a renderer or logger, not after serializing an entire environment.

Do not assume removing only the password is sufficient: internal hostnames, usernames, tokens, and query parameters may also be sensitive.

## Threat model

Protected assets: local source files, development data, credentials and private configuration.

Inputs that can be hostile: repository paths, Compose files, env files, symlinks, exception config, binary lookup paths, subprocess output and oversized data.

The checker is not an isolation boundary for arbitrary malicious code. It must not execute project scripts, source `.env` as shell code, use shell interpolation, evaluate config expressions itself, or allow project files to turn on privileged behavior.

## Collection controls

1. Use trusted Git/Compose executables and fixed argument templates.
2. Validate roots and explicit file selection; constrain symlink traversal.
3. Minimize inherited environment and document precedence.
4. Bound file size, subprocess output, processing time, resource count and number of comparisons.
5. Never request root or silently connect to a remote engine.
6. Never invoke `up`, `down`, `build`, `exec`, `prune`, database migrations, or destructive SQL in normal scans.
7. Leave raw resolved models in memory only; avoid writing secret-bearing data to disk.
8. Disable network probes by default. Runtime observation would be a separate future mode with explicit consent.

## Integration testing safely

For v0.1, real Compose rendering against sanitized fixtures can validate collection without running containers. Record OS, Python, Git, Compose version, selected environment inputs and expected result.

Future live tests must use disposable resources with an unmistakable test-only prefix, generated demo credentials, loopback-only publishing and bounded cleanup. Never use a developer's default database or run global prune commands. Never start two database servers sharing one data directory just to prove the checker can detect it.

## CI proposal

- Standard-library unit tests on every pull request.
- Deterministic normalized snapshots and JSON report fixtures.
- Secret-sentinel regression tests on every PR.
- Compose rendering integration job using a pinned, documented test version.
- Packaging installation smoke test and `--help` check.
- Optional platform jobs are experimental until their semantics pass the full matrix.
- Do not request production secrets or privileged Docker access for untrusted external PRs.

## v0.1 acceptance checklist

- [ ] All four MVP rule families are implemented or any reduced scope is explicitly renamed/documented.
- [ ] No positive finding relies solely on equal database names or internal container ports.
- [ ] UNKNOWN appears for unsupported/missing identity evidence.
- [ ] Profiles, merge order and explicit project names are covered.
- [ ] Secrets remain absent from every report/error/log test.
- [ ] Scan does not change project files or runtime state.
- [ ] Text and JSON contain scope, provenance, limitations and completeness.
- [ ] Exit codes match chapter 6, including strict mode and partial failures.
- [ ] Installation works in a clean supported environment.
- [ ] README lists the exact tested platform/version combinations.
- [ ] Name/license/dependency review and contribution/security policies are complete.
- [ ] Example output comes from the actual tool, not a fabricated screenshot.

## What counts as a successful release

A smaller honest release is better than a broad misleading one. If only named-volume checking is ready, publish it as a limited alpha with that exact scope—not as a complete environment isolation scanner.
