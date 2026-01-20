"""Path helpers for repo-local development mode."""

from __future__ import annotations

import os
from pathlib import Path


def get_repo_root() -> Path:
    """Return the repository root (dev mode only)."""
    return Path(__file__).resolve().parents[3]


def get_src_root() -> Path:
    """Return the repo-local src/ directory."""
    return get_repo_root() / "src"


def get_data_dir() -> Path:
    """Return the canonical data directory (src/data)."""
    return get_src_root() / "data"


def get_logs_dir() -> Path:
    """Return the canonical logs directory (src/logs)."""
    return get_src_root() / "logs"


def get_log_file() -> Path:
    """Return the canonical log file path (src/logs/antrack.log)."""
    return get_logs_dir() / "antrack.log"


def get_ephemeris_dir() -> Path:
    """Return the ephemeris directory (src/data/ephemeris)."""
    return get_data_dir() / "ephemeris"


def get_tle_dir() -> Path:
    """Return the TLE directory (src/data/tle)."""
    return get_data_dir() / "tle"


def get_radiosources_dir() -> Path:
    """Return the radio sources directory (src/data/radiosources)."""
    return get_data_dir() / "radiosources"


def get_spacecrafts_dir() -> Path:
    """Return the spacecrafts directory (src/data/spacecrafts)."""
    return get_data_dir() / "spacecrafts"


def get_config_path() -> Path:
    """Resolve the settings.txt path with ANTRACK_CONFIG_PATH override."""
    override = os.getenv("ANTRACK_CONFIG_PATH")
    if override:
        return Path(override).expanduser().resolve()

    repo_default = get_repo_root() / "settings.txt"
    if repo_default.exists():
        return repo_default.resolve()

    legacy = get_src_root() / "antrack" / "settings.cfg"
    if legacy.exists():
        return legacy.resolve()

    return repo_default.resolve()
