"""Report generator for isolation-check findings in text and versioned JSON formats."""
from __future__ import annotations

import json
from typing import Any

from isolation_check.models import EngineScope, Finding, HostScope, Report


from isolation_check.version import get_package_version


def build_report(
    findings: list[Finding],
    environments: list[str],
    engine_scope: EngineScope,
    host_scope: HostScope | None = None,
) -> Report:
    """Aggregates findings into a complete Report object."""
    summary = {"fail": 0, "warn": 0, "pass": 0, "unknown": 0, "ignored": 0}
    for f in findings:
        if f.status in summary:
            summary[f.status] += 1
        else:
            summary["unknown"] += 1

    return Report(
        schema_version="1.0",
        tool_version=get_package_version(),
        mode="configuration-only",
        complete=False,  # Always partial in v0.1 because broader coverage is not yet evaluated
        engine_scope=engine_scope.to_dict(),
        host_scope=host_scope.to_dict() if host_scope else {},
        environments=environments,
        summary=summary,
        findings=findings,
    )


def format_text_report(report: Report) -> str:
    """Formats the report into human-readable terminal text."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("isolation-check -- CONFIGURATION AUDIT")
    scope_id = report.engine_scope.get("scope_id") or "unspecified"
    scope_basis = report.engine_scope.get("basis") or "no scope basis"
    lines.append(f"Scope: {scope_id} ({scope_basis})")
    if report.host_scope and report.host_scope.get("scope_id"):
        h_scope_id = report.host_scope.get("scope_id")
        h_scope_basis = report.host_scope.get("basis") or "no host scope basis"
        lines.append(f"Host Scope: {h_scope_id} ({h_scope_basis})")
    lines.append("Analysis: configuration-only; exit 0 does not prove complete isolation")
    lines.append("=" * 72)
    lines.append("")

    for finding in report.findings:
        status_tag = finding.status.upper().ljust(7)
        rule_tag = finding.rule_id.ljust(8)
        lines.append(f"{status_tag} {rule_tag} {finding.message}")

        if finding.evidence:
            for ev in finding.evidence:
                env = ev.get("environment", "")
                svc = ev.get("service", "")
                resolved = ev.get("resolved_name")
                logical = ev.get("logical_key")
                mode = "read-only" if ev.get("read_only") else "writable"
                target = ev.get("target")

                if "published_port" in ev:
                    proto = str(ev.get("protocol", "tcp")).upper()
                    port = ev.get("published_port")
                    ip = ev.get("host_ip", "0.0.0.0")
                    src = ev.get("source_ref", "")
                    lines.append(f"        {env}: service '{svc}' -> {ip}:{port}/{proto} ({src})")
                elif resolved:
                    lines.append(
                        f"        {env}: service '{svc}' -> logical '{logical}' "
                        f"(resolved '{resolved}', {mode}, target: '{target}')"
                    )
                elif target:
                    lines.append(
                        f"        {env}: service '{svc}' -> target '{target}' ({mode})"
                    )
                else:
                    lines.append(f"        {env}: service '{svc}'")

        if finding.basis and finding.status in ("fail", "warn"):
            lines.append(f"        basis: {finding.basis}")

        if finding.recommendation:
            lines.append(f"        next:  {finding.recommendation}")

        lines.append("")

    lines.append("-" * 72)
    s = report.summary
    lines.append(
        f"Summary: {s['fail']} fail, {s['warn']} warn, {s['pass']} pass, "
        f"{s['unknown']} unknown, {s['ignored']} ignored"
    )
    lines.append("Coverage: incomplete -- not a full isolation guarantee")
    lines.append("=" * 72)

    return "\n".join(lines)


def format_json_report(report: Report) -> str:
    """Serializes the report to structured JSON."""
    return json.dumps(report.to_dict(), indent=2)
