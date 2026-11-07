# PyInstaller spec for Nikon Pill on Windows: one-file windowed EXE with the
# NikonFirmwarePatch Release toolkit bundled as data under nfp/.
import os
import shutil
from pathlib import Path

VENDOR = Path(os.environ.get('NFP_VENDOR_DIR', Path.home() / 'git' / 'NikonFirmwarePatch-toolkit-v0.4.0-rc.2'))
if not (VENDOR / 'patch.py').is_file():
    raise SystemExit(f'NFP toolkit not found at {VENDOR}; set NFP_VENDOR_DIR')

# Same trim as the macOS spec: engine never reads luts/ or docs/.
STAGE = Path('_nfp_stage')
if STAGE.exists():
    shutil.rmtree(STAGE)
shutil.copytree(VENDOR, STAGE,
                ignore=shutil.ignore_patterns('luts', 'docs', '.git', '__pycache__'))

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
    datas=[(str(STAGE), 'nfp')],
    hiddenimports=['backend', 'worker', 'ui', 'capstone'],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pytest', *QT_EXCLUDES],
    noarchive=False,
)

# excludes only blocks Python imports; PySide6's hook still collects every Qt
# DLL. Prune by destination path: keep Core/Gui/Widgets + zh/en translations.
import re as _re

_DROP_QT = _re.compile(
    r'PySide6[/\\]Qt[/\\]bin[/\\]Qt(?:6?Qml|Quick|Pdf|Svg|'
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
