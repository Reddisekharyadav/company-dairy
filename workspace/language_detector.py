from __future__ import annotations

from pathlib import Path


class LanguageDetector:
    def detect(self, project_dir: Path) -> tuple[list[str], list[str]]:
        project_dir = project_dir.resolve()
        languages: list[str] = []
        frameworks: list[str] = []
        files = {path.name for path in project_dir.iterdir() if path.is_file()}

        if (project_dir / "requirements.txt").exists() or (project_dir / "pyproject.toml").exists() or any(path.suffix == ".py" for path in project_dir.rglob("*.py")):
            languages.append("Python")
        if (project_dir / "pom.xml").exists() or (project_dir / "build.gradle").exists() or any(path.suffix == ".java" for path in project_dir.rglob("*.java")):
            languages.append("Java")
        if (project_dir / "package.json").exists() or any(path.suffix in {".js", ".ts", ".jsx", ".tsx"} for path in project_dir.rglob("*.js")):
            languages.append("Node")
        if (project_dir / "Cargo.toml").exists() or any(path.suffix == ".rs" for path in project_dir.rglob("*.rs")):
            languages.append("Rust")
        if (project_dir / "go.mod").exists() or any(path.suffix == ".go" for path in project_dir.rglob("*.go")):
            languages.append("Go")
        if (project_dir / "CMakeLists.txt").exists() or (project_dir / "Makefile").exists() or any(path.suffix in {".cpp", ".cc", ".cxx", ".c", ".h"} for path in project_dir.rglob("*.cpp")):
            languages.append("C++")

        if (project_dir / "package.json").exists():
            try:
                import json

                package_json = json.loads((project_dir / "package.json").read_text(encoding="utf-8"))
                dependencies = {**package_json.get("dependencies", {}), **package_json.get("devDependencies", {})}
                if "react" in dependencies or "react-dom" in dependencies:
                    frameworks.append("React")
                if "next" in dependencies:
                    frameworks.append("Next.js")
            except Exception:
                pass

        if (project_dir / "pom.xml").exists():
            frameworks.append("Spring")
        if (project_dir / "Dockerfile").exists() or (project_dir / "docker-compose.yml").exists():
            frameworks.append("Docker")

        return sorted(set(languages)), sorted(set(frameworks))
