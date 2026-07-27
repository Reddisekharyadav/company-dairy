from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Repository:
    path: str
    current_branch: str = "main"
    remote: str | None = None
    last_commit: str | None = None
    modified_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Repository":
        return cls(
            path=data.get("path", ""),
            current_branch=data.get("current_branch", "main"),
            remote=data.get("remote"),
            last_commit=data.get("last_commit"),
            modified_files=list(data.get("modified_files", [])),
            untracked_files=list(data.get("untracked_files", [])),
        )
