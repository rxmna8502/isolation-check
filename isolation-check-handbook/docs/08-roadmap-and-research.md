# 08 — Roadmap, Collaboration & Research

## Build order, not a promised deadline

| Milestone | Deliverable | Completion evidence |
| --- | --- | --- |
| M0: Validate | Talk to developers using parallel worktrees; collect sanitized failure examples | At least a few concrete reproducible cases, not just likes |
| M1: Offline core | Normalized model, named-volume rule, JSON and tests | Chapter 5 exercise extended into project modules |
| M2: Collection | Git discovery and controlled Compose rendering | Real fixture normalization with provenance and redaction |
| M3: Rules | Project, fixed published ports, volume/bind and supported Postgres comparisons | Positive, negative and UNKNOWN tests per rule |
| M4: Usability | CLI/config, exceptions, reports and installable package | Clean-environment install and documented exit behavior |
| M5: Alpha | Versioned tag, release notes, limitations and real demo | Reproducible demo and passing release checklist |

Do not promise dates before measuring available time and implementation complexity.


## Collaborating with friends

Split by clear interfaces, not by “everyone edits everything.”

- **Collector owner:** Git/Compose invocation, environment selection, provenance, limits.
- **Rule owner:** normalized identities, comparisons and uncertainty semantics.
- **Testing/security owner:** fixtures, secret redaction, subprocess failure cases.
- **Documentation/release owner:** runnable examples, installation, CLI contract and release checklist.

For a two-person team, combine collection with CLI, and rules with tests; review each other's work. No contributor should be described publicly as a teammate before agreeing to join.

## Suggested first issues

1. Document the initial Linux-only support matrix.
2. Define normalized environment/resource dataclasses.
3. Parse NUL-delimited Git worktree records.
4. Collect canonical Compose JSON with explicit options and bounded output.
5. Implement mounted named-volume identity and VOL001.
6. Implement project-name comparison and PROJ001.
7. Implement fixed IPv4 host binding comparison and PORT001.
8. Add explicit supported PostgreSQL URI parsing and PG001.
9. Add text/JSON renderers and exit-code tests.
10. Add secret-sentinel tests and redaction review.
11. Add narrow exception policy and validation.
12. Create a real, configuration-only demo and alpha release checklist.

Each issue should include scope, out-of-scope cases, fixture inputs, expected findings, and acceptance tests. Label beginner issues only when the task is actually bounded and documented.

## What `CONTRIBUTING.md` should explain

How to set up the supported environment; run unit/integration tests; submit sanitized reproductions; add a rule; preserve JSON compatibility; propose exceptions; and review privacy/security implications.

A new rule needs a documented identity model, at least one positive/negative/UNKNOWN fixture, a safe remediation message, and no raw secret output.

## Release materials

- README: one-sentence purpose, current status, actual demo, tested prerequisites, installation, examples and limits.
- `SECURITY.md`: no security certification claim; private reporting route only after one is actually established.
- LICENSE: choose explicitly; MIT is a simple candidate, not an already selected license.
- `CHANGELOG.md`: distinguish implemented, experimental and breaking changes.
- Release notes: scope, exact tested versions, known false-positive/negative classes and JSON schema version.
- Demo: show a sanitized shared-volume configuration, the checker result, and a limitation case. Do not demonstrate real data destruction.

## Research: what is evidenced versus hypothesized

**Evidenced problem:** developers describe parallel worktree port conflicts and shared database/migration problems. These reports motivate a check but do not establish market size.[\[1\]](https://dev.to/mashrulhaque/git-worktrees-for-ai-coding-run-multiple-agents-in-parallel-3pgb)[\[2\]](https://dev.to/andrea_schiona/running-coding-agents-in-parallel-with-git-worktrees-4cnk)

**Existing solutions overlap:** BranchBox advertises per-feature containers, networks, databases and ports. Isola advertises per-worktree service/database isolation. Worktrunk offers lifecycle hooks and branch-derived ports.[\[3\]](https://github.com/branchbox/branchbox)[\[4\]](https://github.com/cyucelen/isola)[\[5\]](https://worktrunk.dev/hook/)

**Project hypothesis:** a lightweight independent checker could be useful alongside those managers and ad-hoc setups. This niche has not been established as unique or unoccupied. This documentation is not a patent, trademark or exhaustive competitive review.

## Alternatives considered

**Devcontainer connectivity diagnostics:** a Dev Containers CLI issue describes missing host-integration behavior, including a WSL/Neovim user's forwarding problem. However, devcontainer-bridge already implements forwarding, browser opening and Unix socket bridging. A new project must not present that functionality as novel.[\[6\]](https://github.com/devcontainers/cli/issues/1308)[\[7\]](https://github.com/bradleybeddoes/devcontainer-bridge)

**Docker cache diagnosis:** whycache already documents explaining cache misses, changed files and rebuild costs. It is a reason not to blindly rebuild the same concept.[\[8\]](https://github.com/allaboutaryan/whycache)

These alternatives explain the recommendation; they are not part of isolation-check's scope.

## Questions to ask potential users

1. How many local branches/stacks do you run concurrently?
2. Which resources are intentionally shared?
3. Have migrations, tests, volumes or ports interfered across branches?
4. How do you select env files, profiles and project names today?
5. Would you run a read-only preflight check? Where: locally, pre-test, or CI?
6. What information must never appear in a shareable report?
7. Can you provide a sanitized configuration reproducing the problem?

If users already solve the problem reliably with their environment manager, narrow or reconsider the project rather than adding unrelated features.

## Primary technical reading

- Git worktree machine-readable listing: [official Git documentation](https://git-scm.com/docs/git-worktree).
- Compose canonical model rendering: [docker compose config](https://docs.docker.com/reference/cli/docker/compose/config/).
- Project naming/precedence: [Compose project names](https://docs.docker.com/compose/how-tos/project-name/).
- Named volumes and explicit names: [Compose volume reference](https://docs.docker.com/reference/compose-file/volumes/).
- Published ports, environments and mounts: [Compose services reference](https://docs.docker.com/reference/compose-file/services/).
- PostgreSQL URI and connection semantics: [libpq connection parameters](https://www.postgresql.org/docs/current/libpq-connect.html).

## Final study assignment

Without running a database, build two sanitized Compose models that share a custom writable volume. Show that your checker flags the configured sharing even when project names differ. Then change to distinct project-scoped volumes, add an unsupported database target, and verify the report stays incomplete rather than claiming total isolation.
