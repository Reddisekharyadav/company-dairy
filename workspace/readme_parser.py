from __future__ import annotations

from pathlib import Path

from .metadata import ReadmeMetadata


class ReadmeParser:
    def parse(self, path: Path) -> ReadmeMetadata | None:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8", errors="ignore")
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        metadata = ReadmeMetadata(path=str(path))
        metadata.project_name = lines[0].lstrip("# ").strip() if lines else None
        metadata.description = lines[1] if len(lines) > 1 else None
        installation_index = next((index for index, line in enumerate(lines) if line.lower().startswith("## installation")), None)
        if installation_index is not None and installation_index + 1 < len(lines):
            metadata.installation = lines[installation_index + 1]
        features_index = next((index for index, line in enumerate(lines) if line.lower().startswith("## features")), None)
        if features_index is not None:
            metadata.features = [line.lstrip("-*").strip() for line in lines[features_index + 1 : features_index + 4] if line and not line.startswith("##")]
        usage_index = next((index for index, line in enumerate(lines) if line.lower().startswith("## usage")), None)
        if usage_index is not None and usage_index + 1 < len(lines):
            metadata.usage = lines[usage_index + 1]
        license_index = next((index for index, line in enumerate(lines) if line.lower().startswith("## license")), None)
        if license_index is not None and license_index + 1 < len(lines):
            metadata.license = lines[license_index + 1]
        return metadata
