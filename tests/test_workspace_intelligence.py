from pathlib import Path

from workspace.workspace_manager import WorkspaceManager


def test_scan_workspace_discovers_project_metadata(tmp_path: Path):
    project_dir = tmp_path / "demo_app"
    (project_dir / "src").mkdir(parents=True)
    (project_dir / "README.md").write_text(
        "# Demo App\n\nA sample project for testing.\n\n## Installation\nRun pip install -r requirements.txt\n",
        encoding="utf-8",
    )
    (project_dir / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    (project_dir / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (project_dir / ".git").mkdir()

    manager = WorkspaceManager(root=tmp_path, cache_path=tmp_path / "workspace_cache.json")
    projects = manager.scan_workspace()

    assert len(projects) == 1
    project = projects[0]
    assert project.name == "demo_app"
    assert "Python" in project.languages
    assert project.readme is not None
    assert project.readme.description
    assert project.dependencies
    assert project.git_repository is not None
    assert project.git_repository.current_branch is not None
