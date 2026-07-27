from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .metadata import ReadmeMetadata
from .repository import Repository


@dataclass
class Project:
    name: str
    path: str
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    git_repository: Optional[Repository] = None
    dependencies: list[dict[str, str]] = field(default_factory=list)
    readme: Optional[ReadmeMetadata] = None
    size: int = 0
    last_modified: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.git_repository is not None:
            payload["git_repository"] = self.git_repository.to_dict()
        if self.readme is not None:
            payload["readme"] = self.readme.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        readme = None
        if data.get("readme"):
            readme = ReadmeMetadata.from_dict(data["readme"])
        repository = None
        if data.get("git_repository"):
            repository = Repository.from_dict(data["git_repository"])
        return cls(
            name=data.get("name", ""),
            path=data.get("path", ""),
            languages=list(data.get("languages", [])),
            frameworks=list(data.get("frameworks", [])),
            git_repository=repository,
            dependencies=list(data.get("dependencies", [])),
            readme=readme,
            size=int(data.get("size", 0)),
            last_modified=data.get("last_modified"),
            tags=list(data.get("tags", [])),
        )
