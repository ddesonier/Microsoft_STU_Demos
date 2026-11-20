"""Fail-fast environment configuration helpers."""

import os
import urllib.parse


def value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def require(*names: str) -> None:
    missing = [name for name in names if not value(name)]
    if missing:
        raise RuntimeError(
            "Missing required application setting(s): " + ", ".join(missing)
        )


def require_https_url(name: str) -> str:
    require(name)
    configured = value(name).rstrip("/")
    parts = urllib.parse.urlsplit(configured)
    if parts.scheme != "https" or not parts.netloc:
        raise RuntimeError(f"{name} must be an absolute HTTPS URL")
    return configured
