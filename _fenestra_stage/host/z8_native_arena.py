"""Generic empty native arena prerequisite; independent of feature producers."""
import hashlib
import json

BASE=0x40160000
STOCK_BYTES=81807709
STOCK_SHA256='2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
ARENA_BEGIN=0x408bf8f0
ARENA_END=0x40c00000
COPY_END_SITE=0x41184d8c
COPY_END_OLD=bytes.fromhex('081e9fd26811a8f2')
COPY_END_NEW=bytes.fromhex('080080d20818a8f2')
INIT_FILE_BEGIN=0x4b13000
INIT_FILE_END=0x4dc0000
INIT_RAM_BEGIN=0xa02808000
ALIAS_DELTA=0xa0f800000-0x40200000
# Individually audited cold-bootstrap resource roots, never a broad BSS grant.
RESOURCE_ROOTS=({'name':'camera.locale.icon-root','address':'0xa02f2ab80','bytes':8,
                 'record_kind':'pointer','cold_start_only':True},)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')

def arena_profile():
    profile=dict(schema='fenestra.z8-c311-native-arena/v1',stock_main_sha256=STOCK_SHA256,
        arena_begin=hex(ARENA_BEGIN),arena_end=hex(ARENA_END),
        stock_arena_sha256=sha(bytes(ARENA_END-ARENA_BEGIN)),
        copy_end_site=hex(COPY_END_SITE),copy_end_original=COPY_END_OLD.hex(),
        copy_end_foundation=COPY_END_NEW.hex(),write_alias_delta=hex(ALIAS_DELTA),
        init_file_begin=hex(INIT_FILE_BEGIN),init_file_end=hex(INIT_FILE_END),
        init_ram_begin=hex(INIT_RAM_BEGIN),cold_start_only=True,
        resource_roots=[dict(root) for root in RESOURCE_ROOTS],
        locale_contract=dict(abi=1,symbol='camera.locale.text',symbol_version=1,descriptor_bytes=32,
            native_bounds='immutable-native-offset-count',accepted_ids='published-font-bank-capacity',
            count_hook_permitted=False))
    digest=sha(canonical(profile))
    return dict(**profile,profile_sha256=digest,requires_id=int.from_bytes(bytes.fromhex(digest)[:8],'little'))


def verify_arena(stock):
    if len(stock)!=STOCK_BYTES or sha(stock)!=STOCK_SHA256:
        raise ValueError('exact original Z8 C3.11 MAIN length/SHA-256 required')
    if stock[COPY_END_SITE-BASE:COPY_END_SITE-BASE+8]!=COPY_END_OLD:
        raise ValueError('original copy-end instructions differ')
    if any(stock[ARENA_BEGIN-BASE:ARENA_END-BASE]):
        raise ValueError('original reserved arena is not entirely zero')



def arena_header(profile=None):
    """Generate the exact target dependency/resource policy, without a producer import."""
    profile=arena_profile() if profile is None else profile
    roots=profile['resource_roots']
    if not roots or any(r['record_kind']!='pointer' or r['bytes']!=8 or not r['cold_start_only'] for r in roots):
        raise ValueError('only audited cold-bootstrap pointer roots supported')
    rows=','.join('{UINT64_C('+r['address']+'),'+str(r['bytes'])+'u,8u}' for r in roots)
    identity=','.join(str(v) for v in bytes.fromhex(profile['profile_sha256']))
    return ('/* Generated generic native arena/resource contract. */\n'
            '#ifndef FX_Z8_NATIVE_ARENA_PROFILE_H\n#define FX_Z8_NATIVE_ARENA_PROFILE_H\n'
            '#include <stdint.h>\n'
            'typedef struct {uint64_t address;uint32_t bytes,flags;} FxZ8NativeResourceRoot;\n'
            '#define FX_Z8_NATIVE_ARENA_DEPENDENCY UINT64_C('+hex(profile['requires_id'])+')\n'
            '#define FX_Z8_NATIVE_ARENA_PROFILE_IDENTITY {'+identity+'}\n'
            '#define FX_Z8_NATIVE_RESOURCE_ROOT_COUNT '+str(len(roots))+'u\n'
            '#define FX_Z8_NATIVE_RESOURCE_ROOT_INITIALIZER {'+rows+'}\n'
            '#endif\n')
