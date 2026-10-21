"""Authoritative version helper for isolation-check."""
from __future__ import annotations

import importlib.metadata

MANIFEST_SCHEMA_VERSION = 1
DEFAULT_PACKAGE_VERSION = "0.2.0a1"


def get_package_version() -> str:
    """Returns the installed package version with a safe fallback for local development."""
    try:
        v = importlib.metadata.version("isolation-check")
        if v:
            return v
    except Exception:
        pass
    return DEFAULT_PACKAGE_VERSION
