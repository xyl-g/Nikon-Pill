"""NFP toolkit discovery and subprocess wrapping.

The GUI never imports the upstream NikonFirmwarePatch code: everything runs
through ``patch.py`` / ``verify.py`` as subprocesses. Two layouts are
supported and auto-detected:

- frozen app (DMG/EXE): the Release toolkit ships as data under ``nfp/`` and
  the app re-invokes itself with ``--nfp-engine-patch`` (main.py engine mode);
- source tree: a Git clone or extracted Release toolkit directory is run with
  ``sys.executable patch.py``, like before.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

VERSION_RE = re.compile(r'[0-9]{2}\.[0-9]{2}')
TAG_RE = re.compile(r'[a-z][0-9]')
FEATURES_RE = re.compile(r'FEATURES\s*=\s*\(([^)]*)\)')

FROZEN = getattr(sys, 'frozen', False)


def bundled_toolkit() -> Path | None:
    """nfp/ data directory inside a frozen app (PyInstaller --add-data)."""
    if not FROZEN:
        return None
    base = Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
    path = base / 'nfp'
    return path if (path / 'patch.py').is_file() else None


@dataclass
class ToolkitInfo:
    root: Path
    patch_py: Path
    verify_py: Path
    features: list            # feature-group ids in upstream order
    mode: str                 # 'prebuilt' | 'source' | 'frozen'
    compiler: str             # clang path when source mode, else ''
    notes: list = field(default_factory=list)

    @property
    def ready(self):
        if self.mode == 'prebuilt':
            return True
        if self.mode == 'frozen':
            return True
        return bool(self.compiler)


def detect_toolkit(root=None) -> ToolkitInfo:
    """Locate patch.py/verify.py, read the feature groups and pick the build mode.

    Frozen mode always wins when bundled data exists. A git clone ships no
    ``resources/*.bin`` units and therefore needs clang in source mode; the
    Release toolkit ZIP carries them and builds without one.
    """
    notes = []
    bundled = bundled_toolkit()
    if bundled is not None:
        root = bundled
        notes.append('内置完整工具包（预编译单元随应用分发，无需 Python 与编译器）')
    else:
        if root is None:
            root = os.environ.get('NFP_TOOLKIT', '')
        if not root:
            raise FileNotFoundError('未选择 NikonFirmwarePatch 工具包目录')
        root = Path(root).expanduser()

    root = Path(root)
    patch_py = root / 'patch.py'
    verify_py = root / 'verify.py'
    features_py = root / 'features.py'
    combinations = root / 'resources' / 'combinations.json'
    for path in (patch_py, verify_py, features_py, combinations):
        if not path.is_file():
            raise FileNotFoundError(f'工具包不完整：缺少 {path.name}（{path}）')
    match = FEATURES_RE.search(features_py.read_text(encoding='utf-8'))
    if not match:
        raise ValueError('无法从 features.py 解析 FEATURES 定义')
    features = [f.strip().strip('\'"') for f in match.group(1).split(',') if f.strip().strip('\'"')]
    if not features:
        raise ValueError('features.py 中 FEATURES 为空')

    resources = root / 'resources'
    compiled_json = resources / 'compiled.json'
    prebuilt = []
    if compiled_json.is_file():
        try:
            units = json.loads(compiled_json.read_text(encoding='utf-8'))['units']
            prebuilt = [u for u in units if (resources / f'{u}.bin').is_file()]
        except (ValueError, KeyError):
            pass

    if bundled is not None:
        # Frozen engine mode: the parent executable replays patch.py/verify.py
        # bundled as data, so neither sys.executable nor clang is involved.
        mode, compiler = 'frozen', ''
    elif prebuilt:
        mode, compiler = 'prebuilt', ''
        notes.append(f'预编译补丁单元 {len(prebuilt)} 个：构建无需编译器')
    else:
        mode = 'source'
        clang = shutil.which('clang') or shutil.which('clang.exe') or ''
        if clang:
            try:
                line = subprocess.check_output([clang, '--version'], text=True,
                                               stderr=subprocess.DEVNULL).splitlines()[0]
            except (OSError, subprocess.CalledProcessError):
                line = clang
            notes.append('源码模式：' + line)
            compiler = clang
        else:
            notes.append('源码模式：未找到 clang，无法构建（Git 克隆不含预编译单元；'
                         '请安装 LLVM/clang 或改用完整 Release 工具包 ZIP）')
            compiler = ''
    return ToolkitInfo(root=root, patch_py=patch_py, verify_py=verify_py,
                       features=features, mode=mode, compiler=compiler, notes=notes)


def output_name(version: str) -> str:
    """Same naming rule as patch.py: --version 04.06 -> Z_8_0406.bin."""
    return 'Z_8_' + version.replace('.', '') + '.bin'


def _engine_cmd(info: ToolkitInfo, script: str, args: list) -> list:
    if info.mode == 'frozen':
        return [sys.executable, '--nfp-engine-' + script, *args]
    return [sys.executable, '-B', str(info.root / (script + '.py')), *args]


def check_cmd(info: ToolkitInfo, firmware: Path) -> list:
    return _engine_cmd(info, 'patch', [str(firmware), '--check'])


def build_cmd(info: ToolkitInfo, firmware: Path, features, version: str,
              tag: str, output_dir: Path) -> list:
    cmd = _engine_cmd(info, 'patch', [str(firmware),
                                      '--features', *features, '--version', version,
                                      '--tag', tag, '--output', str(output_dir)])
    if info.mode == 'source':
        cmd += ['--code-source', 'source', '--compiler', info.compiler]
    return cmd


def verify_cmd(info: ToolkitInfo, firmware: Path, output_bin: Path, features,
               version: str, tag: str) -> list:
    return _engine_cmd(info, 'verify', [str(firmware), str(output_bin),
                                        '--features', *features,
                                        '--version', version, '--tag', tag])


def run_streamed(cmd, cwd: Path, on_line):
    """Run a subprocess, forwarding every stdout/stderr line through on_line.

    Returns (returncode, stdout_text).
    """
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                  encoding='utf-8', errors='replace', bufsize=1)
    if FROZEN and os.name == 'nt':
        kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    proc = subprocess.Popen(cmd, cwd=str(cwd), **kwargs)
    lines = []
    for line in proc.stdout:
        line = line.rstrip('\n\r')
        lines.append(line)
        on_line(line)
    code = proc.wait()
    return code, '\n'.join(lines)


def parse_tail_json(text: str):
    """patch.py --check / build print a JSON document as the last stdout block."""
    start = text.find('{')
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except ValueError:
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except ValueError:
                continue
    return None


def self_test():
    """CLI sanity pass: detect the toolkit and --check the stock firmware."""
    import argparse
    ap = argparse.ArgumentParser(description='backend self-test without the GUI')
    ap.add_argument('--toolkit', default=str(Path.home() / 'git' / 'NikonFirmwarePatch'))
    ap.add_argument('--firmware', default=str(Path.home() / 'Downloads' / 'Z_8_0311.bin'))
    a = ap.parse_args()
    info = detect_toolkit(a.toolkit)
    print(json.dumps(dict(root=str(info.root), features=info.features, mode=info.mode,
                          compiler=info.compiler, ready=info.ready, notes=info.notes,
                          firmware=a.firmware), ensure_ascii=False, indent=2))
    firmware = Path(a.firmware)
    if not firmware.is_file():
        print(f'-- firmware not found, skipping check: {firmware}')
        return
    print('-- running patch.py --check ...')
    code, out = run_streamed(check_cmd(info, firmware), info.root, print)
    report = parse_tail_json(out)
    print(json.dumps(dict(exit_code=code, status=(report or {}).get('status'),
                          identity=(report or {}).get('identity'),
                          input_sha256=(report or {}).get('input_sha256'),
                          json_parsed=report is not None), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    self_test()
