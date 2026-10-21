"""Authoritative version helper for isolation-check."""
from __future__ import annotations

import importlib.metadata

# Distinct version constants for package, manifest schema, and runtime-state schema
PACKAGE_NAME = "isolation-check"
DEFAULT_PACKAGE_VERSION = "0.3.0a1"
MANIFEST_SCHEMA_VERSION = 1
RUNTIME_STATE_SCHEMA_VERSION = "1.0"


def get_package_version() -> str:
    """Returns the installed package version with a safe fallback for local development."""
    try:
        v = importlib.metadata.version(PACKAGE_NAME)
        if v:
            return v
    except Exception:
        pass
    return DEFAULT_PACKAGE_VERSION
