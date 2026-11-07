"""QThread workers running patch.py/verify.py off the UI thread."""
import os
import subprocess
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from backend import (ToolkitInfo, build_cmd, check_cmd, parse_tail_json,
                     verify_cmd)


@dataclass
class CheckResult:
    ok: bool
    report: dict          # parsed --check JSON, or {}
    raw: str


@dataclass
class BuildResult:
    ok: bool
    report: dict          # final build JSON printed by patch.py
    raw: str


@dataclass
class VerifyResult:
    ok: bool
    report: dict
    raw: str


RESULT_BY_KIND = {'check': CheckResult, 'build': BuildResult, 'verify': VerifyResult}


class TaskWorker(QThread):
    line = Signal(str)        # one streamed output line
    done = Signal(object)     # CheckResult / BuildResult / VerifyResult

    def __init__(self, kind, info: ToolkitInfo, firmware, *, features=(),
                 version='', tag='p0', output_dir=None, output_bin=None,
                 parent=None):
        super().__init__(parent)
        self.kind = kind
        self.info = info
        self.firmware = firmware
        self.features = tuple(features)
        self.version = version
        self.tag = tag
        self.output_dir = output_dir
        self.output_bin = output_bin
        self._proc = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self._proc is not None and self._proc.poll() is None:
            try:
                if os.name == 'nt':
                    # terminate() only kills the direct child; the engine may
                    # still be re-running itself, so drop the whole tree.
                    subprocess.run(['taskkill', '/T', '/F', '/PID', str(self._proc.pid)],
                                   capture_output=True)
                else:
                    self._proc.terminate()
            except OSError:
                pass

    def run(self):
        if self.kind == 'check':
            cmd = check_cmd(self.info, self.firmware)
        elif self.kind == 'build':
            cmd = build_cmd(self.info, self.firmware, self.features,
                            self.version, self.tag, self.output_dir)
        else:
            cmd = verify_cmd(self.info, self.firmware, self.output_bin,
                             self.features, self.version, self.tag)
        try:
            kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding='utf-8', errors='replace', bufsize=1)
            if os.name == 'nt':
                kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            self._proc = subprocess.Popen(cmd, cwd=str(self.info.root), **kwargs)
            chunks = []
            for line in self._proc.stdout:
                line = line.rstrip('\n\r')
                chunks.append(line)
                self.line.emit(line)
            code = self._proc.wait()
            raw = '\n'.join(chunks)
            report = parse_tail_json(raw) or {}
            self.done.emit(RESULT_BY_KIND[self.kind](
                ok=code == 0 and not self._cancelled, report=report, raw=raw))
        except OSError as exc:
            self.line.emit(f'[error] {exc}')
            self.done.emit(RESULT_BY_KIND[self.kind](ok=False, report={}, raw=str(exc)))
