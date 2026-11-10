"""Rebuild the Photo Tools r10 WLM from its sources and a baseline bundle."""
import argparse
import hashlib
from pathlib import Path
import struct
import subprocess
import sys

from build_wlm_native_components import window_patch
from cipher_patch import atomic_new
from wlm_bundle import decode_bundle, encode_bundle
from wlm_native import decode_component, encode_component
from z8_feature_suite import VENDOR, checked_upstream
from z8_native_capsule import EXEC, IMAGE, WRITE, Patch, decode, encode
from z8_native_profile import BASE, check_main
from z8_wlm_native_recipes import OwnedWindow

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'modules/photo_tools/r10'
MODULE = 'photo_tools'
CAVE, LIMIT = 0x409f0000, 0x409fc000
STOCK_RAM_FILE_DELTA = 0x9fdcf5000
R10_SHA256 = '1f1cf2016022410df03fe910df725e0639f0ed8bafc491e47e3d6428e0eea38f'
R10_CODE_SHA256 = '637d514057de8d0262093963ecbc40ed967249f061bc31e537ce85a7d2320225'

# Only the r10 branch sites are listed here. The FileService and NVMe timing
# hooks used by r10p are separate from this recipe.
JUMPS = (
    (0x41174b34, '08254029', 'c40_fraction'),
    (0x411718a0, 'e00b0032', 'c80_driver'),
    (0x41174a10, '691c0072', 'hs_fraction'),
    (0x410622d4, '090040b9', 'c80_crop_entry'),
    (0x41065194, 'ffc302d1', 'c80_isp_entry'),
    (0x41062000, 'f40f1ef8', 'c80_desc_entry'),
    (0x40273b74, '80000054', 'wa_alignment_branch'),
    (0x40273bd4, 'dc000035', 'wa_copy_branch'),
)
CALLS = (
    (0x40f850e4, 0x40f96584, 'dxc60_post_limit'),
    (0x40f95c4c, 0x40f96584, 'dxc60_post_limit'),
    (0x40f96538, 0x40f96584, 'dxc60_post_limit'),
    (0x40f97edc, 0x40f96584, 'dxc60_post_limit'),
    (0x40f9931c, 0x40f96584, 'dxc60_post_limit'),
    (0x40f993fc, 0x40f96584, 'dxc60_post_limit'),
    (0x406cd458, 0x4085a8e0, 'wt_lookup'),
    (0x406cd468, 0x4085b320, 'wt_open'),
    (0x406cd6a0, 0x4085b6d4, 'wt_finish'),
    (0x402d9d20, 0x402dd78c, 'wa_background_write'),
    (0x402dc850, 0x402dd78c, 'wa_background_write'),
)
POINTERS = (
    (0xa02a99a48 + 12 * 8, 0x411749d0, 'c80_rate_query'),
    (0xa02a99af8 + 12 * 8, 0x41174aa4, 'c80_fraction_path'),
)


def checked_patch(stock, offset, data, purpose, expected=None):
    old = stock[offset:offset + len(data)]
    if expected is not None and old != expected:
        raise ValueError(f'original {purpose} bytes differ at {offset:#x}')
    if offset == CAVE - BASE:
        if any(old) or CAVE + len(data) > LIMIT:
            raise ValueError('r10 executable allocation differs')
        return Patch(IMAGE, EXEC, CAVE, old, data)
    return window_patch(OwnedWindow(offset, old, data, MODULE, purpose))


def page(stock, address, content):
    offset = address - BASE
    before = stock[offset:offset + 0x1000]
    if len(before) != 0x1000 or any(before) or len(content) > len(before):
        raise ValueError(f'r10 data page differs at {address:#x}')
    return Patch(IMAGE, WRITE, address, before, content.ljust(len(before), b'\0'))


