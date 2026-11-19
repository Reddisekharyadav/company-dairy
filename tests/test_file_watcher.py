"""Focused-window inference tests for real editor title formats."""
from __future__ import annotations

from pathlib import Path

from tracker.file_watcher import FileEditWatcher


def _project(tmp_path: Path, name: str = "vscode-project") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    return root


def test_vscode_title_resolves_file_and_project(tmp_path: Path):
    root = _project(tmp_path)
    watcher = FileEditWatcher(roots=[str(root)])

    file_path, project, editor = watcher._extract_file_info(
        "Code.exe", "app.py - Visual Studio Code"
    )

    assert Path(file_path) == (root / "app.py").resolve()
    assert project == "vscode-project"
    assert editor == "VS Code"


def test_cursor_title_resolves_file_and_project(tmp_path: Path):
    root = _project(tmp_path, "cursor-project")
    watcher = FileEditWatcher(roots=[str(root)])

    file_path, project, editor = watcher._extract_file_info(
        "Cursor.exe", "app.py - Cursor"
    )

    assert Path(file_path) == (root / "app.py").resolve()
    assert project == "cursor-project"
    assert editor == "Cursor"


def test_antigravity_title_order_resolves_file_and_project(tmp_path: Path):
    root = _project(tmp_path, "antigravity-project")
    watcher = FileEditWatcher(roots=[str(root)])

    file_path, project, editor = watcher._extract_file_info(
        "Antigravity IDE.exe", "Antigravity IDE - app.py"
    )

    assert Path(file_path) == (root / "app.py").resolve()
    assert project == "antigravity-project"
    assert editor == "Antigravity IDE"


def test_cursor_basename_is_ambiguous_when_multiple_roots(tmp_path: Path):
    cursor = _project(tmp_path, "cursor-project")
    vscode = _project(tmp_path, "vscode-project")
    watcher = FileEditWatcher(roots=[str(cursor), str(vscode)])

    file_path, project, editor = watcher._extract_file_info(
        "Cursor.exe", "app.py - Cursor"
    )

    assert file_path == "app.py"
    assert project is None
    assert editor == "Cursor"


def test_cursor_project_hint_disambiguates_same_basename(tmp_path: Path):
    cursor = _project(tmp_path, "cursor-project")
    vscode = _project(tmp_path, "vscode-project")
    watcher = FileEditWatcher(roots=[str(cursor), str(vscode)])

    file_path, project, editor = watcher._extract_file_info(
        "Cursor.exe", "app.py - cursor-project - Cursor"
    )

    assert Path(file_path) == (cursor / "app.py").resolve()
    assert project == "cursor-project"
    assert editor == "Cursor"


def test_antigravity_basename_is_ambiguous_when_multiple_roots(tmp_path: Path):
    antigravity = _project(tmp_path, "antigravity-project")
    other = _project(tmp_path, "other-project")
    watcher = FileEditWatcher(roots=[str(antigravity), str(other)])

    file_path, project, editor = watcher._extract_file_info(
        "Antigravity IDE.exe", "Antigravity IDE - app.py"
    )

    assert file_path == "app.py"
    assert project is None
    assert editor == "Antigravity IDE"


def test_non_editor_window_is_not_inferred(tmp_path: Path):
    root = _project(tmp_path)
    watcher = FileEditWatcher(roots=[str(root)])

    assert watcher._extract_file_info(
        "powershell.exe", "app.py - Windows PowerShell"
    ) is None
