"""Build the exact cold-retry3 Z8 C3.11 foundation locally on Windows."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))

import build_z8_core
from cipher_patch import apply_patch, atomic_new
from compiler_dependencies import identity_configuration
from verify_z8_core import verify
from z8_container import extract
from z8_repack import produce

BUILD_IDENTITY = 'f06231d84970cda74ff6afcdb2aad7d03f6fe0bc4a3ec76c15a9cfd40654579a'
MAIN_SHA256 = '0e0114267ef82caf78b7795a470192b434528ec0f4a8a6b031d1a003c4c63dfd'
FIRMWARE_SHA256 = '0a9cdd079ab09136982a7bbcb2f3c0ffbe64a12f79cff27d5d5a5da833d7ee43'
REFERENCE_CLANG = (
    'clang version 19.1.5\n'
    'Target: x86_64-pc-windows-msvc\n'
    'Thread model: posix\n'
    'InstalledDir: C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Tools\\Llvm\\x64\\bin\n'
)


def cold_retry3_identity(root, output, inputs, configuration):
    version, target, threads, *_ = configuration['compiler'].splitlines()
    if (not re.fullmatch(r'clang version 19\.1\.5(?: .*)?', version)
            or target != 'Target: x86_64-pc-windows-msvc' or threads != 'Thread model: posix'):
        raise ValueError('cold-retry3 requires Windows x64 Clang 19.1.5')
    # The historical identity includes the original toolchain's banner and
    # install path. Keep that metadata canonical across local install paths.
    config = {**configuration, 'compiler': REFERENCE_CLANG}
    return identity_configuration(root, output, inputs, config)


def build(firmware, llvm, output):
    firmware = firmware.resolve(strict=True)
    llvm = llvm.resolve(strict=True)
    output = output.resolve()
    if os.name != 'nt':
        raise ValueError('this exact recipe requires Windows')
    if not output.is_relative_to(ROOT / 'build') or output == ROOT / 'build' or output.exists():
        raise ValueError('choose a new output directory below build/')
    nm = llvm / 'llvm-nm.exe'
    for name in ('clang.exe', 'ld.lld.exe', 'llvm-objcopy.exe', nm.name):
        if not (llvm / name).is_file():
            raise FileNotFoundError(llvm / name)
    output.mkdir(parents=True)
    stock = output / 'stock.main'
    core = output / 'core'
    container = ROOT / 'vendor/NikonFirmwarePatch-container'
    extract(firmware, stock, external_project=container)

    with patch.object(build_z8_core, 'identity_configuration', cold_retry3_identity):
        report = build_z8_core.build(stock, core, llvm_bin=llvm, native_capsules=True)
    if report['debug_build_identity'] != BUILD_IDENTITY or report['candidate_sha256'] != MAIN_SHA256:
        raise ValueError('built MAIN differs from cold-retry3; check source and LLVM 19.1.5')

    validation = core / 'verification.json'
    verified = verify(stock, core, nm)
    atomic_new(validation, (json.dumps(verified, indent=2) + '\n').encode())
    patch_file = output / 'cold-retry3.fxpatch'
    # The repacker reruns the independent verifier and looks up llvm-nm on PATH.
    with patch.dict(os.environ, PATH=str(llvm) + os.pathsep + os.environ.get('PATH', '')):
        repacked = produce(firmware, core, validation, container, output / 'repack', patch_file)
    if repacked['result_sha256'] != FIRMWARE_SHA256:
        raise ValueError('repacked firmware differs from cold-retry3')

    result = output / 'Z_8_0311.bin'
    apply_patch(firmware, patch_file, result)
    if hashlib.sha256(result.read_bytes()).hexdigest() != FIRMWARE_SHA256:
        raise ValueError('cold-retry3 output readback differs')
    return dict(firmware=str(result), sha256=FIRMWARE_SHA256, build_identity=BUILD_IDENTITY)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--llvm-bin', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'build/cold-retry3')
    args = parser.parse_args()
    print(json.dumps(build(args.firmware, args.llvm_bin, args.output_dir), indent=2))


if __name__ == '__main__':
    main()
