# isolation-check — Project Handbook

Documentation and a limited teaching prototype for a proposed open-source development-environment configuration auditor.

## Project status

**Design-stage project, not a released application.** Broader CLI commands and interfaces in the chapters are proposals. The included Python example compares normalized named-volume snapshots only: it does not discover worktrees, collect Docker configuration, connect to databases, or prove complete isolation.

## Study guide

1. [01 — Foundations & the Problem](docs/01-foundations-and-problem.md)
2. [02 — Scope, Guarantees & Safety Model](docs/02-scope-and-safety.md)
3. [03 — Architecture & Repository Setup](docs/03-architecture-and-setup.md)
4. [04 — Detection Rules & Resource Identity](docs/04-detection-rules.md)
5. [05 — Build Walkthrough & Tested Teaching Prototype](docs/05-build-walkthrough.md)
6. [06 — CLI, Configuration & Report Contract](docs/06-cli-and-report-contract.md)
7. [07 — Testing, Security & Release Acceptance](docs/07-testing-and-security.md)
8. [08 — Roadmap, Collaboration & Research](docs/08-roadmap-and-research.md)

Start with chapters 1–4, then complete chapter 5.

## Run the included teaching example

Python 3.11+ recommended. No third-party Python dependencies, Docker, database, or network access required.

```bash
cd prototype
python3 -m unittest -v
python3 prototype.py fixtures/left.json fixtures/right.json
```

The last command intentionally returns **exit 1** because both inputs declare the same writable named volume in the same user-declared scope.

```bash
python3 prototype.py fixtures/left.json fixtures/different-volume.json
# Exit 0, but the report remains incomplete.

python3 prototype.py fixtures/left.json fixtures/different-volume.json --strict
# Exit 2: broader checks remain UNKNOWN.
```

Eight unit tests passed when the ZIP was prepared. That result applies only to the teaching example, not the planned full application.

## Contents

- `docs/`: eight Markdown study chapters with references.
- `prototype/prototype.py`: limited offline implementation.
- `prototype/test_prototype.py`: eight unit tests.
- `prototype/fixtures/`: three synthetic, credential-free sample inputs.

Open the Markdown files in VS Code, another Markdown reader, or import them into Notion.

## Important limits

- `scope_id` is a user assertion, not a verified physical engine identity.
- Matching references show configured sharing under stated assumptions.
- Different names do not prove independent backing storage.
- Exit 0 does not mean complete isolation; inspect UNKNOWN and coverage.
- Never start multiple PostgreSQL servers against a shared data directory.
- A production repository, registered package name, license and release have not been established by these documents.
