"""Build the independent Z8 C3.11 card WAV WLM for the cold-retry3 foundation."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys

from build_sdk_module import main as build_frontend
from wlm_bundle import decode_bundle, encode_bundle
from wlm_native import compose_components, encode_component
from z8_native_arena import BASE, INIT_FILE_BEGIN, INIT_FILE_END, INIT_RAM_BEGIN, arena_profile, verify_arena
from z8_native_capsule import DATA, EXEC, HOOK, IMAGE, INSTRUCTION, POINTER, WRITE, Patch, _word_branch, decode, encode, sha
from z8_native_profile import STOCK_SHA256

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor/NikonFirmwarePatch-container'
sys.path.insert(0, str(VENDOR))
from z8cardwav.wlm_hooks import CLASSIFIER_HOOKS, ENTRY_HOOKS, generate

NATIVE = VENDOR / 'z8cardwav/firmware'
LLVM = Path('C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/Llvm/x64/bin')
FOUNDATION_SHA = '0e0114267ef82caf78b7795a470192b434528ec0f4a8a6b031d1a003c4c63dfd'
MODULE = 'card_wav'
SELECTOR = 1 << 5
TEXT_HOOK = 0x4032e130
HOLDER = 0xa028cb030
SLOT = 0x40940000
LIMIT = SLOT + 0x10000
ROWS = 0x4094b000
EVENTS = ROWS + 0x200
LABEL_SCAN = 0x1f00
LABEL_END = LABEL_SCAN + 21
TARGET = bytes.fromhex(STOCK_SHA256)
FLAGS = ['--target=aarch64-none-elf', '-O2', '-Wall', '-Wextra', '-Werror', '-ffreestanding',
         '-fno-builtin', '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
         '-mgeneral-regs-only', '-mstrict-align', '-fno-pic', '-march=armv8-a', '-mno-outline-atomics',
         '-DM2_CONFIRM_IMPORT=1', '-DM2_CONFIRM_RETRY=1', '-DM2_STAGED_CONFIRM=1']


def read_at(image, address, size):
    if INIT_RAM_BEGIN <= address < INIT_RAM_BEGIN + INIT_FILE_END - INIT_FILE_BEGIN:
        offset = INIT_FILE_BEGIN + address - INIT_RAM_BEGIN
    else:
        offset = address - BASE
    if not 0 <= offset <= len(image) - size:
        raise ValueError(f'MAIN window outside image at {address:#x}')
    return image[offset:offset + size]


def branch_target(site, code):
    word, = struct.unpack('<I', code)
    if word >> 26 != 0b000101:
        raise ValueError('expected existing foundation text branch')
    offset = (word & 0x3ffffff) - ((word >> 25 & 1) << 26)
    return site + offset * 4


def build_native(stock, foundation, output, llvm):
    def run(name, *args):
        path = llvm / name
        tool = path.with_suffix('.exe') if path.with_suffix('.exe').is_file() else path
        return subprocess.run([str(tool), *map(str, args)], check=True, stdout=subprocess.PIPE,
                              text=True, encoding='utf-8', timeout=60).stdout

    source = output / 'hooks.S'
    source.write_text(generate(lambda address, size: read_at(stock, address, size)), encoding='ascii')
    text_target = branch_target(TEXT_HOOK, read_at(foundation, TEXT_HOOK, 4))
    objects = []
    for path in [*sorted(NATIVE.glob('*.c')), source]:
        obj = output / (path.stem + '.o')
        flags = FLAGS if path.suffix == '.c' else FLAGS[:1]
        run('clang', *flags, '-I', NATIVE, '-I', ROOT / 'include', '-c', path, '-o', obj)
        objects.append(obj)
    elf = output / 'adapter.elf'
    definitions = [f'--defsym=continue_{name}={address + 4:#x}' for name, address in ENTRY_HOOKS.items()]
    definitions += [f'--defsym=continue_class_{i}={address + 4:#x}' for i, address in enumerate(CLASSIFIER_HOOKS)]
    run('ld.lld', '-m', 'aarch64elf', '--build-id=none', '-z', 'max-page-size=4096',
        '-T', NATIVE / 'wlm.ld', f'--defsym=foundation_text={text_target:#x}', *definitions, *objects, '-o', elf)
    if run('llvm-nm', '-u', elf).strip():
        raise ValueError('unresolved native symbols')
    symbols = run('llvm-nm', '-n', elf)
    (output / 'symbols.txt').write_text(symbols, encoding='ascii')
    layouts = [run('clang', *FLAGS, '-Xclang', '-fdump-record-layouts', '-fsyntax-only', NATIVE / f'{unit}.c')
               for unit in ('adapter', 'picker')]
    (output / 'record-layouts.txt').write_text(''.join(layouts), encoding='utf-8')
    return elf.read_bytes(), {parts[2]: int(parts[0], 16) for line in symbols.splitlines()
                              if len(parts := line.split()) == 3}


def load_segments(elf, symbols):
    header = struct.unpack_from('<HHIQQQIHHHHHH', elf, 16)
    kind, machine, _, entry, phoff, _, _, _, phsize, phnum, *_ = header
    if (elf[:7], kind, machine, entry, phsize, phnum) != (
            b'\x7fELF\x02\x01\x01', 2, 183, symbols['fx_native_pack_entry'], 56, 3):
        raise ValueError('unexpected native ELF contract')
    end = SLOT
    for i, (permissions, flags) in enumerate(((5, EXEC), (4, 0), (6, WRITE))):
        kind, actual, offset, address, physical, file_size, size, alignment = struct.unpack_from('<II6Q', elf, phoff + i * phsize)
        bound = ROWS if i < 2 else LIMIT
        if ((kind, actual, physical, alignment) != (1, permissions, address, 4096) or
                address % 4096 or not end <= address < address + size <= bound or
                not 0 < file_size <= size or offset + file_size > len(elf)):
            raise ValueError('invalid native load segment or page permissions')
        if (i == 0 and (address != SLOT or not address <= entry < address + size)) or (
                i == 2 and (address != SLOT + 0xd000 or address + size != LIMIT)):
            raise ValueError('native segment outside its slot')
        end = (address + size + 4095) & ~4095
        yield address, flags, elf[offset:offset + file_size].ljust(size, b'\0')


def native_plan(stock, foundation, elf, symbols):
    patches = []

    def add(kind, flags, address, new):
        old = read_at(foundation, address, len(new))
        if address != TEXT_HOOK and old != read_at(stock, address, len(new)):
            raise ValueError(f'foundation owns {address:#x}')
        if kind == IMAGE and any(old):
            raise ValueError(f'native slot not empty at {address:#x}')
        if old != new:
            patches.append(Patch(kind, flags, address, old, new))

    for address, flags, raw in load_segments(elf, symbols):
        add(IMAGE, flags, address, raw)

    builtin = read_at(stock, 0x43d64d94, 5 * 44)
    row = bytearray(builtin[-44:])
    struct.pack_into('<H', row, 0, LABEL_SCAN)
    struct.pack_into('<I', row, 24, 0x7e20)
    add(IMAGE, 0, ROWS, builtin + row)
    events = read_at(stock, 0x43d64e70, 5 * 8)
    add(IMAGE, 0, EVENTS, events + 21 * events[:8])

    for index, pc in enumerate(CLASSIFIER_HOOKS):
        add(HOOK, INSTRUCTION, pc, _word_branch(pc, symbols[f'hook_class_{index}']))

    for name, pc in ENTRY_HOOKS.items():
        add(HOOK, INSTRUCTION, pc, _word_branch(pc, symbols['hook_' + name]))
    add(HOOK, INSTRUCTION, TEXT_HOOK, _word_branch(TEXT_HOOK, symbols['mark2_discovery_label']))
    pc = 0x405a5618
    word, = struct.unpack('<I', _word_branch(pc, symbols['mark2_outer_type_text']))
    add(HOOK, INSTRUCTION, pc, struct.pack('<I', word | 0x80000000))
    for address, target in ((HOLDER + 0x10, ROWS), (HOLDER + 0x18, EVENTS),
                            (0xa028ed100, symbols['mark2_sound_refresh']),
                            *((a, symbols['mark2_discovery_submit']) for a in (0x43d9ee68, 0x43d9ee88, 0x43d9ef28))):
        add(HOOK, POINTER, address, struct.pack('<Q', target))
    for address, value in ((HOLDER + 0x20, 6), (HOLDER + 0x28, 416), (HOLDER + 0x30, 416)):
        add(HOOK, DATA, address, struct.pack('<H', value))
    return sorted(patches, key=lambda p: (p.kind, p.address))


def build(stock_path, foundation_path, output, llvm):
    stock, foundation = stock_path.read_bytes(), foundation_path.read_bytes()
    verify_arena(stock)
    if sha(foundation) != FOUNDATION_SHA:
        raise ValueError('requires the exact cold-retry3 foundation MAIN')
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'build'):
        raise ValueError('new output directory under build/ required')
    output.mkdir(parents=True)
    elf, symbols = build_native(stock, foundation, output, llvm)
    patches = native_plan(stock, foundation, elf, symbols)
    required = arena_profile()['requires_id']
    package_id = int.from_bytes(bytes.fromhex(sha(elf))[:8], 'little') or 1
    capsule = encode(patches, package_id, required, symbols['fx_native_pack_entry'])
    component = encode_component(MODULE, 1, SELECTOR, 95,
                                 [dict(mask=SELECTOR, value=SELECTOR, capsule=capsule)],
                                 target=TARGET, requires_id=required)
    frontend_dir = output / 'frontend'
    build_frontend([str(ROOT / 'modules/card_wav/module.c'), '--id', MODULE, '--target', 'z8-c311',
                    '--output-dir', str(frontend_dir), '--llvm-bin', str(llvm), '--optimize', 's'])
    bundle = encode_bundle((frontend_dir / (MODULE + '.wlm')).read_bytes(), MODULE, [component], TARGET)
    parsed = decode_bundle(bundle, module_id=MODULE, target=TARGET)
    plan, meta = compose_components([parsed['components'][0]['data']], target=TARGET, requires_id=required)
    merged = decode(plan)
    if [(p.kind, p.flags, p.address, p.old, p.new) for p in merged['patches']] != [
            (p.kind, p.flags, p.address, p.old, p.new) for p in patches]:
        raise ValueError('composed WLM changed the native plan')
    (output / 'card_wav.fnc').write_bytes(plan)
    (output / 'card_wav.wlm').write_bytes(bundle)
    manifest = dict(module=MODULE, wlm_bytes=len(bundle), wlm_sha256=sha(bundle), main_sha256=FOUNDATION_SHA,
                    stock_sha256=STOCK_SHA256, plan_sha256=sha(plan), native_records=len(patches),
                    native_entries=meta['entries'], label_range=[LABEL_SCAN, LABEL_END],
                    foundation_text=hex(symbols['foundation_text']), physical_qualified=False)
    (output / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, default=ROOT / 'inputs/z8-c311-main.bin')
    parser.add_argument('--foundation', type=Path, required=True)
    parser.add_argument('--llvm-bin', type=Path, default=LLVM)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.stock, args.foundation, args.output, args.llvm_bin.resolve()), indent=2))


if __name__ == '__main__':
    main()