def replace_capture_backend(stock, old_capsule, code, symbols):
    from z8patch.compiled import load
    from z8patch.image import branch
    from z8precapture.patcher import edits

    stock_code, stock_meta = load('precapture', 'prebuilt', 'clang')
    base_edits = [checked_patch(stock, edit['offset'], edit['new'], edit['purpose'])
                  for edit in edits(stock_code, stock_meta['symbols'])]
    original = {p.address: p for p in old_capsule['patches']}
    if any(original.get(p.address) != p for p in base_edits):
        raise ValueError('baseline Photo Tools pre-capture component differs')

    changes = [checked_patch(stock, edit['offset'], edit['new'], edit['purpose'])
               for edit in edits(code, symbols)]

    def va(name):
        return int(symbols[name]['va'], 0)

    crop_at = 0xa0297c378 - STOCK_RAM_FILE_DELTA
    crop = bytearray(stock[crop_at:crop_at + 0x178])
    if struct.unpack_from('<4H', crop) != (5392, 1444, 3592, 548):
        raise ValueError('original C60 crop record differs')
    struct.pack_into('<4H', crop, 0, 5392, 256, 3592, 12)
    rates = b'C40RAW03' + struct.pack('<II', 0, 40000)
    rates = (rates.ljust(0x30, b'\0') + struct.pack('<4I', 0, 0, 80000, 140000)).ljust(0x100, b'\0')
    changes.extend((
        page(stock, 0x40bd0000, b'RO1DXTRC'),
        page(stock, 0x40b51000, rates + bytes(crop)),
        page(stock, 0x409fd000, b'ROEVT002'),
        page(stock, 0x40b50000, (bytes(64) + b'WTALGN05').ljust(0x140, b'\0') + b'WCBURST1'),
    ))
    for offset, size in ((0x4d2ffc8, 0x70c000), (0x4d2ffc4, 0x41f080)):
        changes.append(checked_patch(stock, offset, struct.pack('<I', size + 0x200),
                                     'RAW record tail', struct.pack('<I', size)))
    for site, preimage, name in JUMPS:
        changes.append(checked_patch(stock, site - BASE, branch(site, va(name)), name, bytes.fromhex(preimage)))
    for site, old, name in CALLS:
        changes.append(checked_patch(stock, site - BASE, branch(site, va(name), link=True),
                                     name, branch(site, old, link=True)))
    for site, old, name in POINTERS:
        changes.append(checked_patch(stock, site - STOCK_RAM_FILE_DELTA, struct.pack('<Q', va(name)),
                                     name, struct.pack('<Q', old)))

    removed = {p.address for p in base_edits}
    patches = sorted([p for p in old_capsule['patches'] if p.address not in removed] + changes,
                     key=lambda p: (p.kind, p.address))
    spans = sorted((p.address, p.address + len(p.new)) for p in patches)
    if any(end > begin for (_, end), (begin, _) in zip(spans, spans[1:])):
        raise ValueError('r10 patch windows overlap')
    digest = hashlib.sha256(code)
    for p in patches:
        digest.update(struct.pack('<QI', p.address, len(p.new)) + p.new)
    return encode(patches, int.from_bytes(digest.digest()[:8], 'little'),
                  old_capsule['requires_id'], old_capsule['entry'])


def build(stock_file, base_wlm, llvm, output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'build') or output == ROOT / 'build' or output.exists():
        raise ValueError('choose a new output directory below build/')
    checked_upstream()
    from z8patch.compiled import FLAGS
    from z8patch.linker import link

    stock = stock_file.read_bytes()
    check_main(stock)
    bundle = decode_bundle(base_wlm.read_bytes(), module_id=MODULE,
                           target=hashlib.sha256(stock).digest())
    photo = next(p for p in bundle['components'] if p['id'] == MODULE)
    old_capsule = decode(decode_component(photo['data'], target=bundle['target'], selection=16)['capsule'])

    clang = llvm / ('clang.exe' if sys.platform == 'win32' else 'clang')
    output.mkdir(parents=True)
    obj = output / 'precapture.o'
    subprocess.run([str(clang.resolve(strict=True)), *FLAGS, '-I', str(VENDOR / 'z8precapture'),
                    '-c', str(SOURCE / 'precapture.c'), '-o', str(obj)],
                   cwd=ROOT, check=True, timeout=60)
    code, _, symbols, _ = link(obj.read_bytes(), CAVE, LIMIT, 'precapture_history_entry')
    if hashlib.sha256(code).hexdigest() != R10_CODE_SHA256:
        raise ValueError('r10 native code differs; use the documented LLVM 19.1.5 toolchain')
    capsule = replace_capture_backend(stock, old_capsule, code, symbols)
    component = encode_component(MODULE, photo['version'], photo['selector_bit'], photo['rank'],
                                 [{'mask': 0, 'value': 0, 'capsule': capsule}],
                                 target=bundle['target'], requires_id=old_capsule['requires_id'])

    frontend_dir = output / 'frontend'
    subprocess.run([sys.executable, '-B', str(ROOT / 'fenestra_z8.py'), 'build-module',
                    str(SOURCE / 'module.c'), '--id', MODULE, '--optimize', 's',
                    '--llvm-bin', str(llvm.resolve()), '--output-dir', str(frontend_dir)],
                   cwd=ROOT, check=True, timeout=60)
    frontend = (frontend_dir / 'photo_tools.wlm').read_bytes()
    result = encode_bundle(frontend, MODULE,
                           [component if p['id'] == MODULE else p['data'] for p in bundle['components']],
                           bundle['target'])
    if hashlib.sha256(result).hexdigest() != R10_SHA256:
        raise ValueError('r10 WLM differs from the release identity')
    path = output / 'photo_tools.wlm'
    atomic_new(path, result)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', type=Path, required=True)
    parser.add_argument('--base-wlm', type=Path, required=True)
    parser.add_argument('--llvm-bin', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    path = build(args.stock, args.base_wlm, args.llvm_bin, args.output_dir)
    print(f'{R10_SHA256}  {path}')


if __name__ == '__main__':
    main()
