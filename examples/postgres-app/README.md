# Minimal PostgreSQL Application Example

This example demonstrates how `isolation-check` creates and manages isolated development environments for a single application service paired with a PostgreSQL database.

> **Development-Only Notice:** This setup contains development credentials (`devpassword`) and is designed exclusively for testing local isolation boundaries. Never use these credentials or configurations in production.
>
> **Network / Image Requirement:** Starting containers via `isolation-check up` requires internet access on first run to download the base Docker images (`node:20-alpine` and `postgres:16-alpine`), unless they are already cached in your local Docker engine. This example is not fully offline.

---

## Files in This Example

- `docker-compose.yaml`: Base development Compose file declaring a `web` application service and a `db` PostgreSQL service.
- `isolation-check.toml`: Declarative configuration specifying the service names, database URL environment variable, and application container target port (`3000`).

---

## Step-by-Step Walkthrough

### 1. Initialize a Local Git Repository
`isolation-check` operates on Git repositories and worktrees:

```bash
git init
git add .
git commit -m "Initial commit for example"
```

### 2. Preview Environment Creation (`--dry-run`)
Validate configuration and view the proposed plan without making changes:

```bash
isolation-check create feature-payments --dry-run
```

### 3. Create Two Parallel Environments
Create two separate Git worktrees with dedicated PostgreSQL volumes and loopback overrides:

```bash
# Create first environment
isolation-check create feature-payments

# Create second environment
isolation-check create feature-analytics
```

### 4. Verify Configuration Isolation
Audit both environments before starting containers to confirm there are no shared writable volumes or host-port conflicts:

```bash
isolation-check check ../feature-payments ../feature-analytics
```

Both environments pass `VOL001` and `PORT001` checks because `isolation-check` created dedicated volume mounts (`feature-payments_pgdata` and `feature-analytics_pgdata`) and removed fixed host port bindings.

### 5. Start the First Environment
Start the Compose project for `feature-payments`:

```bash
isolation-check up feature-payments
```

Output:
- Binds the `web` container target port (`3000`) to a dynamic host port on loopback `127.0.0.1` (e.g. `http://127.0.0.1:10001`).
- Keeps the PostgreSQL database unpublished on the host (`ports: !reset []`).
- Records transient runtime state in `../feature-payments/.isolation-check/runtime-state.json`.

### 6. Inspect Environment Status
Query live container states and health without modifying anything (read-only):

```bash
isolation-check status feature-payments
```

### 7. Stop the Environment Without Losing Data
Stop the containers and network while strictly preserving PostgreSQL data volumes and code:

```bash
isolation-check down feature-payments
```
