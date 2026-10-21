"""Rule PORT001: Fixed IPv4 host-port binding collision detector.

Compares published host ports between two development environments under an
explicit shared host assumption.
"""
from __future__ import annotations

from isolation_check.models import Finding, NormalizedEnvironment, PortBinding


def check_ports(
    left: NormalizedEnvironment,
    right: NormalizedEnvironment,
) -> list[Finding]:
    """Evaluates rule PORT001 on two normalized environments.

    Rules applied:
    1. Host Scope Verification: If host scopes differ or are missing, report UNKNOWN.
    2. Host Networking: If any service uses `network_mode: host`, report UNKNOWN.
    3. Dynamic & Range Ports: Flag dynamic port publishing and port ranges as UNKNOWN.
    4. IPv6 Bindings: Flag IPv6 bindings as UNKNOWN.
    5. Fixed IPv4 Overlaps:
       - Matches protocol (TCP vs UDP) and published host port.
       - Overlaps if either is wildcard (0.0.0.0 or omitted) OR both have identical host IP.
       - Emits FAIL on conflict.
       - Emits PASS if no fixed IPv4 conflicts exist.
    """
    findings: list[Finding] = []

    # 1. Host Scope Verification
    if not left.host_scope.is_compatible_with(right.host_scope):
        findings.append(
            Finding(
                rule_id="PORT001",
                status="unknown",
                severity="info",
                confidence="low",
                basis="unmatched or missing host scope",
                message=(
                    "Cannot evaluate host-port collisions: host scopes do not match or are missing. "
                    "Ports across unknown or separate hosts cannot collide."
                ),
                environments=[left.label, right.label],
                recommendation="Explicitly declare matching host scopes if both environments share the same host.",
            )
        )
        return findings

    scope_id = left.host_scope.scope_id or "unknown-host"

    # 2. Check Host Networking Mode
    for env in (left, right):
        if env.has_host_networking:
            findings.append(
                Finding(
                    rule_id="PORT001",
                    status="unknown",
                    severity="high",
                    confidence="high",
                    basis="host network mode",
                    message=(
                        f"Environment '{env.label}' contains at least one service using 'network_mode: host'. "
                        "Container binds directly to host network sockets without Compose port mappings, "
                        "which cannot be statically verified offline."
                    ),
                    environments=[env.label],
                    recommendation="Inspect container network configuration manually or use bridge networking with published ports.",
                )
            )

    # 3. Check for dynamic, range, or IPv6 bindings
    for env in (left, right):
        for b in env.port_bindings:
            if b.is_dynamic:
                findings.append(
                    Finding(
                        rule_id="PORT001",
                        status="unknown",
                        severity="info",
                        confidence="high",
                        basis="dynamic host port allocation",
                        message=(
                            f"Environment '{env.label}' service '{b.service_name}' configures dynamic host-port publishing "
                            f"(target {b.target_port} without a fixed published host port). Dynamic allocation cannot be verified offline."
                        ),
                        environments=[env.label],
                        evidence=[
                            {
                                "environment": env.label,
                                "service": b.service_name,
                                "source_ref": b.source_ref,
                                "target_port": b.target_port,
                            }
                        ],
                        recommendation="Specify an explicit fixed host port if collision detection is required.",
                    )
                )
            elif b.is_range:
                findings.append(
                    Finding(
                        rule_id="PORT001",
                        status="unknown",
                        severity="info",
                        confidence="high",
                        basis="published port range",
                        message=(
                            f"Environment '{env.label}' service '{b.service_name}' configures a published host port range "
                            f"'{b.raw_published}'. Port ranges are not evaluated for fixed collisions in this release."
                        ),
                        environments=[env.label],
                        evidence=[
                            {
                                "environment": env.label,
                                "service": b.service_name,
                                "source_ref": b.source_ref,
                                "raw_published": b.raw_published,
                            }
                        ],
                        recommendation="Use fixed single-port bindings if collision detection is required.",
                    )
                )
            elif b.is_ipv6:
                findings.append(
                    Finding(
                        rule_id="PORT001",
                        status="unknown",
                        severity="info",
                        confidence="low",
                        basis="unsupported IPv6 address family",
                        message=(
                            f"Environment '{env.label}' service '{b.service_name}' configures an IPv6 host binding "
                            f"'{b.host_ip}'. IPv6 dual-stack semantics are not evaluated in this release."
                        ),
                        environments=[env.label],
                        evidence=[
                            {
                                "environment": env.label,
                                "service": b.service_name,
                                "source_ref": b.source_ref,
                                "host_ip": b.host_ip,
                            }
                        ],
                        recommendation="Use IPv4 bindings for host-port collision checking.",
                    )
                )

    # 4. Compare Fixed IPv4 Port Bindings
    left_fixed = [
        b for b in left.port_bindings
        if b.published_port and not b.is_dynamic and not b.is_range and not b.is_ipv6
    ]
    right_fixed = [
        b for b in right.port_bindings
        if b.published_port and not b.is_dynamic and not b.is_range and not b.is_ipv6
    ]

    conflicts_by_key: dict[tuple[str, int], tuple[list[PortBinding], list[PortBinding]]] = {}

    for b_left in left_fixed:
        for b_right in right_fixed:
            # Different protocols (TCP vs UDP) do not conflict
            if b_left.protocol != b_right.protocol:
                continue
            # Different ports do not conflict
            if b_left.published_port != b_right.published_port:
                continue

            # Check IP overlap: either is wildcard OR both are identical IP
            overlaps = (
                b_left.is_wildcard
                or b_right.is_wildcard
                or b_left.host_ip == b_right.host_ip
            )
            if overlaps:
                key = (b_left.protocol, b_left.published_port)
                if key not in conflicts_by_key:
                    conflicts_by_key[key] = ([], [])
                if b_left not in conflicts_by_key[key][0]:
                    conflicts_by_key[key][0].append(b_left)
                if b_right not in conflicts_by_key[key][1]:
                    conflicts_by_key[key][1].append(b_right)

    has_conflict = False
    for (protocol, port), (l_bindings, r_bindings) in sorted(conflicts_by_key.items()):
        has_conflict = True
        evidence: list[dict] = []
        left_ips = sorted({b.host_ip for b in l_bindings})
        right_ips = sorted({b.host_ip for b in r_bindings})

        for b in l_bindings:
            evidence.append(
                {
                    "environment": left.label,
                    "service": b.service_name,
                    "source_ref": b.source_ref,
                    "published_port": b.published_port,
                    "host_ip": b.host_ip,
                    "protocol": b.protocol,
                }
            )
        for b in r_bindings:
            evidence.append(
                {
                    "environment": right.label,
                    "service": b.service_name,
                    "source_ref": b.source_ref,
                    "published_port": b.published_port,
                    "host_ip": b.host_ip,
                    "protocol": b.protocol,
                }
            )

        left_ip_str = ", ".join(left_ips)
        right_ip_str = ", ".join(right_ips)

        findings.append(
            Finding(
                rule_id="PORT001",
                status="fail",
                severity="high",
                confidence="high",
                basis=f"overlapping IPv4 host binding on declared host scope '{scope_id}'",
                resource_ref=f"{protocol.upper()}/{port}",
                environments=[left.label, right.label],
                evidence=evidence,
                message=(
                    f"Both environments configure overlapping fixed {protocol.upper()} host port {port} "
                    f"({left_ip_str} vs {right_ip_str})."
                ),
                recommendation="Assign distinct host ports or use dynamic publishing before starting both stacks on the same host.",
            )
        )

    if not has_conflict:
        findings.append(
            Finding(
                rule_id="PORT001",
                status="pass",
                severity="info",
                confidence="high",
                basis=f"declared host scope '{scope_id}'",
                message="No overlapping fixed IPv4 host port bindings detected between the selected environments.",
                environments=[left.label, right.label],
            )
        )

    return findings
