from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dependency_detector import DependencyDetector
from .language_detector import LanguageDetector
from .metadata import MetadataBuilder
from .project import Project
from .project_scanner import ProjectScanner
from .readme_parser import ReadmeParser
from .repository import Repository


class WorkspaceManager:
    def __init__(self, root: str | Path | None = None, cache_path: str | Path | None = None) -> None:
        self.root = Path(root or Path.cwd()).resolve()
        self.cache_path = Path(cache_path or self.root / "workspace_cache.json").resolve()
        self.scanner = ProjectScanner()
        self.language_detector = LanguageDetector()
        self.dependency_detector = DependencyDetector()
        self.readme_parser = ReadmeParser()
        self.metadata_builder = MetadataBuilder()

    def scan_workspace(self) -> list[Project]:
        cached = self._load_cache()
        if cached is not None:
            return [Project.from_dict(item) for item in cached]

        projects: list[Project] = []
        for project_dir in self.scanner.find_projects(self.root):
            project = self._build_project(project_dir)
            if project is not None:
                projects.append(project)
        self._save_cache(projects)
        return projects

    def list_projects(self) -> list[str]:
        return [project.name for project in self.scan_workspace()]

    def get_project(self, name: str) -> Project | None:
        for project in self.scan_workspace():
            if project.name.lower() == name.lower():
                return project
        return None

    def get_stats(self) -> dict[str, Any]:
        projects = self.scan_workspace()
        languages: dict[str, int] = {}
        for project in projects:
            for language in project.languages:
                languages[language] = languages.get(language, 0) + 1
        return {
            "project_count": len(projects),
            "languages": languages,
            "git_repositories": sum(1 for project in projects if project.git_repository is not None),
            "readme_count": sum(1 for project in projects if project.readme is not None),
            "total_size": sum(project.size for project in projects),
        }

    def refresh(self) -> list[Project]:
        self.cache_path.unlink(missing_ok=True)
        return self.scan_workspace()

    def _build_project(self, project_dir: Path) -> Project | None:
        if not project_dir.exists():
            return None
        languages, frameworks = self.language_detector.detect(project_dir)
        dependencies = self.dependency_detector.detect(project_dir)
        readme_path = project_dir / "README.md"
        readme = self.readme_parser.parse(readme_path) if readme_path.exists() else None
        repository = self._detect_repository(project_dir)
        size = sum(path.stat().st_size for path in project_dir.rglob("*") if path.is_file())
        project = Project(
            name=project_dir.name,
            path=str(project_dir),
            languages=languages,
            frameworks=frameworks,
            git_repository=repository,
            dependencies=dependencies,
            readme=readme,
            size=size,
            last_modified=datetime.fromtimestamp(project_dir.stat().st_mtime, tz=timezone.utc).isoformat(),
            tags=self._derive_tags(project_dir, languages, frameworks),
        )
        return project

    def _detect_repository(self, project_dir: Path) -> Repository | None:
        git_dir = project_dir / ".git"
        if not git_dir.exists():
            return None
        branch = "main"
        try:
            branch = (project_dir / ".git" / "HEAD").read_text(encoding="utf-8", errors="ignore").split("/")[-1].strip()
        except Exception:
            pass
        return Repository(
            path=str(project_dir),
            current_branch=branch,
            remote=None,
            last_commit=None,
            modified_files=[],
            untracked_files=[],
        )

    def _derive_tags(self, project_dir: Path, languages: list[str], frameworks: list[str]) -> list[str]:
        tags = []
        if (project_dir / "Dockerfile").exists() or (project_dir / "docker-compose.yml").exists():
            tags.append("docker")
        if (project_dir / ".venv").exists() or (project_dir / "venv").exists():
            tags.append("venv")
        tags.extend(languages)
        tags.extend(frameworks)
        return sorted(set(tags))

    def _load_cache(self) -> list[dict[str, Any]] | None:
        if not self.cache_path.exists():
            return None
        try:
            with self.cache_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def _save_cache(self, projects: list[Project]) -> None:
        payload = [project.to_dict() for project in projects]
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
