"""Build a fixed-address resident EL1 control pack, never flash or execute it."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'host'))
from compiler_dependencies import dependencies
from z8_native_capsule import pinned_native_text_layout
LLVM = Path('C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/Llvm/x64/bin')
SOURCES = ['nativepacks/feature_controls/backend.c', 'nativepacks/feature_controls/photo_backend.c', 'nativepacks/feature_controls/precapture_subscription.c',
           'nativepacks/feature_controls/hooks.S', 'src/suite_video.c', 'src/framing.c',
           'src/photo_tools.c', 'src/memory.c']


def sha(data): return hashlib.sha256(data).hexdigest()


def load_segments(elf):
    if elf[:7] != b'\x7fELF\x02\x01\x01': raise ValueError('ELF64 LE required')
    kind, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phcount, shsize, shcount, shnames = struct.unpack_from('<HHIQQQIHHHHHH', elf, 16)
    if (kind, machine, version, ehsize, phsize, shsize) != (2, 183, 1, 64, 56, 64): raise ValueError('standalone AArch64 ET_EXEC required')
    segments = []
    for i in range(phcount):
        typ, perm, off, va, physical, filesz, memsz, alignment = struct.unpack_from('<IIQQQQQQ', elf, phoff + 56 * i)
        if typ in (2, 3, 7): raise ValueError('no dynamic interpreter/TLS imports')
        if typ != 1 or not memsz: continue
        if perm not in (4, 5, 6) or filesz > memsz or off + filesz > len(elf): raise ValueError('invalid segment')
        if va < 0x408d0000 or va + memsz > 0x40950000: raise ValueError('outside reserved range')
        part = elf[off:off + filesz]
        segments.append(dict(va=va, flags=perm, file_offset=off, file_bytes=filesz, memory_bytes=memsz,
                             alignment=alignment, sha=sha(part), memory_sha256=sha(part + bytes(memsz - filesz))))
    for i in range(shcount):
        _, typ, attr, _, _, size, _, _, _, _ = struct.unpack_from('<IIQQQQIIQQ', elf, shoff + 64 * i)
        if typ in (4, 9) and size: raise ValueError('no runtime relocations')
    if not any(s['flags'] & 1 and s['va'] <= entry < s['va'] + s['file_bytes'] for s in segments): raise ValueError('entry outside RX')
    return entry, segments


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--features', type=lambda v: int(v, 0), default=15)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--llvm', type=Path, default=LLVM)
    args = ap.parse_args()
    if args.features not in range(1, 16): raise ValueError('nonempty F1/V2/L4/P8 subset required')
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    def tool(name):
        p = args.llvm / (name + ('.exe' if sys.platform == 'win32' else ''))
        if not p.is_file(): raise FileNotFoundError(p)
        return str(p)
    cc, ld, nm = (tool(n) for n in ('clang', 'ld.lld', 'llvm-nm'))
    metadata_path = ROOT / 'vendor/NikonFirmwarePatch-features/resources/compiled.json'
    metadata_raw = metadata_path.read_bytes(); native = json.loads(metadata_raw)['units']
    locale = pinned_native_text_layout(args.features)
    pinned = [('pixelshift.psm_worker', int(native['pixelshift']['symbols']['psm_worker']['va'], 0), 8),
              ('quality.tier_star_republish', int(native['quality']['symbols']['tier_star_republish']['va'], 0), 2),
              ('framing.opengate.writer_close_hook', int(native['opengate']['symbols']['writer_close_hook']['va'], 0), 1)]
    if [x[1] for x in pinned] != [0x409a44cc, 0x40a41250, 0x40960870]: raise ValueError('pinned native dependency changed')
    selected = [(n, a) for n, a, bit in pinned if args.features & bit]
    hdr = ['/* Generated from the exact pinned native image metadata. */',
           '#define FX_CONTROL_DEPENDENCY_COUNT ' + str(len(selected)) + 'u',
           '#define FX_CONTROL_PSM_WORKER UINT64_C(' + hex(pinned[0][1] if args.features & 8 else 0) + ')',
           '#define FX_CONTROL_TIER_REPUBLISH UINT64_C(' + hex(pinned[1][1] if args.features & 2 else 0) + ')',
           '#define FX_CONTROL_UPSTREAM_CLOSE UINT64_C(' + hex(pinned[2][1] if args.features & 1 else 0) + ')',
           'const uint8_t fx_controls_dependency_digest[32]={' + ','.join(hex(v) for v in hashlib.sha256(metadata_raw).digest()) + '};',
           'const uint32_t fx_controls_dependency_count=' + str(len(selected)) + 'u;',
           'const FxNativePackSymbol fx_controls_dependency_table[' + str(max(1,len(selected))) + ']={']
    hdr += ['{.name="' + n + '",.version=1u,.flags=FX_NATIVE_PACK_SYMBOL_FUNCTION,.address=UINT64_C(' + hex(a) + ')},' for n,a in selected]
    if not selected: hdr.append('{0},')
    hdr.append('};')
    hdr += ['const FxNativeLocale fx_controls_locale={.abi=1u,.bytes=sizeof(FxNativeLocale),.languages=' + str(locale['languages']) + 'u,.text_count=' + str(locale['text_count']) + 'u,.offsets_directory=UINT64_C(' + hex(locale['offsets_directory']) + '),.pools_directory=UINT64_C(' + hex(locale['pools_directory']) + ')};',
            'const FxNativePackSymbol fx_controls_locale_export={.name="camera.locale.text",.version=1u,.flags=FX_NATIVE_PACK_SYMBOL_DATA,.address=(uintptr_t)&fx_controls_locale};']
    (out / 'fx_control_dependencies.h').write_text('\n'.join(hdr) + '\n', encoding='utf-8')
    commands, inputs, objects = [], set(SOURCES), []
    def run(cmd):
        subprocess.run(cmd, cwd=ROOT, check=True, timeout=60); commands.append(cmd)
    for i, source in enumerate(SOURCES):
        obj, dep = out / (str(i) + '.o'), out / (str(i) + '.d')
        flags = ['--target=aarch64-none-elf', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-Wconversion', '-Wshadow',
                 '-ffreestanding', '-fno-builtin', '-mgeneral-regs-only', '-mstrict-align', '-mno-outline-atomics',
                 '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
                 '-ffunction-sections', '-fdata-sections', '-I', 'include', '-I', 'nativepacks/feature_controls', '-I', str(out),
                 '-DFX_FREESTANDING=1', '-DFX_CONTROL_FEATURES=' + str(args.features)]
        if source.endswith('.S'): flags = ['--target=aarch64-none-elf']
        run([cc, *flags, '-MMD', '-MF', str(dep), '-MT', 'fx_core', '-c', source, '-o', str(obj)])
        inputs.update(dependencies(dep, ROOT)); objects.append(str(obj))
    elf_path = out / 'controls.elf'
    run([ld, '-m', 'aarch64elf', '-z', 'max-page-size=4096', '-T', 'nativepacks/feature_controls/controls.ld',
         '--gc-sections', '--defsym=fx_native_pack_feature_mask=' + str(args.features), '--undefined=fx_controls_metadata', '--undefined=fx_controls_dependency_digest', '--undefined=fx_controls_dependency_table', '--undefined=fx_controls_dependency_count', *objects, '-o', str(elf_path)])
    elf = elf_path.read_bytes(); entry, segments = load_segments(elf)
    symbols = {p[2]: int(p[0], 16) for line in subprocess.check_output([nm, '--defined-only', '--extern-only', str(elf_path)], text=True).splitlines()
               if len(p := line.split()) == 3}
    for i, s in enumerate(segments):
        name = 'controls.segment' + str(i) + '.bin'; start = s['file_offset']
        (out / name).write_bytes(elf[start:start + s['file_bytes']]); s['file'] = name
    inputs.update(['nativepacks/feature_controls/build.py', 'nativepacks/feature_controls/controls.ld', 'vendor/NikonFirmwarePatch-features/resources/compiled.json', 'host/z8_native_capsule.py'])
    result = dict(schema=1, abi=1, features=args.features, feature_bits=dict(framing=1, video_quality=2, log_colour=4, photo_tools=8),
                  fixed_base=0x408d0000, limit=0x40950000, entry=entry, dependencies=[dict(name=n,version=1,address=a) for n,a in selected],
                  native_metadata_sha256=sha(metadata_raw), locale_descriptor=locale,
                  elf=str(elf_path), elf_sha256=sha(elf), symbols=symbols, segments=segments,
                  sources_sha256={n: sha((ROOT / n).read_bytes()) for n in sorted(inputs)},
                  compiler=subprocess.check_output([cc, '--version'], text=True).splitlines()[0], commands=commands,
                  resident=True, native_execution_tested=False)
    (out / 'controls.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(metadata=str(out / 'controls.json'), entry=hex(entry), features=args.features,
                         segments=segments, elf_sha256=result['elf_sha256']), indent=2))


if __name__ == '__main__': main()
