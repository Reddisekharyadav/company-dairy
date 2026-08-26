# -*- mode: python -*-
# WorkSense AI — Background Tracker + Report Generator
# No web server. Runs silently in system tray.
# Build: venv\Scripts\python.exe -m PyInstaller worksense_onefile.spec --noconfirm --clean
import os
import platform
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

project_root = os.path.abspath(os.getcwd())
block_cipher = None


# ── Hidden imports ────────────────────────────────────────────────────────────
hiddenimports = []

# Pystray (tray icon)
# Web & App dependencies
try:
    hiddenimports += collect_submodules('uvicorn')
    hiddenimports += collect_submodules('fastapi')
    hiddenimports += collect_submodules('starlette')
except Exception:
    pass

hiddenimports += [
    # UI (floating status widget)
    'tkinter',
    'tkinter.messagebox',
    'ui',
    'ui.status_widget',
    # Tray icon
    'pystray._win32',
    # System monitoring
    'psutil',
    # Image & OCR (screen capture)
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'mss',
    'pytesseract',
    'io',
    # SQLAlchemy (database)
    'sqlalchemy',
    'sqlalchemy.orm',
    'sqlalchemy.dialects',
    'sqlalchemy.dialects.sqlite',
    'sqlalchemy.dialects.sqlite.pysqlite',
    # Report generation
    'jinja2',
    'docx',
    'docx.oxml',
    'docx.oxml.ns',
    'reportlab',
    'reportlab.graphics',
    'reportlab.lib',
    'reportlab.lib.pagesizes',
    'reportlab.lib.colors',
    'reportlab.pdfgen',
    'reportlab.pdfgen.canvas',
    # Git watcher
    'git',
    'gitdb',
    # Web server & API
    'uvicorn',
    'fastapi',
    'starlette',
    'anyio',
    'httptools',
    'websockets',
    'python_multipart',
    # Tracker modules
    'tracker.file_watcher',
    'tracker.search_extractor',
    'tracker.session_memory',
    'tracker.browser_history',
    'tracker.active_window',
    'tracker.categorizer',
    'reports.briefing',
    'watchdog',
    # .env support
    'dotenv',
    # Standard
    'threading',
    'logging',
    'sqlite3',
    'pathlib',
    'datetime',
    'tempfile',
    'shutil',
    'urllib',
    'urllib.parse',
]

# Platform-specific imports
if platform.system() == 'Windows':
    hiddenimports += [
        'win32gui',
        'win32process',
        'win32con',
        'pywintypes',
    ]

# ── Data files ────────────────────────────────────────────────────────────────
datas = []

# Bundle backend templates and static files
for base_dir in ('backend/templates', 'backend/static'):
    full_path = os.path.join(project_root, base_dir)
    if os.path.isdir(full_path):
        for root_dir, _, files in os.walk(full_path):
            rel_dir = os.path.relpath(root_dir, project_root)
            for fname in files:
                src = os.path.join(root_dir, fname)
                datas.append((src, rel_dir))

# Bundle .env file if it exists
env_file = os.path.join(project_root, '.env')
if os.path.isfile(env_file):
    datas.append((env_file, '.'))

# ── Analysis ──────────────────────────────────────────────────────────────────
analysis = Analysis(
    ['main.py'],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'test',
        'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    [],
    name='WorkSense',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,      # Silent — no console window
    icon=None,
)
