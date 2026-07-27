from __future__ import annotations

from pathlib import Path


class ProjectScanner:
    def __init__(self) -> None:
        self.indicators = [
            ".git",
            "README.md",
            "package.json",
            "pom.xml",
            "requirements.txt",
            "Cargo.toml",
            "Dockerfile",
            "docker-compose.yml",
            "pyproject.toml",
            "build.gradle",
            "CMakeLists.txt",
            "Makefile",
            "go.mod",
        ]

    def find_projects(self, root: Path) -> list[Path]:
        root = root.resolve()
        if not root.exists():
            return []

        projects: list[Path] = []
        for path in sorted(root.rglob("*")):
            if not path.is_dir():
                continue
            # Skip virtual environment and build directories
            if any(part in {".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__"} for part in path.parts):
                continue
            if any((path / marker).exists() for marker in self.indicators):
                projects.append(path)
        return projects
