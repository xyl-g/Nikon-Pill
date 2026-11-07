"""End-to-end smoke: check firmware, build into a timestamped subdir, auto-verify."""
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from backend import FROZEN
from ui import MainWindow

SHOT = Path(__file__).resolve().parent / 'build' / 'smoke-window.png'
BASE = Path('/Users/xiaoyang/Downloads/nfp-out-base')   # 故意用已存在的目录


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    print('base dir exists (on purpose):', BASE.exists())
    app = QApplication(sys.argv)
    win = MainWindow()
    if not FROZEN:
        win._tk_dir = '/Users/xiaoyang/git/NikonFirmwarePatch-toolkit-v0.4.0-rc.2'
        win._refresh_toolkit()
    win.out_edit.setText(str(BASE))
    win.version_edit.setText('04.06')
    win.tag_edit.setText('p0')
    win._set_all(True)
    win.resize(1000, 900)
    win.show()
    win._set_firmware(Path('/Users/xiaoyang/Downloads/Z_8_0311.bin'))

    started = {'build': False}

    def kick_build():
        if win._check_ok and not started['build']:
            started['build'] = True
            print('check ok, build enabled:', win.build_btn.isEnabled())
            win._start_build()

    def poll():
        kick_build()
        if win.worker is None and started['build'] and win._identity:
            win.grab().save(str(SHOT))
            out = win._build_bin
            print('build dir :', win._build_dir)
            print('bin exists:', out.is_file(), out.stat().st_size if out.is_file() else 0)
            print('identity  :', win._identity)
            print('sha256    :', win._sha)
            print('verify    :', 'PASS' if 'PASS' in win.result_title.text() else win.result_title.text())
            print('files     :', sorted(p.name for p in win._build_dir.iterdir()))
            print('saved', SHOT)
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(1000)
    QTimer.singleShot(300000, app.quit)   # hard stop
    app.exec()


if __name__ == '__main__':
    main()
