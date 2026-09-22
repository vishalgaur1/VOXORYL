from __future__ import annotations

"""Compat shim — preference + status live in voxoryl.inference."""

from voxoryl.inference import (
    cloud_ready,
    effective_mode,
    effective_provider,
    set_mode,
    status,
)

__all__ = ["load", "set_mode", "current_mode", "cloud_configured", "status_dict"]


def load() -> dict:
    return status()


def status_dict(mode: str | None = None) -> dict:
    if mode is not None:
        return set_mode(mode)
    return status()


def current_mode() -> str:
    return effective_mode()


def cloud_configured() -> bool:
    return bool(cloud_ready().get("ok"))


def cloud_provider_label() -> str:
    return effective_provider()
