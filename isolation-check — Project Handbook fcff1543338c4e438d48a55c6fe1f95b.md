# isolation-check — Project Handbook

<aside>
🚧

Project status: design-stage open-source project. No repository, working release, benchmark, or implemented feature has been verified. Commands, interfaces, and release criteria below are proposed unless explicitly labeled as an executable teaching prototype.

</aside>

## What this project is

**isolation-check** is a proposed local command-line tool that compares development environments and flags accidental sharing of databases, Docker volumes, Compose project namespaces, and host ports.
Its purpose is to **audit configuration**, not create environments or guarantee security isolation. Separate source-code directories do not, by themselves, establish separate runtime resources.

## The first release in one sentence

Compare two explicitly selected local Docker Compose environments and explain configuration-level collisions, uncertainty, and safe next steps—without starting services or connecting to databases.

## How to study this handbook

Read the numbered chapters below in order. Each chapter stands on its own, so it can also be shared with a collaborator.

1. **Foundations & Problem** — learn Git worktrees, containers, networks, volumes, databases, and the real failure scenario.
2. **Scope, Guarantees & Safety** — understand what the tool will and will not claim.
3. **Architecture & Repository Setup** — set up the recommended Python implementation and understand the data flow.
4. **Detection Rules & Resource Identity** — study how collisions are identified without misleading comparisons.
5. **Build Walkthrough & Teaching Prototype** — implement the first offline check, then extend it toward the MVP.
6. **CLI, Configuration & Report Contract** — define predictable commands, policy, JSON, and exit behavior.
7. **Testing, Security & Release Acceptance** — test edge cases, protect credentials, and decide when a release is honest.
8. **Roadmap, Collaboration & Research** — organize work, check alternatives, validate demand, and prepare an early release.

## Decisions made for this proposal

| Area | Proposed decision |
| --- | --- |
| Delivery | Local CLI; no website, hosted backend, account, or AI dependency |
| Implementation | Python 3.11+; standard library first |
| Initial platform | Linux, local Docker Engine, Docker Compose v2 |
| MVP resources | Compose project names, host port bindings, volumes/bind mounts, explicit PostgreSQL targets |
| Default permissions | Configuration inspection only; no live database probes |
| Output | Human-readable text and versioned JSON |
| Core principle | Unknown is a valid result; no global “fully isolated” verdict |

These are engineering recommendations, not decisions already approved by a team. macOS, WSL, Windows, remote Docker contexts, Redis, Kubernetes, and automatic fixes are not part of the first release.

## First milestone

Create two disposable worktree configurations that reference the same external database volume. Run the checker offline. It should identify the shared configured resource, explain why it matters, avoid printing secrets, and leave the environments unchanged.

## Before coding

- [ ]  Confirm the project/repository/package name is available.
- [ ]  Agree on the initial platform and explicit MVP boundaries.
- [ ]  Read chapters 1–4 before expanding the implementation.
- [ ]  Build the offline teaching prototype in chapter 5.
- [ ]  Validate the problem with developers who actually use worktrees or parallel local stacks.
- [ ]  Do not describe an announcement or prototype as a released security tool.

## Chapters

The individual study chapters are listed below.

[01 — Foundations & the Problem](https://app.notion.com/p/01-Foundations-the-Problem-3271a9f7b97b4ee7b09dd5fe32f3606f?pvs=21)

[02 — Scope, Guarantees & Safety Model](https://app.notion.com/p/02-Scope-Guarantees-Safety-Model-15ba4495bf394c93bbaeed1d98cda8ac?pvs=21)

[03 — Architecture & Repository Setup](https://app.notion.com/p/03-Architecture-Repository-Setup-cb9f285d74024336936f025889801ce8?pvs=21)

[04 — Detection Rules & Resource Identity](https://app.notion.com/p/04-Detection-Rules-Resource-Identity-aa03bc891a724858817c6dbce97b2368?pvs=21)

[05 — Build Walkthrough & Tested Teaching Prototype](https://app.notion.com/p/05-Build-Walkthrough-Tested-Teaching-Prototype-fe7731d1cd1c4c499e8064b24d87e00e?pvs=21)

[06 — CLI, Configuration & Report Contract](https://app.notion.com/p/06-CLI-Configuration-Report-Contract-c0250bf772724bb8b705a229715af410?pvs=21)

[07 — Testing, Security & Release Acceptance](https://app.notion.com/p/07-Testing-Security-Release-Acceptance-a62c3fcf413043539c9d81b652b0a2dc?pvs=21)

[08 — Roadmap, Collaboration & Research](https://app.notion.com/p/08-Roadmap-Collaboration-Research-2f47a99bbbf94e4d813048865e9f647b?pvs=21)