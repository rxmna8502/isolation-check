"""Data models for isolation-check."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EngineScope:
    """Represents the execution engine scope for resource identity.

    Volume names alone do not guarantee identity unless evaluated within
    the same declared engine scope.
    """
    scope_id: str | None
    basis: str = "explicit shared local-engine assumption"

    def is_compatible_with(self, other: EngineScope) -> bool:
        if self.scope_id is None or other.scope_id is None:
            return False
        return self.scope_id == other.scope_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "basis": self.basis,
        }


@dataclass
class HostScope:
    """Represents the host execution context for host-level resources like ports."""
    scope_id: str | None
    basis: str = "explicit shared local-host assumption"

    def is_compatible_with(self, other: HostScope) -> bool:
        if self.scope_id is None or other.scope_id is None:
            return False
        return self.scope_id == other.scope_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "basis": self.basis,
        }


@dataclass
class VolumeMount:
    """Represents a volume mounted by a specific service."""
    service_name: str
    logical_key: str
    resolved_name: str | None
    target: str
    read_only: bool
    source_ref: str
    is_anonymous: bool = False
    driver: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PortBinding:
    """Represents a published host port binding configured by a service."""
    service_name: str
    published_port: int | None
    target_port: int | None
    protocol: str  # "tcp" or "udp"
    host_ip: str  # canonical IP e.g. "127.0.0.1" or "0.0.0.0"
    is_wildcard: bool
    is_dynamic: bool = False
    is_range: bool = False
    is_ipv6: bool = False
    source_ref: str = ""
    raw_published: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedEnvironment:
    """Normalized configuration snapshot for a single Compose environment."""
    label: str
    root: str
    compose_project_name: str | None
    engine_scope: EngineScope
    host_scope: HostScope = field(default_factory=lambda: HostScope(scope_id="local-host"))
    mounted_volumes: list[VolumeMount] = field(default_factory=list)
    top_level_volumes: dict[str, dict[str, Any]] = field(default_factory=dict)
    port_bindings: list[PortBinding] = field(default_factory=list)
    has_host_networking: bool = False
    services: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "root": self.root,
            "compose_project_name": self.compose_project_name,
            "engine_scope": self.engine_scope.to_dict(),
            "host_scope": self.host_scope.to_dict(),
            "mounted_volumes": [v.to_dict() for v in self.mounted_volumes],
            "top_level_volumes": self.top_level_volumes,
            "port_bindings": [p.to_dict() for p in self.port_bindings],
            "has_host_networking": self.has_host_networking,
            "services": self.services,
            "uncertainties": self.uncertainties,
        }


@dataclass
class Finding:
    """Individual rule evaluation result."""
    rule_id: str
    status: str  # "fail", "warn", "pass", "unknown", "ignored"
    severity: str  # "high", "medium", "low", "info"
    confidence: str  # "high", "medium", "low"
    message: str
    basis: str
    resource_ref: str | None = None
    environments: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    recommendation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "rule_id": self.rule_id,
            "status": self.status,
            "severity": self.severity,
            "confidence": self.confidence,
            "basis": self.basis,
            "message": self.message,
        }
        if self.resource_ref is not None:
            res["resource_ref"] = self.resource_ref
        if self.environments:
            res["environments"] = self.environments
        if self.evidence:
            res["evidence"] = self.evidence
        if self.recommendation is not None:
            res["recommendation"] = self.recommendation
        return res


from isolation_check.version import get_package_version


@dataclass
class Report:
    """Overall comparison report conforming to handbook v0.1 schema."""
    schema_version: str = "1.0"
    tool_version: str = field(default_factory=get_package_version)
    mode: str = "configuration-only"
    complete: bool = False
    engine_scope: dict[str, Any] = field(default_factory=dict)
    host_scope: dict[str, Any] = field(default_factory=dict)
    environments: list[str] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=lambda: {
        "fail": 0, "warn": 0, "pass": 0, "unknown": 0, "ignored": 0
    })
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        res = {
            "schema_version": self.schema_version,
            "tool_version": self.tool_version,
            "mode": self.mode,
            "complete": self.complete,
            "engine_scope": self.engine_scope,
            "environments": self.environments,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
        }
        if self.host_scope:
            res["host_scope"] = self.host_scope
        return res
