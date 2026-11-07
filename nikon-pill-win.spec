# PyInstaller spec for Nikon Pill on Windows: one-file windowed EXE with the
# NikonFirmwarePatch Release toolkit bundled as data under nfp/.
import os
from pathlib import Path

VENDOR = Path(os.environ.get('NFP_VENDOR_DIR', Path.home() / 'git' / 'NikonFirmwarePatch-toolkit-v0.4.0-rc.2'))
if not (VENDOR / 'patch.py').is_file():
    raise SystemExit(f'NFP toolkit not found at {VENDOR}; set NFP_VENDOR_DIR')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[(str(VENDOR), 'nfp')],
    hiddenimports=['backend', 'worker', 'ui', 'capstone'],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NikonPill',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon='build/icon.ico',
    append_to_toc_errors=False,
)
