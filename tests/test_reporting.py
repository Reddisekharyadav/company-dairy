from pathlib import Path

from main import _resolve_output_dir


def test_resolve_output_dir_prefers_existing_desktop(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert _resolve_output_dir() == desktop


def test_resolve_output_dir_falls_back_to_home_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("DESKTOP", raising=False)

    resolved = _resolve_output_dir()

    assert resolved == tmp_path / "Desktop"
