# Coding Agent Integration Guide

This guide explains how AI coding assistants and autonomous agents may safely interact with `isolation-check`.

> **Source of Truth:** The `isolation-check` CLI executable is the primary source of truth. Do not build custom wrappers that bypass CLI validations or reimplement internal state parsing.

---

## Safety Guardrails for Autonomous Agents

When delegating environment preparation or verification to an AI agent, enforce the following constraints:

1. **Always Run `create --dry-run` First:**
   Before creating worktrees or branches on disk, run:
   ```bash
   isolation-check create <name> --dry-run
   ```
   Inspect the calculated plan (project name, volume, paths, container port) before proceeding.

2. **Require Explicit Repository Mappings:**
   Never guess or dynamically construct database names, service names, or connection variables from random files. Require explicit parameters in `isolation-check.toml` or CLI arguments.

3. **Never Bypass Ownership Validation:**
   `isolation-check` cross-checks repository records, worktree manifests, and Git worktree registrations. Never attempt to manipulate containers directly via raw `docker compose` or `docker` commands without passing through `isolation-check up/status/down`.

4. **Never Use Destructive Cleanup Without Human Confirmation:**
   Destructive resource removal (`destroy`) is intentionally omitted from this alpha milestone. Agents must never execute `docker volume rm` or pass `-v` to `docker compose down` on managed database volumes.

5. **Treat `UNKNOWN` as Unresolved:**
   If `isolation-check check` reports an `UNKNOWN` status (e.g. for unevaluated bind mounts or runtime state), do not claim the environments are isolated. Report the condition honestly.

6. **Never Print or Copy Private Environment Files:**
   The file `.isolation-check/isolation.env` contains generated local credentials (mode `0600`). Agents must never read, print, or copy this file into logs, transcripts, prompts, or version control.

7. **Never Claim Complete Isolation:**
   Always use precise language: `isolation-check creates and verifies supported isolation boundaries for local Git worktree environments.` Never claim "universal" or "complete" environment isolation.

8. **Use `status` Before Controlling an Environment:**
   Before invoking `up` or `down`, query environment state using:
   ```bash
   isolation-check status <environment> --json
   ```
   Inspect the returned state (`RUNNING`, `STOPPED`, `PARTIAL`, `HEALTHY`) to avoid redundant operations.

---

## Recommended Agent Workflow

```
[Agent Task: Prepare Worktree for Feature]
                  │
                  ▼
   isolation-check create <env> --dry-run
                  │
          (Validate Plan)
                  │
                  ▼
      isolation-check create <env>
                  │
                  ▼
 isolation-check check <base_wt> <new_wt>
                  │
          (Verify Exit 0)
                  │
                  ▼
        isolation-check up <env>
                  │
      (Read Loopback URL)
                  │
                  ▼
      Run Developer Tests / Tasks
                  │
                  ▼
       isolation-check down <env>
          (Preserves Volume)
```
