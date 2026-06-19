"""Informations globales de version de l'application."""

from __future__ import annotations

import subprocess
from pathlib import Path

Version_Major = 2
Version_Minor = 0
Version_patch = 0

# Chaîne de version affichable
version = f"v{Version_Major}.{Version_Minor}.{Version_patch}"


def git_commit_short() -> str | None:
    """Return the current short git SHA when available."""
    repo_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return None
    commit = result.stdout.strip()
    return commit or None


def display_version() -> str:
    """Return a human-friendly version string with optional git SHA."""
    commit = git_commit_short()
    return f"{version} ({commit})" if commit else version
