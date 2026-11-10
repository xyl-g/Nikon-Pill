"""Pinned all-feature native backend for the opt-in Z8 feature-suite core.

The separately licensed upstream is kept intact. Its reviewed fixed-address
code runs in the native firmware; WLMs use copied services, never these pointers.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor/NikonFirmwarePatch-features'
PROVENANCE = ROOT / 'vendor/NikonFirmwarePatch-features.lock.json'
PROVENANCE_SHA256 = 'be51d4d099a34c03eeebf14e5f5acd4743168b1edac725d968e5bd0422cacbdd'
COMPILED_SHA256 = 'a3b6ee2167cce2deae9dad7d8d6722e145fbc25ae22602ffff83ba4a92d93a65'
COMBINATIONS_SHA256 = 'a4d113c4c692068f74effaa404c833feeef269c886464ecd6198abede0df0853'
FEATURES = ('framing', 'video-quality', 'log-colour', 'photo-tools')
BASE = 0x40160000
TEXT_BASE = 3225
HOOKS = {
    0x40cebe3c: ('fx_z8_suite_video_open_hook', 0xf9000268, False),
    0x40ceef10: ('fx_z8_suite_video_close_hook', 0xf81e0ff4, False),
}
NATIVE = {'fx_z8_suite_video_open_resume': 0x40cebe40,
          'fx_z8_suite_video_upstream_close': 0x40960870}
SOURCES = ['src/suite_video.c', 'src/framing.c', 'src/photo_tools.c',
           'src/platform/z8_c311_full/suite_video.c',
           'src/platform/z8_c311_full/suite_video_hooks.S',
           'src/platform/z8_c311_full/framing.c',
           'src/platform/z8_c311_full/photo_tools.c']
KEEP = ['fx_z8_suite_video_metadata']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_upstream():
    sys.dont_write_bytecode = True
    if not (VENDOR / 'features.py').is_file():
        raise FileNotFoundError('Prepare the local toolkit with scripts/prepare_z8_feature_toolkit.py')
    raw = PROVENANCE.read_bytes()
    if sha(raw) != PROVENANCE_SHA256:
        raise ValueError('feature-suite provenance differs')
    provenance = json.loads(raw)
    for name, digest in provenance['files'].items():
        source = (VENDOR / name).resolve(strict=True)
        if not source.is_relative_to(VENDOR) or sha(source.read_bytes()) != digest:
            raise ValueError('feature-suite upstream dependency differs: ' + name)
    sys.path.insert(0, str(VENDOR)) if str(VENDOR) not in sys.path else None
    return provenance


def prepare_core(original, out):
    import struct
    provenance = checked_upstream()
    for address, (_, word, _) in HOOKS.items():
        if struct.unpack_from('<I', original, address - BASE)[0] != word:
            raise ValueError('feature-suite native MOV preimage differs')
    compiled = json.loads((VENDOR / 'resources/compiled.json').read_text())
    from z8_framing_profile import verify_original
    from z8_photo_tools_profile import validate_metadata
    verify_original(original)
    validate_metadata(compiled)
    exports = [('FX_Z8_TIER_STAR_REPUBLISH', 'quality', 'tier_star_republish'),
               ('FX_Z8_PSM_WORKER', 'pixelshift', 'psm_worker'),
               ('FX_Z8_PSM_CAMERA_JOB', 'pixelshift', 'psm_camera_job')]
    lines = ['/* Exact reviewed upstream native exports; core use only. */',
             '#ifndef FX_Z8_FEATURE_SUITE_SYMBOLS_H', '#define FX_Z8_FEATURE_SUITE_SYMBOLS_H']
    for macro, unit, name in exports:
        row = compiled['units'][unit]
        raw = (VENDOR / row['resource']).read_bytes()
        if len(raw) != row['bytes'] or sha(raw) != row['sha256']:
            raise ValueError('reviewed upstream binary differs: ' + unit)
        lines.append('#define ' + macro + ' ' + row['symbols'][name]['va'] + 'ull')
    lines.extend(['#define FX_Z8_FEATURE_SUITE_TEXT_BASE 3225u', '#endif', ''])
    (out / 'z8_feature_suite_symbols.h').write_text('\n'.join(lines))
    return dict(abi=1, features=list(FEATURES), text_base=TEXT_BASE,
                source_commit=provenance['source_commit'], provenance_sha256=PROVENANCE_SHA256,
                native_movie_lifetime_tracking=True, hook_sites=2,
                physical_qualified=False, plugin_only_install_supported=False)


def upstream_inputs():
    provenance = checked_upstream()
    return ['host/z8_feature_suite.py', 'host/z8_framing_profile.py', 'host/z8_photo_tools_profile.py',
            PROVENANCE.relative_to(ROOT).as_posix(),
            *['vendor/NikonFirmwarePatch-features/' + name for name in provenance['files']]]


def compose(original):
    checked_upstream()
    import binascii
    from features import compose as upstream_compose
    eg = original + binascii.crc_hqx(original, 0).to_bytes(2, 'big')
    image, detail, rows = upstream_compose(eg, list(FEATURES), code_source='prebuilt')
    return bytes(image.new[:-2]), detail, rows
