# PyInstaller spec for Nikon Pill (macOS .app; Windows uses nikon-pill-win.spec
# for a one-file console=False EXE). The NikonFirmwarePatch Release toolkit is
# bundled as data under nfp/ and replayed in-process via the --nfp-engine flags.
import os
import shutil
from pathlib import Path

VENDOR = Path(os.environ.get('NFP_VENDOR_DIR', Path.home() / 'git' / 'NikonFirmwarePatch-toolkit-v0.4.0-rc.2'))
if not (VENDOR / 'patch.py').is_file():
    raise SystemExit(f'NFP toolkit not found at {VENDOR}; set NFP_VENDOR_DIR')

# Copy the toolkit to a staging dir and drop pieces the engine never reads
# (luts/ = post-production LUTs for editors, docs/ = markdown guides; both
# referenced only by README/release-manifest, ~14 MB combined).
STAGE = Path('_nfp_stage')
if STAGE.exists():
    shutil.rmtree(STAGE)
shutil.copytree(VENDOR, STAGE,
                ignore=shutil.ignore_patterns('luts', 'docs', '.git', '__pycache__'))
datas = [(str(STAGE), 'nfp')]

# Unused PySide6 modules: this is a plain QWidget app — no QML/Quick stack,
# no PDF viewer, no network/GL/SVG/DBus/keyboard input contexts.
QT_EXCLUDES = [
    'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtQmlMeta', 'PySide6.QtQmlModels',
    'PySide6.QtQmlWorkerScript', 'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2',
    'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtNetwork', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
    'PySide6.QtDBus', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets',
    'PySide6.QtVirtualKeyboard', 'PySide6.QtTest', 'PySide6.QtDesigner',
    'PySide6.QtUiTools', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebChannel', 'PySide6.QtWebSockets', 'PySide6.QtPositioning',
    'PySide6.QtLocation', 'PySide6.QtBluetooth', 'PySide6.QtNfc',
    'PySide6.QtSerialPort', 'PySide6.QtSensors', 'PySide6.QtCharts',
    'PySide6.QtDataVisualization', 'PySide6.Qt3DCore', 'PySide6.QtSql',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtRemoteObjects',
    'PySide6.QtScxml', 'PySide6.QtStateMachine', 'PySide6.QtTextToSpeech',
    'PySide6.QtXml',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['backend', 'worker', 'ui', 'capstone'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pytest', *QT_EXCLUDES],
    noarchive=False,
)

# excludes only blocks Python imports; PySide6's hook still collects every Qt
# framework binary and all 99 translation files. Prune the collected TOCs by
# destination path: keep QtCore/Gui/Widgets and zh/en translations only.
import re as _re

_DROP_QT = _re.compile(
    r'PySide6/Qt/lib/Qt(?:Qml|Quick|Pdf|Svg|'
    r'VirtualKeyboard|Designer|UiTools|WebEngine|WebChannel|WebSockets|'
    r'Positioning|Location|Bluetooth|Nfc|SerialPort|Sensors|Charts|'
    r'DataVisualization|3D|Sql|Multimedia|RemoteObjects|Scxml|'
    r'StateMachine|TextToSpeech|Xml|Test|Help|PrintSupport)[A-Za-z0-9]*')
_KEEP_QM = _re.compile(r'^(qtbase_(en|zh_CN)|qt_(zh_CN))\.qm$')

def _prune(toc):
    kept = []
    for dest, src, kind in toc:
        if dest.endswith('.qm'):
            if not _KEEP_QM.match(Path(dest).name):
                continue
        elif _DROP_QT.search(dest):
            continue
        kept.append((dest, src, kind))
    return kept

a.binaries = _prune(a.binaries)
a.datas = _prune(a.datas)

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
        'CFBundleShortVersionString': '0.1.1',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
    },
)
