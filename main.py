"""nfp-ui entry point: GUI, or bundled NFP engine mode when frozen.

The PyInstaller build bundles the NikonFirmwarePatch toolkit as data under
``nfp/``. A frozen app re-invokes itself with ``--nfp-engine-patch`` /
``--nfp-engine-verify`` to run patch.py/verify.py headless (same
self-relaunch pattern as multiprocessing), so end users need neither a
Python install nor a compiler.
"""
import sys

ENGINE_FLAGS = ('--nfp-engine-patch', '--nfp-engine-verify', '--nfp-engine-fenestra')


def engine_base():
    from pathlib import Path
    return Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))


def run_engine() -> int:
    import runpy
    base = engine_base()
    flag = sys.argv[1]
    if flag == '--nfp-engine-fenestra':
        script, name = base / 'fenestra' / 'patcher.py', 'fenestra'
    else:
        script, name = base / 'nfp' / ('verify.py' if flag == '--nfp-engine-verify' else 'patch.py'), 'nfp'
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script)] + sys.argv[2:]
    try:
        runpy.run_path(str(script), run_name='__main__')
    except SystemExit as exc:      # patch.py/verify.py/patcher.py signal failure this way
        code = exc.code
        return code if isinstance(code, int) else (1 if code else 0)
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ENGINE_FLAGS:
        sys.exit(run_engine())
    from PySide6.QtWidgets import QApplication
    from ui import APP_NAME, MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
