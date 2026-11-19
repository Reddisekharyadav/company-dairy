"""Shared file/IDE metadata used by the duration and filesystem watchers."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

TRACKED_EXTENSIONS = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".go", ".rb", ".php", ".rs",
    ".swift", ".kt", ".kts", ".vue", ".svelte", ".html", ".htm", ".css",
    ".scss", ".sass", ".less", ".json", ".jsonc", ".yaml", ".yml", ".toml",
    ".xml", ".md", ".mdx", ".rst", ".txt", ".sql", ".sh", ".bash", ".zsh",
    ".ps1", ".bat", ".cmd", ".dockerfile", ".tf", ".hcl", ".ipynb", ".gradle",
    ".properties", ".ini", ".cfg", ".env", ".gitignore", ".editorconfig",
}

# Files without an extension that are still source/config files.
TRACKED_FILENAMES = {
    "dockerfile", "makefile", "justfile", "procfile", "gemfile", "rakefile",
}

EXT_LANG_MAP = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript/React",
    ".jsx": "JavaScript/React", ".java": "Java", ".c": "C", ".h": "C/C++ Header",
    ".cpp": "C++", ".hpp": "C++", ".cc": "C++", ".cs": "C#", ".go": "Go",
    ".rb": "Ruby", ".php": "PHP", ".rs": "Rust", ".swift": "Swift",
    ".kt": "Kotlin", ".kts": "Kotlin", ".vue": "Vue", ".svelte": "Svelte",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".sass": "Sass", ".less": "Less", ".sql": "SQL", ".sh": "Shell",
    ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell", ".bat": "Batch",
    ".cmd": "Batch", ".md": "Markdown", ".mdx": "MDX", ".json": "JSON",
    ".jsonc": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".tf": "Terraform", ".hcl": "HCL", ".ipynb": "Jupyter Notebook",
    ".gradle": "Gradle", ".xml": "XML", ".rst": "reStructuredText",
    ".env": "Environment", ".ini": "INI", ".cfg": "Config",
}

IDE_PROCESS_MAP = {
    "code": "VS Code", "code.exe": "VS Code",
    "code - insiders": "VS Code Insiders", "code-insiders": "VS Code Insiders",
    "cursor": "Cursor", "cursor.exe": "Cursor",
    "pycharm": "PyCharm", "pycharm.exe": "PyCharm", "pycharm64.exe": "PyCharm",
    "idea": "IntelliJ IDEA", "idea.exe": "IntelliJ IDEA", "idea64.exe": "IntelliJ IDEA",
    "webstorm": "WebStorm", "webstorm.exe": "WebStorm", "webstorm64.exe": "WebStorm",
    "rider": "Rider", "rider.exe": "Rider", "rider64.exe": "Rider",
    "clion": "CLion", "clion.exe": "CLion", "clion64.exe": "CLion",
    "goland": "GoLand", "goland.exe": "GoLand", "goland64.exe": "GoLand",
    "rubymine": "RubyMine", "rubymine.exe": "RubyMine", "rubymine64.exe": "RubyMine",
    "phpstorm": "PhpStorm", "phpstorm.exe": "PhpStorm", "phpstorm64.exe": "PhpStorm",
    "sublime_text": "Sublime Text", "sublime_text.exe": "Sublime Text",
    "atom": "Atom", "atom.exe": "Atom",
    "notepad++": "Notepad++", "notepad++.exe": "Notepad++",
    "vim": "Vim", "nvim": "Neovim", "nvim.exe": "Neovim", "emacs": "Emacs",
    "emacs.exe": "Emacs", "notepad": "Notepad", "notepad.exe": "Notepad",
    "antigravity": "Antigravity IDE", "antigravity.exe": "Antigravity IDE",
    "fleet": "Fleet", "fleet.exe": "Fleet", "zed": "Zed", "zed.exe": "Zed",
}

DEFAULT_IGNORED_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".nox", "node_modules", "bower_components",
    "venv", ".venv", "env", ".env", "dist", "build", "out", "target",
    "bin", "obj", ".next", ".nuxt", ".cache", ".idea", ".vscode",
    "coverage", "htmlcov", ".gradle", ".terraform", ".serverless",
    "site-packages", "Library", "Temp", "tmp",
}

DEFAULT_IGNORED_SUFFIXES = {
    ".tmp", ".temp", ".swp", ".swo", ".lock", ".log", ".pyc", ".pyo",
    ".class", ".o", ".obj", ".so", ".dll", ".dylib", ".exe", ".pdb",
    ".crdownload", ".part", ".partial", ".download", ".bak", "~",
}

DEFAULT_IGNORED_PREFIXES = ("~$", ".~lock.", ".#")


def get_language(file_path: str) -> Optional[str]:
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext in EXT_LANG_MAP:
        return EXT_LANG_MAP[ext]
    if path.name.lower() in TRACKED_FILENAMES:
        return path.name.title()
    return None


def is_tracked_file(file_path: str, extra_extensions=None) -> bool:
    path = Path(file_path)
    name = path.name.lower()
    if name in TRACKED_FILENAMES:
        return True
    ext = path.suffix.lower()
    if extra_extensions and ext in {str(e).lower() for e in extra_extensions}:
        return True
    return ext in TRACKED_EXTENSIONS


def detect_ide(process_name: str) -> Optional[str]:
    if not process_name:
        return None
    proc = process_name.lower().strip()
    if proc in IDE_PROCESS_MAP:
        return IDE_PROCESS_MAP[proc]
    for key, name in IDE_PROCESS_MAP.items():
        if len(key) >= 5 and proc.startswith(key):
            return name
    return None
