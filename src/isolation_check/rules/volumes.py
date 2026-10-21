"""Rule VOL001: Named volume collision detector.

Compares resolved engine-facing volume names mounted by services in two
development environments within an explicitly declared engine scope.
"""
from __future__ import annotations

from isolation_check.models import Finding, NormalizedEnvironment, VolumeMount


def check_volumes(
    left: NormalizedEnvironment,
    right: NormalizedEnvironment,
    include_coverage: bool = True,
) -> list[Finding]:
    """Evaluates rule VOL001 and COVERAGE on two normalized environments."""
    findings: list[Finding] = []

    # 1. Engine Scope Verification
    if not left.engine_scope.is_compatible_with(right.engine_scope):
        findings.append(
            Finding(
                rule_id="VOL001",
                status="unknown",
                severity="info",
                confidence="low",
                basis="unmatched or missing engine scope",
                message=(
                    "Cannot evaluate named volume collisions: engine scopes do not match or are missing. "
                    "Matching volume names alone across unknown or separate Docker engines cannot establish identity."
                ),
                environments=[left.label, right.label],
                recommendation="Explicitly declare matching engine scopes if both environments share the same local Docker engine.",
            )
        )
        if include_coverage:
            findings.append(_coverage_finding(left.label, right.label))
        return findings

    scope_id = left.engine_scope.scope_id or "unknown-scope"

    # 2. Check for anonymous or unresolved volume mounts (identity cannot be established)
    for env in (left, right):
        for mount in env.mounted_volumes:
            if mount.is_anonymous or mount.resolved_name is None:
                findings.append(
                    Finding(
                        rule_id="VOL001",
                        status="unknown",
                        severity="medium",
                        confidence="high",
                        basis="anonymous or unresolved volume mount",
                        message=(
                            f"Environment '{env.label}' service '{mount.service_name}' mounts an anonymous or "
                            f"unresolved volume at target '{mount.target}'. Physical storage identity cannot be established offline."
                        ),
                        resource_ref=None,
                        environments=[env.label],
                        evidence=[
                            {
                                "environment": env.label,
                                "service": mount.service_name,
                                "source_ref": mount.source_ref,
                                "target": mount.target,
                                "read_only": mount.read_only,
                            }
                        ],
                        recommendation="Use named volumes or explicit bind mounts if storage identity verification is required.",
                    )
                )

    # 3. Pairwise comparison of mounted volumes by resolved name
    left_by_name: dict[str, list[VolumeMount]] = {}
    for m in left.mounted_volumes:
        if m.resolved_name:
            left_by_name.setdefault(m.resolved_name, []).append(m)

    right_by_name: dict[str, list[VolumeMount]] = {}
    for m in right.mounted_volumes:
        if m.resolved_name:
            right_by_name.setdefault(m.resolved_name, []).append(m)

    common_names = sorted(set(left_by_name.keys()) & set(right_by_name.keys()))
    shared_found = False

    for name in common_names:
        shared_found = True
        l_mounts = left_by_name[name]
        r_mounts = right_by_name[name]

        # Is at least one mount writable on either side?
        at_least_one_writable = any(not m.read_only for m in l_mounts) or any(
            not m.read_only for m in r_mounts
        )

        evidence: list[dict] = []
        for m in l_mounts:
            evidence.append(
                {
                    "environment": left.label,
                    "service": m.service_name,
                    "source_ref": m.source_ref,
                    "logical_key": m.logical_key,
                    "resolved_name": m.resolved_name,
                    "target": m.target,
                    "read_only": m.read_only,
                }
            )
        for m in r_mounts:
            evidence.append(
                {
                    "environment": right.label,
                    "service": m.service_name,
                    "source_ref": m.source_ref,
                    "logical_key": m.logical_key,
                    "resolved_name": m.resolved_name,
                    "target": m.target,
                    "read_only": m.read_only,
                }
            )

        if at_least_one_writable:
            findings.append(
                Finding(
                    rule_id="VOL001",
                    status="fail",
                    severity="high",
                    confidence="high",
                    basis=f"same resolved volume name within declared scope '{scope_id}'",
                    resource_ref=name,
                    environments=[left.label, right.label],
                    evidence=evidence,
                    message=(
                        f"Both environments configure access to the same named volume '{name}' "
                        f"and at least one mount is writable."
                    ),
                    recommendation=(
                        "Review intended storage separation before running either stack; configure "
                        "distinct volume names or use project-scoped defaults."
                    ),
                )
            )
        else:
            # Both sides are read-only
            findings.append(
                Finding(
                    rule_id="VOL001",
                    status="warn",
                    severity="medium",
                    confidence="high",
                    basis=f"same resolved volume name within declared scope '{scope_id}' (both read-only)",
                    resource_ref=name,
                    environments=[left.label, right.label],
                    evidence=evidence,
                    message=(
                        f"Both environments configure access to named volume '{name}', "
                        f"but all mounts are read-only."
                    ),
                    recommendation=(
                        "Both environments mount this volume read-only; no write collisions detected, "
                        "but environments share read-only data."
                    ),
                )
            )

    if not shared_found:
        findings.append(
            Finding(
                rule_id="VOL001",
                status="pass",
                severity="info",
                confidence="high",
                basis=f"declared scope '{scope_id}'",
                message="No shared mounted named volumes detected between the selected environments.",
                environments=[left.label, right.label],
            )
        )

    # 4. Incomplete coverage disclaimer
    if include_coverage:
        findings.append(_coverage_finding(left.label, right.label))

    return findings


def _coverage_finding(left_label: str, right_label: str) -> Finding:
    return Finding(
        rule_id="COVERAGE",
        status="unknown",
        severity="info",
        confidence="high",
        basis="partial evaluation",
        message="Ports, bind mounts, databases, project namespaces, and runtime state were not evaluated. Exit 0 does not prove complete environment isolation.",
        environments=[left_label, right_label],
    )
