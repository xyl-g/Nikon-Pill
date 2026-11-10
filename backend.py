"""Toolkit discovery and subprocess wrapping for two build backends.

- kind='nfp'      NikonFirmwarePatch: patch.py composes feature groups with a
                  user-chosen version/tag (prebuilt units bundled under nfp/).
- kind='fenestra' Fenestra-Z8-C311-source: patcher.py applies a pinned
                  cold-retry3.fxpatch to the stock firmware (stdlib-only,
                  no version/feature selection; result is content-pinned).

The GUI never imports either upstream codebase: everything runs through
subprocesses. Frozen apps re-invoke themselves via --nfp-engine-* flags.
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

# Fenestra pinned assets (wlm-2026.11.10-r10 / cold-retry3-source)
FENESTRA_INPUT_SHA = 'd67413c2ede6b9ce8887175f20700bd9fbad2a973a4820968c45503d15d80d96'
FENESTRA_PATCH_SHA = '395b05e5db84c464e6cb44b178623cf5d237cd9aef5d8ed9ad0b8cfc244758a1'
FENESTRA_RESULT_SHA = '0a9cdd079ab09136982a7bbcb2f3c0ffbe64a12f79cff27d5d5a5da833d7ee43'


def bundled_toolkit() -> Path | None:
    """nfp/ data directory inside a frozen app (PyInstaller --add-data)."""
    if not FROZEN:
        return None
    base = Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
    path = base / 'nfp'
    return path if (path / 'patch.py').is_file() else None


def bundled_fenestra() -> Path | None:
    """fenestra/ data directory inside a frozen app."""
    if not FROZEN:
        return None
    base = Path(getattr(sys, '_MEIPASS', Path(sys.executable).resolve().parent))
    path = base / 'fenestra'
    return path if (path / 'patcher.py').is_file() else None


@dataclass
class ToolkitInfo:
    kind: str                # 'nfp' | 'fenestra'
    root: Path
    engine_py: Path          # patch.py (nfp) or patcher.py (fenestra)
    verify_py: Path          # verify.py (nfp) or None
    features: list = field(default_factory=list)   # nfp feature groups
    mode: str = 'frozen'     # 'frozen' | 'prebuilt' | 'source'
    compiler: str = ''
    patch_file: Path | None = None                 # fenestra .fxpatch
    notes: list = field(default_factory=list)

    @property
    def ready(self) -> bool:
        if self.kind == 'fenestra':
            return self.patch_file is not None and self.patch_file.is_file()
        if self.mode in ('frozen', 'prebuilt'):
            return True
        return bool(self.compiler)


def _detect_nfp(root: Path, notes: list) -> ToolkitInfo:
    patch_py, verify_py, features_py = root / 'patch.py', root / 'verify.py', root / 'features.py'
    for path in (patch_py, verify_py, features_py, root / 'resources' / 'combinations.json'):
        if not path.is_file():
            raise FileNotFoundError(f'NFP 工具包不完整：缺少 {path.name}')
    match = FEATURES_RE.search(features_py.read_text(encoding='utf-8'))
    if not match:
        raise ValueError('无法从 features.py 解析 FEATURES 定义')
    features = [f.strip().strip('\'"') for f in match.group(1).split(',') if f.strip().strip('\'"')]

    resources = root / 'resources'
    prebuilt = []
    compiled_json = resources / 'compiled.json'
    if compiled_json.is_file():
        try:
            units = json.loads(compiled_json.read_text(encoding='utf-8'))['units']
            prebuilt = [u for u in units if (resources / f'{u}.bin').is_file()]
        except (ValueError, KeyError):
            pass

    bundled = bundled_toolkit() is not None
    if bundled:
        mode, notes_mode = 'frozen', '内置完整工具包（预编译单元随应用分发）'
        notes.append(notes_mode)
        return ToolkitInfo(kind='nfp', root=root, engine_py=patch_py, verify_py=verify_py,
                           features=features, mode=mode, notes=notes)
    if prebuilt:
        notes.append(f'预编译补丁单元 {len(prebuilt)} 个：构建无需编译器')
        return ToolkitInfo(kind='nfp', root=root, engine_py=patch_py, verify_py=verify_py,
                           features=features, mode='prebuilt', notes=notes)
    clang = shutil.which('clang') or shutil.which('clang.exe') or ''
    if clang:
        try:
            line = subprocess.check_output([clang, '--version'], text=True,
                                           stderr=subprocess.DEVNULL).splitlines()[0]
        except (OSError, subprocess.CalledProcessError):
            line = clang
        notes.append('源码模式：' + line)
        return ToolkitInfo(kind='nfp', root=root, engine_py=patch_py, verify_py=verify_py,
                           features=features, mode='source', compiler=clang, notes=notes)
    notes.append('源码模式：未找到 clang，无法构建（请安装 LLVM/clang 或改用完整 Release 工具包）')
    return ToolkitInfo(kind='nfp', root=root, engine_py=patch_py, verify_py=verify_py,
                       features=features, mode='source', notes=notes)


def _find_fenestra_patch(root: Path) -> Path | None:
    """Locate a bundled .fxpatch: repo root, releases/, or assets/ subdir."""
    candidates = [root / 'cold-retry3.fxpatch', *root.glob('releases/*.fxpatch'),
                  *root.glob('assets/*.fxpatch'), *root.glob('*.fxpatch')]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _detect_fenestra(root: Path, notes: list) -> ToolkitInfo:
    patcher_py = root / 'patcher.py'
    if not patcher_py.is_file():
        raise FileNotFoundError(f'Fenestra 工具包不完整：缺少 patcher.py（{root}）')
    mode = 'frozen' if bundled_fenestra() is not None else 'source'
    patch = _find_fenestra_patch(root)
    if patch is None:
        notes.append('未找到 .fxpatch 补丁文件；请将 cold-retry3.fxpatch 放入工具包目录')
        return ToolkitInfo(kind='fenestra', root=root, engine_py=patcher_py,
                           verify_py=None, mode=mode, notes=notes)
    digest = _sha256_file(patch)
    if digest == FENESTRA_PATCH_SHA:
        notes.append('cold-retry3.fxpatch 校验一致（官方 r10 基线）')
    else:
        notes.append(f'警告：fxpatch SHA-256 非官方基线（{digest[:12]}…），产物将不做预校验')
    info = ToolkitInfo(kind='fenestra', root=root, engine_py=patcher_py, verify_py=None,
                       mode=mode, patch_file=patch, notes=notes)
    if digest == FENESTRA_PATCH_SHA:
        info.notes.append(f'预期产物 SHA-256 {FENESTRA_RESULT_SHA[:12]}…')
    return info


def _sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def detect_toolkit(root=None, kind: str = 'nfp') -> ToolkitInfo:
    """Detect the chosen toolkit. Frozen mode wins when bundled data exists."""
    notes: list = []
    if kind == 'fenestra':
        bundled = bundled_fenestra()
        if bundled is not None:
            return _detect_fenestra(bundled, notes)
        if root is None:
            raise FileNotFoundError('未选择 Fenestra 工具包目录')
        return _detect_fenestra(Path(root).expanduser(), notes)

    bundled = bundled_toolkit()
    if bundled is not None:
        return _detect_nfp(bundled, notes)
    if root is None:
        raise FileNotFoundError('未选择 NikonFirmwarePatch 工具包目录')
    return _detect_nfp(Path(root).expanduser(), notes)


def output_name(version: str) -> str:
    """Same naming rule as patch.py: --version 04.06 -> Z_8_0406.bin."""
    return 'Z_8_' + version.replace('.', '') + '.bin'


def _engine_cmd(info: ToolkitInfo, script: str, args: list) -> list:
    if info.mode == 'frozen':
        return [sys.executable, '--nfp-engine-' + script, *args]
    return [sys.executable, '-B', str(info.engine_py), *args]


def check_cmd(info: ToolkitInfo, firmware: Path) -> list:
    if info.kind == 'fenestra':
        # patcher.py has no read-only check; verify by hashing the input.
        return None
    return _engine_cmd(info, 'patch', [str(firmware), '--check'])


def build_cmd(info: ToolkitInfo, firmware: Path, features=(), version: str = '',
              tag: str = '', output_dir: Path | None = None,
              output_bin: Path | None = None) -> list:
    if info.kind == 'fenestra':
        return _engine_cmd(info, 'fenestra',
                           ['--firmware', str(firmware),
                            '--patch', str(info.patch_file),
                            '--output', str(output_bin)])
    return _engine_cmd(info, 'patch', [str(firmware), '--features', *features,
                                       '--version', version, '--tag', tag,
                                       '--output', str(output_dir)])


def verify_cmd(info: ToolkitInfo, firmware: Path, output_bin: Path, features=(),
               version: str = '', tag: str = '') -> list | None:
    if info.kind == 'fenestra':
        return None    # verification = compare content SHA-256 against pinned value
    return _engine_cmd(info, 'verify', [str(firmware), str(output_bin),
                                        '--features', *features,
                                        '--version', version, '--tag', tag])


def run_streamed(cmd, cwd: Path, on_line):
    """Run a subprocess, forwarding every stdout/stderr line through on_line."""
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
    """patch.py / patcher.py print a JSON document as the last stdout block."""
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
    import argparse
    ap = argparse.ArgumentParser(description='backend self-test without the GUI')
    ap.add_argument('--kind', choices=['nfp', 'fenestra'], default='nfp')
    ap.add_argument('--toolkit', default=str(Path.home() / 'git' / 'NikonFirmwarePatch-toolkit-v0.4.0-rc.2'))
    ap.add_argument('--firmware', default=str(Path.home() / 'Downloads' / 'Z_8_0311.bin'))
    a = ap.parse_args()
    info = detect_toolkit(a.toolkit, kind=a.kind)
    print(json.dumps(dict(kind=info.kind, root=str(info.root), features=info.features,
                          mode=info.mode, patch=str(info.patch_file), ready=info.ready,
                          notes=info.notes), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    self_test()
