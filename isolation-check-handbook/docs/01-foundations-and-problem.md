# 01 — Foundations & the Problem

## Start here

**Learning goal:** explain why separate source-code checkouts can still interfere with each other, and identify which resources the first checker should inspect.

This is a configuration-auditing project, not a Wi-Fi tool, environment manager, or sandbox. It is independent of any other project.

## 1. Git branch versus worktree

A branch identifies a line of development. A working tree is the directory containing checked-out files. Git worktrees let one repository have multiple checked-out directories, often on different branches.

Git provides a machine-readable list through `git worktree list --porcelain -z`; the NUL-separated format handles unusual path characters more reliably than splitting human-readable lines.[\[1\]](https://git-scm.com/docs/git-worktree)
```txt
repository/
  main-checkout/
  feature-login/
  feature-payments/
```

These directories separate code. They do not automatically give each app its own database server, Docker volume, port, or credentials.

## 2. Processes and ports

A process is a running program. A TCP listener belongs to a network namespace and listens on an address and port. `127.0.0.1:3000` on the host is not the same address space as `127.0.0.1:3000` inside a container.

For Compose port mappings, distinguish:

- **Container target port:** where the app listens inside the container.
- **Published host port:** where the host accepts connections.
- **Protocol:** TCP and UDP are different namespaces.
- **Bind address:** loopback, a specific interface, or a wildcard.

Two different containers can both listen on internal port 3000. That is not automatically a collision. Two services requesting the same overlapping host binding may be a collision. Compose also allows dynamically allocated published ports, which cannot be treated as fixed ports in an offline scan.[\[2\]](https://docs.docker.com/reference/compose-file/services/)

## 3. Docker and Compose

Docker runs containers. Compose describes services, networks, mounts, and configuration for an application stack.

A Compose project name groups resources. Its precedence is: explicit `-p`, then `COMPOSE_PROJECT_NAME`, then top-level `name:`, then directory-based defaults. A checker must inspect the effective configuration rather than assume the directory name is always the project name.[\[3\]](https://docs.docker.com/compose/how-tos/project-name/)

## 4. Storage: three different things

| Type | Meaning | Why the checker cares |
| --- | --- | --- |
| Container writable layer | Files stored in the container's own writable layer | Not a substitute for understanding persistent mounts |
| Named volume | Persistent storage managed through the engine | Two environments can reference the same resolved volume name |
| Bind mount | A host path mounted into a container | Different worktrees can both mount the same writable host directory |

Compose volume `name:` is used as-is, without project scoping. External volumes are managed outside that Compose application. Therefore, changing only the project name does not necessarily separate storage.[\[4\]](https://docs.docker.com/reference/compose-file/volumes/)


## 5. PostgreSQL identity

A database target is not just a database name. At minimum, reasoning about it requires the server endpoint, network namespace, port, and database name.

`app_dev` on server A and `app_dev` on server B are not necessarily the same resource. Different users connecting to the same server/database do not automatically receive separate data.

PostgreSQL URIs allow percent-encoding, IPv6, multiple hosts, optional fields, and Unix sockets. The first release should support a clearly defined subset and return UNKNOWN for the rest.[\[5\]](https://www.postgresql.org/docs/current/libpq-connect.html)

## 6. The failure scenario

1. A developer creates two worktrees.
2. Both stacks use an external volume named `shared_pgdata`, or both apps target the same existing database.
3. One branch runs a migration or destructive test setup.
4. The other branch now sees unexpected schema or data.
5. Source files were separate, but mutable state was not.

The checker aims to catch suspicious references before step 3. It does not need to reproduce data loss to demonstrate value.

## 7. Who might use this

- Developers comparing two local feature environments.
- Teams running parallel integration-test stacks on the same machine.
- Developers supervising multiple coding agents.
- Maintainers reviewing a reproducible environment bug report.

This is not initially a tool for every developer or every infrastructure stack. Validate demand in this specific audience first.

## Study exercise

For each pair, decide whether the answer is SAME, DIFFERENT, or UNKNOWN:

1. Two containers both listen internally on 5432.
2. Two stacks on one local Docker engine mount `shared_pgdata` writable.
3. Two connection strings contain `localhost`, but run in different containers.
4. Two worktrees mount the same host directory read-only.

**Answers:** 1 unknown without endpoint context; 2 same configured resource under the stated engine scope; 3 not enough information and often distinct endpoints; 4 shared reference, but not the same write-collision risk as writable sharing.
