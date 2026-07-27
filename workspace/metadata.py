from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ReadmeMetadata:
    path: str
    project_name: str | None = None
    description: str | None = None
    installation: str | None = None
    features: list[str] = field(default_factory=list)
    usage: str | None = None
    license: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReadmeMetadata":
        return cls(
            path=data.get("path", ""),
            project_name=data.get("project_name"),
            description=data.get("description"),
            installation=data.get("installation"),
            features=list(data.get("features", [])),
            usage=data.get("usage"),
            license=data.get("license"),
        )


class MetadataBuilder:
    @staticmethod
    def build_from_readme(path: str, content: str) -> ReadmeMetadata:
        return ReadmeMetadata(path=path)
