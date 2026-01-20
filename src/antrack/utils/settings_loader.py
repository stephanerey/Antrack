"""Settings loader for repo-local configuration files."""

from __future__ import annotations

import configparser
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from antrack.utils.paths import get_config_path

logger = logging.getLogger("settings_loader")


def resolve_settings_path(explicit_path: Optional[Union[str, Path]] = None) -> Path:
    """Resolve settings.txt path, honoring ANTRACK_CONFIG_PATH if set.

    Args:
        explicit_path: Optional explicit path to use instead of defaults.

    Returns:
        A resolved Path to the settings file.
    """
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    return get_config_path()


def load_settings(filepath: Optional[Union[str, Path]] = None) -> Dict[str, Dict[str, Any]]:
    """Load settings from a config file into a nested dictionary.

    Args:
        filepath: Optional path to the settings file. If omitted, uses
            ANTRACK_CONFIG_PATH or repo-local defaults.

    Returns:
        A nested dict of settings: {section: {key: value}}.

    Raises:
        FileNotFoundError: If the settings file does not exist.
        configparser.Error: If the file is not readable as config.
    """
    path = resolve_settings_path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Settings file not found: {path}")

    settings: Dict[str, Dict[str, Any]] = {}
    config = configparser.ConfigParser()
    with path.open("r", encoding="utf-8") as handle:
        config.read_file(handle)
    for section in config.sections():
        settings[section] = {}
        for key, value in config.items(section):
            try:
                settings[section][key] = int(value)
            except ValueError:
                try:
                    settings[section][key] = float(value)
                except ValueError:
                    settings[section][key] = value.strip()
    return settings
