# 02 — Scope, Guarantees & Safety Model

## The product promise

**Report configured resource sharing and collision risks with evidence, while making uncertainty visible.**

Do not promise: “Your environments are completely isolated,” “Your agents are safe,” or “No migration can damage another branch.” A configuration scan cannot establish those claims.

## Current status versus planned status

| Stage | Meaning |
| --- | --- |
| Current project | Design-stage; no production repository or release verified |
| Teaching prototype | A small offline named-volume comparison example in chapter 5; tested separately |
| Proposed MVP | Compose configuration collection plus four rule families |
| Later research | Runtime inspection, aliases, other platforms, more services |

The teaching prototype is not the MVP and is not a sandbox.


## MVP boundary

**Included:**

- Explicitly selected pairs of local development environments.
- Git worktree discovery, with explicit path selection still supported.
- Docker Compose v2 canonical configuration.
- Project namespace, published host ports, named volumes/bind mounts, and explicit PostgreSQL targets.
- Text/JSON reports and predictable exit codes.

**Excluded initially:**
- Starting, stopping, building, or modifying containers.
- Running migrations, test suites, shell hooks, or repository scripts.
- Database connections or live write probes.
- Arbitrary `.env` discovery across home directories.
- Redis, Kubernetes, remote Docker, macOS/WSL/Windows support claims.
- AI-generated diagnoses, automatic fixes, telemetry, and cloud uploads.

## Results have separate dimensions

| Field | Allowed values | Meaning |
| --- | --- | --- |
| Status | fail, warn, pass, unknown, ignored | Rule outcome, not a security certification |
| Severity | high, medium, low, info | Impact if the finding is relevant |
| Confidence | high, medium, low | Strength of evidence, with its basis |
| Evidence scope | configured, observed | Planned configuration versus inspected runtime |
| Completeness | complete or incomplete | Whether all selected checks could be evaluated |

A FAIL means a configured reference violates the chosen no-sharing policy; it does not claim damage has occurred. A PASS applies only to the individual rule and available evidence. Runtime evidence is not collected in v0.1.


## Configuration is not runtime

A saved Compose file may differ from the command used to launch containers. An application may override its database URL after startup. A variable may be obtained from a secret store or custom code.

Record the selected files, project override, profiles, environment sources, and invocation assumptions. When those inputs cannot be established, mark affected checks UNKNOWN.

## Scope and trust

In v0.1, inspect two environments intended for the same explicitly selected local engine context. A context name alone is not proof of the physical engine's identity. Offline results describe resource names under the declared context; they do not prove that resources exist or are currently mounted.

Do not equate two `localhost` strings without knowing where the processes run. Do not equate two named-volume strings across unknown engines. Do not infer that different names imply different underlying remote storage.

## Read-only means narrowly defined behavior

The tool may read explicitly selected files, run a fixed Git listing command, and invoke Compose configuration rendering with a controlled argument list. Rendering can expand environment values and read referenced files; its output is sensitive.

No root permission, Docker socket mount into an untrusted container, credential forwarding, project hook execution, or network probe is needed for the default workflow. Parsing does not make an untrusted repository harmless: use a trusted Compose executable, resource limits, and an appropriate sandbox for hostile input.

## Intentional sharing

Some projects deliberately share read-only fixtures or caches. An exception must identify the rule, environment pair, specific resource, and reason. It must remain visible as IGNORED, not silently become PASS. Ignoring a finding does not erase evidence or fix isolation.

## Success criteria

1. Detect known configured collisions in controlled fixtures.
2. Avoid common false positives from container-local ports and project-scoped names.
3. Show missing evidence rather than silently skipping it.
4. Never print database credentials or entire resolved environments.
5. Leave project files and runtime services unchanged.
6. Provide a report another developer can understand and reproduce safely.

## Study checkpoint

Explain why all of the following can be true: a scan exits 0; a volume rule passes; database inspection is UNKNOWN; and the report is incomplete. Chapter 6 defines that behavior explicitly.
