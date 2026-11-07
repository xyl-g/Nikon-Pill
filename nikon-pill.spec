# PyInstaller spec for Nikon Pill (macOS .app; Windows uses nikon-pill-win.spec
# for a one-file console=False EXE). The NikonFirmwarePatch Release toolkit is
# bundled as data under nfp/ and replayed in-process via the --nfp-engine flags.
import os
from pathlib import Path

VENDOR = Path(os.environ.get('NFP_VENDOR_DIR', Path.home() / 'git' / 'NikonFirmwarePatch-toolkit-v0.4.0-rc.2'))
if not (VENDOR / 'patch.py').is_file():
    raise SystemExit(f'NFP toolkit not found at {VENDOR}; set NFP_VENDOR_DIR')

datas = [(str(VENDOR), 'nfp')]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['backend', 'worker', 'ui', 'capstone'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Nikon Pill',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
app = BUNDLE(
    exe,
    a.binaries,
    a.datas,
    name='Nikon Pill.app',
    icon='build/icon.icns',
    bundle_identifier='io.github.xinmiaozhao.nikon-pill',
    info_plist={
        'CFBundleName': 'Nikon Pill',
        'CFBundleDisplayName': 'Nikon Pill',
        'CFBundleShortVersionString': '0.1.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
    },
)
