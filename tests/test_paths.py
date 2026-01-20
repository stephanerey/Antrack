from pathlib import Path

from antrack.utils import paths
from antrack.utils.settings_loader import load_settings


def test_repo_root_contains_pyproject():
    repo_root = paths.get_repo_root()
    assert (repo_root / "pyproject.toml").exists()


def test_canonical_dirs():
    src_root = paths.get_src_root()
    assert paths.get_data_dir() == src_root / "data"
    assert paths.get_logs_dir() == src_root / "logs"
    assert paths.get_tle_dir() == paths.get_data_dir() / "tle"


def test_config_override(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "settings.txt"
    cfg.write_text("[AXIS_SERVER]\nIP_ADDRESS=1.2.3.4\nPORT=1234\n", encoding="utf-8")
    monkeypatch.setenv("ANTRACK_CONFIG_PATH", str(cfg))

    resolved = paths.get_config_path()
    assert resolved == cfg.resolve()

    settings = load_settings(cfg)
    assert settings["AXIS_SERVER"]["ip_address"] == "1.2.3.4"
    assert settings["AXIS_SERVER"]["port"] == 1234
