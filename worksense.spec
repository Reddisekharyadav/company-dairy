# -*- mode: python -*-
import os
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

project_root = os.path.abspath(os.getcwd())

block_cipher = None

datas = []
for base_dir in ('backend/templates', 'backend/static'):
    full_path = os.path.join(project_root, base_dir)
    if os.path.isdir(full_path):
        for root_dir, _, files in os.walk(full_path):
            rel_dir = os.path.relpath(root_dir, project_root)
            for fname in files:
                src = os.path.join(root_dir, fname)
                dest = os.path.join(rel_dir, fname)
                datas.append((src, dest))

hiddenimports = []

# Include pywin32 runtime for Windows
hiddenimports += collect_submodules('win32com')

# Include our ui package (status widget + consent dialog)
hiddenimports += ['ui', 'ui.status_widget']

# Include dynamic imports used inside functions
hiddenimports += ['mss', 'PIL', 'pytesseract', 'io']

analysis = Analysis(
    ['main.py'],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name='WorkSense',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    strip=False,
    upx=True,
    name='WorkSense'
)
