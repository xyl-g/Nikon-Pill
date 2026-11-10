"""Build a native portrait-video WLM against the exact Z8 cold-retry3 MAIN."""
import argparse
import json
from pathlib import Path
import struct
import subprocess

from build_sdk_module import main as build_frontend
from wlm_bundle import decode_bundle, encode_bundle
from wlm_native import compose_components, encode_component
from z8_native_arena import BASE, STOCK_SHA256, arena_profile, verify_arena
from z8_native_capsule import EXEC, HOOK, IMAGE, INSTRUCTION, WRITE, Patch, _word_branch, encode, sha

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'modules/portrait_video'
LLVM = Path('C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/Llvm/x64/bin')
FOUNDATION = '0e0114267ef82caf78b7795a470192b434528ec0f4a8a6b031d1a003c4c63dfd'
MODULE = 'portrait_video'
SELECTOR = 1 << 6
TARGET = bytes.fromhex(STOCK_SHA256)
SLOT = 0x40be0000
HOOKS = {
    # First video sample of a file; the generic create at 0x40cebe38 is not
    # used by the movie recorder (live counters, 2026-11-10).
    0x40ce42dc: ('portrait_sample_hook', 0xb9455e88, False),
    0x40ceefdc: ('portrait_close_hook', 0x94000d93, True),
    0x40cefd60: ('portrait_tkhd_hook', 0x97ffd09f, True),
}


def build(stock_path, foundation_path, output, llvm):
    stock, foundation = stock_path.read_bytes(), foundation_path.read_bytes()
    verify_arena(stock)
    if sha(foundation) != FOUNDATION:
        raise ValueError('exact cold-retry3 MAIN required')
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'build'):
        raise ValueError('new output under build/ required')
    output.mkdir(parents=True)

    def run(name, *args):
        path = llvm / name
        if path.with_suffix('.exe').is_file():
            path = path.with_suffix('.exe')
        return subprocess.check_output([str(path), *map(str, args)], text=True, timeout=60)

    arch = '--target=aarch64-none-elf'
    run('clang', arch, '-O2', '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
        '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
        '-mstrict-align', '-mgeneral-regs-only', '-mno-outline-atomics', '-I', ROOT / 'include',
        '-c', SOURCE / 'native.c', '-o', output / 'native.o')
    run('clang', arch, '-c', SOURCE / 'hooks.S', '-o', output / 'hooks.o')
    elf = output / 'native.elf'
    run('ld.lld', '-m', 'aarch64elf', '--build-id=none', '-z', 'max-page-size=4096',
        '-T', SOURCE / 'native.ld', '--defsym=portrait_sample_resume=0x40ce42e0',
        '--defsym=portrait_stock_reset=0x40cf2628', '--defsym=portrait_stock_copy=0x40ce3fdc',
        output / 'native.o', output / 'hooks.o', '-o', elf)
    symbols = {parts[2]: int(parts[0], 16) for line in run('llvm-nm', '-n', elf).splitlines()
               if len(parts := line.split()) == 3}
    data = elf.read_bytes()
    entry = symbols['fx_native_pack_entry']
    kind, machine, _, elf_entry, table, _, _, _, stride, count, *_ = struct.unpack_from('<HHIQQQIHHHHHH', data, 16)
    if (data[:7], kind, machine, elf_entry, stride, count) != (b'\x7fELF\x02\x01\x01', 2, 183, entry, 56, 3):
        raise ValueError('unexpected fixed AArch64 ELF layout')

    patches = []
    for i, (permission, flags) in enumerate(((5, EXEC), (4, 0), (6, WRITE))):
        kind, actual, at, va, pa, n, size, align = struct.unpack_from('<II6Q', data, table + i*stride)
        if ((kind, actual, va, pa, align) != (1, permission, SLOT + i*4096, SLOT + i*4096, 4096) or
                not 0 < size <= 4096 or n > size or at + n > len(data) or (i == 0 and not va <= entry < va+n)):
            raise ValueError('native image out of its reserved slot')
        old = foundation[va-BASE:va-BASE+size]
        if old != bytes(size):
            raise ValueError('native slot is occupied')
        patches.append(Patch(IMAGE, flags, va, old, data[at:at+n].ljust(size, b'\0')))

    for address, (name, word, link) in HOOKS.items():
        old = struct.pack('<I', word)
        offset = address - BASE
        if stock[offset:offset+4] != old or foundation[offset:offset+4] != old:
            raise ValueError(f'video hook preimage differs at {address:#x}')
        target = symbols[name]
        if not SLOT <= target < SLOT + len(patches[0].new):
            raise ValueError(f'hook target outside code: {name}')
        new = _word_branch(address, target)
        if link:
            new = struct.pack('<I', struct.unpack('<I', new)[0] | 0x80000000)
        patches.append(Patch(HOOK, INSTRUCTION, address, old, new))

    required = arena_profile()['requires_id']
    identity = int.from_bytes(bytes.fromhex(sha(data))[:8], 'little') or 1
    capsule = encode(patches, identity, required, entry)
    component = encode_component(MODULE, 1, SELECTOR, 96, [dict(mask=SELECTOR, value=SELECTOR, capsule=capsule)],
                                 target=TARGET, requires_id=required)
    frontend = output / 'frontend'
    build_frontend([str(SOURCE / 'module.c'), '--id', MODULE, '--target', 'z8-c311',
                    '--output-dir', str(frontend), '--llvm-bin', str(llvm), '--optimize', 's'])
    name = MODULE + '.wlm'
    bundle = encode_bundle((frontend / name).read_bytes(), MODULE, [component], TARGET)
    parsed = decode_bundle(bundle, module_id=MODULE, target=TARGET)
    plan, _ = compose_components([parsed['components'][0]['data']], target=TARGET, requires_id=required)
    (output / name).write_bytes(bundle)
    (output / (MODULE + '.fnc')).write_bytes(plan)
    report = dict(module=MODULE, wlm_bytes=len(bundle), wlm_sha256=sha(bundle), main_sha256=FOUNDATION,
                  stock_sha256=STOCK_SHA256, plan_sha256=sha(plan), native_records=len(patches),
                  symbols=symbols, physical_recording_verified=False)
    (output / 'build.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, default=ROOT / 'inputs/z8-c311-main.bin')
    parser.add_argument('--foundation', type=Path, required=True)
    parser.add_argument('--llvm-bin', type=Path, default=LLVM)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.stock, args.foundation, args.output, args.llvm_bin.resolve()), indent=2))
