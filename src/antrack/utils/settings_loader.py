# src/utils/settings_loader.py

import configparser

def load_settings(filepath):
    """Load settings from a .cfg file into a nested dictionary."""
    settings = {}
    try:
        config = configparser.ConfigParser()
        with open(filepath, 'r') as f:
            config.read_file(f)
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
    except Exception as e:
        print(f"[ERROR] Failed to load settings: {e}")
    return settings
