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
    commit = _git_commit_from_files(repo_root)
    if commit:
        return commit
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


def _git_commit_from_files(repo_root: Path) -> str | None:
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return None
    try:
        if git_dir.is_file():
            text = git_dir.read_text(encoding="utf-8").strip()
            prefix = "gitdir:"
            if text.lower().startswith(prefix):
                git_dir = (repo_root / text[len(prefix):].strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            ref_path = git_dir / ref
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()[:7] or None
            packed_refs = git_dir / "packed-refs"
            if packed_refs.exists():
                for line in packed_refs.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith("#") and line.endswith(f" {ref}"):
                        return line.split(" ", 1)[0][:7] or None
            return None
        return head[:7] or None
    except Exception:
        return None


def display_version() -> str:
    """Return a human-friendly version string with optional git SHA."""
    commit = git_commit_short()
    return f"{version} ({commit})" if commit else version
