from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DependencyDetector:
    def detect(self, project_dir: Path) -> list[dict[str, str]]:
        dependencies: list[dict[str, str]] = []
        if (project_dir / "requirements.txt").exists():
            for line in (project_dir / "requirements.txt").read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name, version = self._split_requirement(line)
                dependencies.append({"name": name, "version": version})
        if (project_dir / "pyproject.toml").exists():
            dependencies.append({"name": "pyproject", "version": "managed"})
        if (project_dir / "package.json").exists():
            try:
                package_json = json.loads((project_dir / "package.json").read_text(encoding="utf-8"))
                for name, version in {**package_json.get("dependencies", {}), **package_json.get("devDependencies", {})}.items():
                    dependencies.append({"name": name, "version": str(version)})
            except Exception:
                pass
        if (project_dir / "pom.xml").exists():
            dependencies.append({"name": "pom", "version": "managed"})
        if (project_dir / "Cargo.toml").exists():
            dependencies.append({"name": "cargo", "version": "managed"})
        return dependencies

    def _split_requirement(self, requirement: str) -> tuple[str, str]:
        if "==" in requirement:
            name, version = requirement.split("==", 1)
            return name.strip(), version.strip()
        if ">=" in requirement:
            name, version = requirement.split(">=", 1)
            return name.strip(), version.strip()
        if "~=" in requirement:
            name, version = requirement.split("~=", 1)
            return name.strip(), version.strip()
        return requirement.split("[", 1)[0].strip(), ""
