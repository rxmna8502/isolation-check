# 04 — Detection Rules & Resource Identity

## The central engineering problem

A useful checker is not a collection of string-equality tests. It needs to decide when two references mean the same resource—and when it lacks enough evidence.

Use a three-way relationship: **same configured identity, different within the supported model, or unknown**. Preserve the reason for every conclusion.

## Rule catalogue

| Rule | Condition under supported scope | Default result |
| --- | --- | --- |
| PROJ001 | Equal effective Compose project names in the same declared engine scope | WARN: namespace overlap |
| PORT001 | Overlapping fixed host bindings on the same known host scope | FAIL: configured binding conflict |
| VOL001 | Same resolved named volume, mounted by both environments, with either side writable | FAIL: configured shared mutable storage |
| BIND001 | Equal/overlapping canonical bind paths on the same supported host, with either side writable | FAIL or WARN depending on proven overlap |
| PG001 | Same explicit PostgreSQL endpoint, namespace and database | WARN: same configured target |
| COVERAGE | Missing/unsupported inputs affect a selected rule | UNKNOWN: explain missing evidence |

FAIL is a policy outcome based on configuration, not a claim that two running processes are already colliding.


## 1. Compose project names

Use the effective project name returned from canonical rendering under the selected invocation. Precedence includes `-p`, `COMPOSE_PROJECT_NAME`, and top-level `name:` before directory defaults.[\[1\]](https://docs.docker.com/compose/how-tos/project-name/)

Different directories with equal project names may target the same Compose namespace. Different names do not imply separate external volumes or databases. Same project name is a warning because a developer might intentionally be managing one stack from two directories.

## 2. Ports: compare published bindings, not internal targets

Represent a fixed binding as:
```txt
(host_scope, address_family, bind_address, published_port, protocol)
```

Also preserve wildcard/unspecified binding information. IPv4 `0.0.0.0` overlaps specific IPv4 addresses on the same host. IPv6 wildcard behavior can depend on platform and socket configuration; do not guess cross-family equivalence.

Supported first-release cases:

- Same fixed TCP host port and same specific host address: conflict.
- Same fixed port with an overlapping IPv4 wildcard: conflict.
- Same numeric port with TCP versus UDP: no conflict for that rule.
- Same container target port, different fixed published host ports: no host-port collision.
- Target-only publishing or dynamic port ranges: UNKNOWN until runtime allocation is known.
- `network_mode: host`, unsupported Swarm modes, or unknown IPv6 semantics: UNKNOWN, not a false PASS.

Compose documents host publishing, protocols, target-only allocation, and long-form published ranges. Internal `expose` is not host publishing.[\[2\]](https://docs.docker.com/reference/compose-file/services/)

## 3. Named volumes

Do not compare only the YAML key `db-data`. Compare the resolved engine-facing name, within a declared engine scope, for volumes actually mounted by selected services.

Examples:
```txt
A: project alpha, logical db-data -> alpha_db-data
B: project beta,  logical db-data -> beta_db-data
Result: no equal resolved names within this model

A: custom name shared_pgdata
B: custom name shared_pgdata
Result: configured sharing under the same declared engine scope
```

`name:` is not project-scoped. `external: true` references an existing platform-managed volume. Volume drivers can map different names to the same external backing store, so unfamiliar drivers/options must produce a limitation or UNKNOWN rather than proof of independence.[\[3\]](https://docs.docker.com/reference/compose-file/volumes/)

Access mode matters:

- Either side writable: shared mutable-state risk under default policy.
- Both read-only: WARN for shared reference, not a write-collision FAIL.
- Anonymous volumes or missing resolved names: UNKNOWN when identity cannot be established.

Container-wide `read_only` is not a substitute for each mount's access mode.

## 4. Bind mounts

Compare canonical host paths only on the supported local filesystem/host scope. Account for exact equality and parent/child overlap: `/data/app` and `/data/app/db` may share writes.

- Resolve existing symlinks where permitted; do not silently follow arbitrary repository links outside the selected roots.
- A missing path, denied path, remote daemon path, ambiguous case sensitivity, or unhandled mount namespace is UNKNOWN.
- Two paths with different spellings can still be the same underlying filesystem object.
- A read-only source-code mount is a different risk from a writable database directory.

For v0.1, it is acceptable to support exact canonical equality and report nested overlap as WARN with explicit limitations before implementing a complete overlap model.

## 5. PostgreSQL targets

Supported first-release input: one explicit TCP URI with an explicit hostname, numeric port and database, obtained from the selected service configuration or a declared input source.
```txt
(namespace, hostname_or_literal_ip, explicit_port, database_name)
```

Parse the `postgres://` and `postgresql://` schemes with a URI parser, validate, and percent-decode components appropriately. Hostnames can be case-normalized; database identifiers in a URI must not be blindly lowercased. Do not include passwords or users in the resource identity.

PostgreSQL supports optional defaults, multiple hosts, socket endpoints, and query parameters that can affect connection identity. The initial checker should return UNKNOWN for omitted identity components, multi-host URIs, keyword DSNs, socket paths, and unsupported identity-affecting query parameters instead of inventing defaults.[\[4\]](https://www.postgresql.org/docs/current/libpq-connect.html)

### Namespace examples

| Left | Right | Interpretation |
| --- | --- | --- |
| host process -> [localhost:5432/app](http://localhost:5432/app) | another host process -> [localhost:5432/app](http://localhost:5432/app) | Same configured target if host scope is established |
| container A -> [localhost:5432/app](http://localhost:5432/app) | container B -> [localhost:5432/app](http://localhost:5432/app) | Do not equate; container loopbacks differ |
| project A service `db`:5432/app | project B service `db`:5432/app | Service label alone is insufficient; networks may differ or be shared |
| db.internal:5432/app | 10.0.0.8:5432/app | Possible alias; UNKNOWN offline |

Different credentials do not prove different data. Different database names on the same server avoid this particular same-database finding, but cluster-level privileges and destructive administrative actions remain outside the guarantee.


## 6. Service selection and profiles

Only mounted/referenced resources of the selected environment should participate in active comparisons. Do not flag an unused top-level volume as if a service mounts it. Preserve selected profiles and explicit service selection. If effective active services cannot be determined for the tested Compose version, report that uncertainty.

## Rule pseudocode
```txt
for each selected environment pair:
    collect uncertainties
    for each supported resource kind:
        compare identities within their scopes
        if identity cannot be established: UNKNOWN
        if configured sharing violates policy: FAIL or WARN
        otherwise: rule-specific PASS
    apply narrow explicit exceptions as IGNORED
    never replace incomplete coverage with a global isolation PASS
```

## False-positive discipline

Every rule needs positive fixtures, negative fixtures, and uncertainty fixtures. A compelling demo is not enough. Build confidence by demonstrating that the tool refuses to overclaim on aliases, dynamic ports, unsupported drivers, and missing environment data.
