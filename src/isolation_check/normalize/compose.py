"""Normalizes canonical Compose JSON models into engine-facing resource records."""
from __future__ import annotations

import ipaddress
from typing import Any

from isolation_check.models import (
    EngineScope,
    HostScope,
    NormalizedEnvironment,
    PortBinding,
    VolumeMount,
)


def _parse_port_entry(item: Any, service_name: str, idx: int) -> PortBinding:
    """Parses a port entry into a structured PortBinding."""
    source_ref = f"services.{service_name}.ports[{idx}]"

    if isinstance(item, dict):
        raw_target = item.get("target")
        raw_published = item.get("published")
        protocol = str(item.get("protocol") or "tcp").lower()
        host_ip = item.get("host_ip")
    elif isinstance(item, str):
        # Short-form string fallback: [ [host_ip:]published: ] target [/protocol]
        s = item.strip()
        protocol = "tcp"
        if "/" in s:
            s, proto_part = s.rsplit("/", 1)
            protocol = proto_part.lower()

        host_ip = None
        raw_published = None
        raw_target = None

        if s.startswith("[") and "]" in s:
            ip_end = s.index("]")
            host_ip = s[1:ip_end]
            rest = s[ip_end + 1:]
            if rest.startswith(":"):
                rest = rest[1:]
            parts = rest.split(":")
            if len(parts) == 2:
                raw_published, raw_target = parts
            elif len(parts) == 1:
                raw_published = parts[0]
        else:
            parts = s.split(":")
            if len(parts) == 3:
                host_ip, raw_published, raw_target = parts
            elif len(parts) == 2:
                raw_published, raw_target = parts
            elif len(parts) == 1:
                raw_target = parts[0]
                raw_published = None
    elif isinstance(item, int):
        raw_target = item
        raw_published = None
        protocol = "tcp"
        host_ip = None
    else:
        return PortBinding(
            service_name=service_name,
            published_port=None,
            target_port=None,
            protocol="tcp",
            host_ip="0.0.0.0",
            is_wildcard=True,
            is_dynamic=True,
            source_ref=source_ref,
        )

    # Parse target port
    try:
        target_port = int(raw_target) if raw_target is not None else None
    except (ValueError, TypeError):
        target_port = None

    # Parse host_ip and wildcard
    is_wildcard = False
    is_ipv6 = False
    canonical_ip = "0.0.0.0"

    if not host_ip or host_ip == "0.0.0.0":
        canonical_ip = "0.0.0.0"
        is_wildcard = True
    else:
        clean_ip = host_ip.strip("[]")
        if ":" in clean_ip:
            is_ipv6 = True
            canonical_ip = clean_ip
            is_wildcard = (clean_ip == "::")
        else:
            try:
                addr = ipaddress.ip_address(clean_ip)
                if isinstance(addr, ipaddress.IPv4Address):
                    canonical_ip = str(addr)
                    is_wildcard = (addr == ipaddress.IPv4Address("0.0.0.0"))
                else:
                    is_ipv6 = True
                    canonical_ip = str(addr)
            except ValueError:
                canonical_ip = clean_ip

    # Parse published port
    is_dynamic = False
    is_range = False
    published_port = None

    if raw_published is None or raw_published == "" or raw_published == 0:
        is_dynamic = True
    else:
        str_pub = str(raw_published).strip()
        if "-" in str_pub:
            is_range = True
        else:
            try:
                p_num = int(str_pub)
                if 1 <= p_num <= 65535:
                    published_port = p_num
                else:
                    is_dynamic = True
            except ValueError:
                is_dynamic = True

    return PortBinding(
        service_name=service_name,
        published_port=published_port,
        target_port=target_port,
        protocol=protocol,
        host_ip=canonical_ip,
        is_wildcard=is_wildcard,
        is_dynamic=is_dynamic,
        is_range=is_range,
        is_ipv6=is_ipv6,
        source_ref=source_ref,
        raw_published=str(raw_published) if raw_published is not None else None,
    )


