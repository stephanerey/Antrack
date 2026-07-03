"""Instrument backends exposed by the core package.

Keep submodule imports lazy so optional SDR dependencies do not load during
unrelated code paths such as powermeter UI setup.
"""

from __future__ import annotations

from importlib import import_module

__all__ = ["PowermeterClient", "SdrClient"]


def __getattr__(name: str):
    if name == "PowermeterClient":
        return import_module("antrack.core.instruments.powermeter_client").PowermeterClient
    if name == "SdrClient":
        return import_module("antrack.core.instruments.sdr_client").SdrClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