def normalize_compose_model(
    raw_config: dict[str, Any],
    label: str,
    root: str,
    engine_scope: EngineScope,
    host_scope: HostScope | None = None,
) -> NormalizedEnvironment:
    """Transforms raw Compose config JSON into a normalized environment model.

    Crucially:
    1. Extracts top-level volumes and their resolved engine-facing names.
    2. Maps service mounts to resolved names, access modes (ro/rw), and provenance.
    3. Excludes unused top-level volumes from `mounted_volumes`.
    4. Normalizes published host ports, distinguishing fixed bindings from dynamic/range/IPv6.
    5. Detects 'network_mode: host'.
    6. Ignores container-internal 'expose' declarations.
    """
    if host_scope is None:
        host_scope = HostScope(scope_id="local-host")

    project_name = raw_config.get("name")
    top_level_volumes_raw = raw_config.get("volumes") or {}
    if not isinstance(top_level_volumes_raw, dict):
        top_level_volumes_raw = {}

    top_level_volumes: dict[str, dict[str, Any]] = {}
    for key, val in top_level_volumes_raw.items():
        if val is None:
            top_level_volumes[key] = {}
        elif isinstance(val, dict):
            top_level_volumes[key] = dict(val)
        else:
            top_level_volumes[key] = {"value": val}

    services_raw = raw_config.get("services") or {}
    if not isinstance(services_raw, dict):
        services_raw = {}

    mounted_volumes: list[VolumeMount] = []
    port_bindings: list[PortBinding] = []
    has_host_networking = False
    uncertainties: list[str] = []

    for service_name, service_cfg in services_raw.items():
        if not isinstance(service_cfg, dict):
            continue

        # Check host network mode
        if service_cfg.get("network_mode") == "host":
            has_host_networking = True
            uncertainties.append(f"Service '{service_name}' uses 'network_mode: host'.")

        # 1. Volume mounts
        v_list = service_cfg.get("volumes") or []
        if isinstance(v_list, list):
            for idx, item in enumerate(v_list):
                if not isinstance(item, dict):
                    continue

                mount_type = item.get("type", "volume")
                if mount_type != "volume":
                    continue

                source = item.get("source")
                target = item.get("target") or ""
                read_only = bool(item.get("read_only", False))
                source_ref = f"services.{service_name}.volumes[{idx}]"

                if not source:
                    mounted_volumes.append(
                        VolumeMount(
                            service_name=service_name,
                            logical_key="",
                            resolved_name=None,
                            target=target,
                            read_only=read_only,
                            source_ref=source_ref,
                            is_anonymous=True,
                        )
                    )
                    uncertainties.append(
                        f"Service '{service_name}' mounts an anonymous volume at '{target}'."
                    )
                    continue

                driver: str | None = None
                if source in top_level_volumes:
                    vol_meta = top_level_volumes[source]
                    driver = vol_meta.get("driver")
                    if "name" in vol_meta and isinstance(vol_meta["name"], str):
                        resolved_name = vol_meta["name"]
                    else:
                        resolved_name = f"{project_name}_{source}" if project_name else source
                else:
                    resolved_name = f"{project_name}_{source}" if project_name else source

                mounted_volumes.append(
                    VolumeMount(
                        service_name=service_name,
                        logical_key=source,
                        resolved_name=resolved_name,
                        target=target,
                        read_only=read_only,
                        source_ref=source_ref,
                        is_anonymous=False,
                        driver=driver,
                    )
                )

        # 2. Port bindings (published to host)
        # Note: 'expose' entries are internal to the container network and ignored for host bindings
        p_list = service_cfg.get("ports") or []
        if isinstance(p_list, list):
            for idx, p_item in enumerate(p_list):
                binding = _parse_port_entry(p_item, service_name, idx)
                port_bindings.append(binding)
                if binding.is_dynamic:
                    uncertainties.append(
                        f"Service '{service_name}' configures dynamic host port for target {binding.target_port}."
                    )
                elif binding.is_range:
                    uncertainties.append(
                        f"Service '{service_name}' configures host port range '{binding.raw_published}'."
                    )
                elif binding.is_ipv6:
                    uncertainties.append(
                        f"Service '{service_name}' configures IPv6 host binding '{binding.host_ip}'."
                    )

    return NormalizedEnvironment(
        label=label,
        root=root,
        compose_project_name=project_name,
        engine_scope=engine_scope,
        host_scope=host_scope,
        mounted_volumes=mounted_volumes,
        top_level_volumes=top_level_volumes,
        port_bindings=port_bindings,
        has_host_networking=has_host_networking,
        services=list(services_raw.keys()),
        uncertainties=uncertainties,
    )
