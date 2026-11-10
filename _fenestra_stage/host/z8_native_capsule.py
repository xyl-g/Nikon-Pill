"""Build fixed-address native card capsules from exact stock and pinned recipes.

The native firmware contains an empty generic arena, not these feature bytes.
Shared menus/fonts mean a selection is one exact capsule, not stackable patches.
No camera, USB transport, relocation or merged feature MAIN is accepted here.
"""
import argparse
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

from z8_native_profile import BASE, STOCK_BYTES, STOCK_SHA256, check_main, read_main
from z8_feature_suite import (checked_upstream, VENDOR, FEATURES,
                              PROVENANCE_SHA256, COMPILED_SHA256, COMBINATIONS_SHA256)

ROOT=Path(__file__).resolve().parents[1]
SCHEMA='fenestra.z8-native-capsule/v1'
MAGIC=b'FXCAPS1\0'
INDEX_MAGIC=b'FNCIDX01'
INDEX_BYTES=56
HEADER=128
RECORD=112
IMAGE=1
HOOK=2
EXEC=1
WRITE=2
INSTRUCTION=4
POINTER=8
DATA=16
NONE=0xffffffff
from z8_native_arena import (ARENA_BEGIN, ARENA_END, COPY_END_SITE, COPY_END_OLD, COPY_END_NEW,
    INIT_FILE_BEGIN, INIT_FILE_END, INIT_RAM_BEGIN, ALIAS_DELTA, arena_profile, verify_arena)
CONTROLS_BEGIN=0x408d0000
CONTROLS_END=0x40950000
# Audited prebuilt native code allocations (code plus their literal/RO tables).
CODE_AREAS=((0x40951000,0x40951538),(0x40960000,0x40962d30),
    (0x40970000,0x40970c80),(0x40980000,0x409800f8),
    (0x40990000,0x409afdd8),(0x409d8000,0x409d83c8),
    (0x409f0000,0x409f847d),(0x40a00000,0x40a00608),
    (0x40a10000,0x40a10800),(0x40a41000,0x40a4168c),
    (0x40b00000,0x40b00aa8))
MOV_HOOKS={0x40cebe3c:'fx_controls_video_open_hook',
           0x40ceef10:'fx_controls_video_close_hook'}
NATIVE_EXPORTS=(('framing.opengate.writer_close_hook',1,'opengate','writer_close_hook'),
                ('quality.tier_star_republish',2,'quality','tier_star_republish'),
                ('pixelshift.psm_worker',8,'pixelshift','psm_worker'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')


def selected(mask):
    if type(mask) is not int or not 1<=mask<=15:
        raise ValueError('one of the 15 nonempty exact feature combinations is required')
    return [name for i,name in enumerate(FEATURES) if mask&(1<<i)]


def feature_mask(value):
    try:
        mask=int(value,0)
        selected(mask)
        return mask
    except ValueError:
        names=value.split(',')
        if not names or len(set(names))!=len(names) or any(n not in FEATURES for n in names):
            raise argparse.ArgumentTypeError('use mask 1..15 or comma-separated exact feature names')
        return sum(1<<FEATURES.index(name) for name in names)



@dataclass(frozen=True)
class Patch:
    kind:int
    flags:int
    address:int
    old:bytes
    new:bytes
    depends:int=NONE


def _spans_disjoint(spans,label):
    previous=None
    for start,end in sorted(spans):
        if start>=end or (previous is not None and start<previous):
            raise ValueError(label+' windows overlap or are empty')
        previous=end


def encode(patches,package_id,requires_id,entry=0):
    if not 1<=len(patches)<=1024 or not 0<package_id<1<<64 or not 0<=requires_id<1<<64:
        raise ValueError('capsule count or opaque identity outside supported bounds')
    payload=bytearray()
    records=bytearray()
    data_base=HEADER+RECORD*len(patches)
    for patch in patches:
        new_offset=data_base+len(payload);payload.extend(patch.new)
        old_offset=data_base+len(payload);payload.extend(patch.old)
        records.extend(struct.pack('<IIQIIII32s32s16s',patch.kind,patch.flags,patch.address,len(patch.new),
            new_offset,old_offset,patch.depends,bytes.fromhex(sha(patch.old)),bytes.fromhex(sha(patch.new)),bytes(16)))
    body=bytes(records+payload)
    header=struct.pack('<8s6I32s32s4Q',MAGIC,1,HEADER,HEADER+len(body),len(patches),HEADER,0,
        bytes.fromhex(STOCK_SHA256),bytes.fromhex(sha(body)),package_id,requires_id,
        sum(len(p.new) for p in patches),entry)
    result=header+body
    decode(result)
    return result


def decode(blob):
    if len(blob)<HEADER or len(blob)>16*1024*1024:
        raise ValueError('capsule size outside bounds')
    magic,abi,header,size,count,offset,flags,target,body,package,requires,total,entry=struct.unpack_from('<8s6I32s32s4Q',blob)
    if (magic,abi,header,size,offset,flags)!=(MAGIC,1,HEADER,len(blob),HEADER,0) or not package:
        raise ValueError('capsule header differs')
    if not 1<=count<=1024 or HEADER+count*RECORD>len(blob) or target.hex()!=STOCK_SHA256:
        raise ValueError('capsule target or record table differs')
    if body.hex()!=sha(blob[HEADER:]):
        raise ValueError('capsule body SHA-256 differs')
    patches=[];payloads=[];seen_hook=False
    for i in range(count):
        kind,fl,address,n,new,old,depends,old_sha,new_sha,reserved=struct.unpack_from('<IIQIIII32s32s16s',blob,HEADER+i*RECORD)
        if reserved!=bytes(16) or not 0<n<=1024*1024 or address+n>1<<64:
            raise ValueError('record reserved, length or address invalid')
        if kind==IMAGE:
            if seen_hook or fl not in (0,EXEC,WRITE) or depends!=NONE:
                raise ValueError('IMAGE prefix, permissions or dependency invalid')
        elif kind==HOOK:
            seen_hook=True
            if fl not in (INSTRUCTION,POINTER,DATA):raise ValueError('HOOK type invalid')
            if depends!=NONE and (depends>=i or patches[depends].kind!=IMAGE):
                raise ValueError('HOOK dependency is not an earlier IMAGE')
            if fl==INSTRUCTION and (address%4 or n%4):raise ValueError('instruction hook alignment differs')
            if fl==POINTER and (address%8 or n%8):raise ValueError('pointer hook alignment differs')
        else:raise ValueError('unsupported record kind (BOOT_ONLY is never a runtime record)')
        for start in (new,old):
            if start<HEADER+count*RECORD or start+n>len(blob):raise ValueError('record payload outside package')
            payloads.append((start,start+n))
        old_bytes=blob[old:old+n];new_bytes=blob[new:new+n]
        if old_sha.hex()!=sha(old_bytes) or new_sha.hex()!=sha(new_bytes):raise ValueError('record payload SHA-256 differs')
        patches.append(Patch(kind,fl,address,old_bytes,new_bytes,depends))
    _spans_disjoint(payloads,'payload')
    _spans_disjoint([(p.address,p.address+len(p.new)) for p in patches],'destination')
    if sum(len(p.new) for p in patches)!=total or total>8*1024*1024:
        raise ValueError('capsule total-write bytes differ')
    if entry and (entry%4 or not any(p.kind==IMAGE and p.flags==EXEC and p.address<=entry and entry+4<=p.address+len(p.new) for p in patches)):
        raise ValueError('capsule entry is not in a package EXEC IMAGE')
    return dict(patches=patches,package_id=package,requires_id=requires,entry=entry,total_write_bytes=total,
                sha256=sha(blob),stock_main_sha256=target.hex())



def make_index(blob):
    decode(blob)
    return struct.pack('<8sIIQ32s',INDEX_MAGIC,1,INDEX_BYTES,len(blob),bytes.fromhex(sha(blob)))


def verify_index(index,blob):
    if len(index)!=INDEX_BYTES:raise ValueError('BOOT.IDX must be exactly 56 bytes')
    magic,abi,n,size,digest=struct.unpack('<8sIIQ32s',index)
    if (magic,abi,n)!=(INDEX_MAGIC,1,INDEX_BYTES) or size!=len(blob) or not HEADER<=size<=16*1024*1024:
        raise ValueError('BOOT.IDX header or package size differs')
    if digest.hex()!=sha(blob):raise ValueError('BOOT.IDX complete package SHA-256 differs')
    return True


def pinned_native_text_layout(mask):
    """Bounds derived from the pinned selected recipe, before any pack build.

    The full producer additionally checks every stock prefix/UTF-16 endpoint.
    This small API lets a controls builder emit matching RO locale metadata.
    """
    checked_upstream()
    combinations=json.loads((VENDOR/'resources/combinations.json').read_text())
    profile=combinations['profiles']['+'.join(selected(mask))]
    rows=[combinations['patches'][i] for i in profile['patches']]
    tables=[r for r in rows if r['purpose']=='shared 36 language offset tables']
    if len(tables)!=1 or tables[0]['bytes']%(36*4):raise ValueError('pinned native text bank shape differs')
    entries=tables[0]['bytes']//(36*4)
    return dict(abi=1,bytes=32,languages=36,text_count=entries-1,
        offsets_directory=BASE+0x311c5a0,pools_directory=BASE+0x387af58,
        table_address=BASE+int(tables[0]['offset'],0),table_bytes=tables[0]['bytes'],
        combinations_sha256=COMBINATIONS_SHA256,recipe_id=profile['id'])


def _word_branch(site,target):
    delta=target-site
    if delta%4 or not -(1<<27)<=delta<1<<27:raise ValueError('native branch is out of range')
    return struct.pack('<I',0x14000000|((delta//4)&0x3ffffff))


def read_controls_elf(blob,mask):
    # Fixed ET_EXEC AArch64 only: ELF loading is copying, never runtime linking.
    if not 64<=len(blob)<=16*1024*1024 or blob[:16]!=b'\x7fELF\x02\x01\x01'+bytes(9):
        raise ValueError('fixed little-endian ELF64 controls image required')
    typ,machine,version,entry,phoff,shoff,flags,ehsize,phsize,phnum,shsize,shnum,shstr=struct.unpack_from('<HHIQQQIHHHHHH',blob,16)
    if (typ,machine,version,flags,ehsize,phsize,shsize)!=(2,183,1,0,64,56,64) or not 1<=phnum<=32 or not 1<=shnum<=4096:
        raise ValueError('controls ELF type, architecture or table layout invalid')
    if phoff+phnum*phsize>len(blob) or shoff+shnum*shsize>len(blob):raise ValueError('controls ELF tables out of bounds')
    segments=[]
    for i in range(phnum):
        kind,fl,at,va,physical,file_bytes,memory_bytes,alignment=struct.unpack_from('<II6Q',blob,phoff+i*phsize)
        if kind in (2,3,7):raise ValueError('dynamic or TLS controls images are unsupported')
        if kind!=1:continue
        if fl not in (4,5,6) or not memory_bytes or file_bytes>memory_bytes or at+file_bytes>len(blob):
            raise ValueError('controls segment bounds or W+X permissions invalid')
        if not CONTROLS_BEGIN<=va<va+memory_bytes<=CONTROLS_END or physical!=va or alignment not in (0,1,4096,65536):
            raise ValueError('controls segment outside the fixed reserved slot')
        if alignment>1 and (va-at)%alignment:raise ValueError('controls segment alignment invalid')
        data=blob[at:at+file_bytes]+bytes(memory_bytes-file_bytes)
        segments.append(dict(va=va,flags=fl,file_offset=at,file_bytes=file_bytes,memory_bytes=memory_bytes,data=data))
    if not segments or min(s['va'] for s in segments)!=CONTROLS_BEGIN:
        raise ValueError('controls ELF does not begin at the pinned slot')
    _spans_disjoint([(s['va'],s['va']+s['memory_bytes']) for s in segments],'controls segment')
    # Equal-page incompatible permissions would make sealing ambiguous.
    pages={}
    for s in segments:
        for page in range(s['va']//4096,(s['va']+s['memory_bytes']-1)//4096+1):
            if page in pages and pages[page]!=s['flags']:raise ValueError('controls permissions share a page')
            pages[page]=s['flags']
    sections=[struct.unpack_from('<II4QII2Q',blob,shoff+i*shsize) for i in range(shnum)]
    symbols={};symbol_sizes={}
    for section in sections:
        _,kind,section_flags,va,at,n,link,info,align,entsize=section
        if kind in (4,9) and n:raise ValueError('controls ELF contains relocation records')
        if kind not in (8,) and at+n>len(blob):raise ValueError('controls section out of bounds')
        if kind!=2:continue
        if entsize!=24 or n%24 or link>=len(sections):raise ValueError('controls symbol table invalid')
        string_section=sections[link]
        if string_section[1]!=3:raise ValueError('controls symbols have no string table')
        strings=blob[string_section[4]:string_section[4]+string_section[5]]
        for cursor in range(at,at+n,24):
            name,info,other,index,value,symbol_bytes=struct.unpack_from('<IBBHQQ',blob,cursor)
            if name>=len(strings):raise ValueError('controls symbol name out of bounds')
            end=strings.find(b'\0',name)
            if end<0:raise ValueError('controls symbol name not terminated')
            try:text=strings[name:end].decode('ascii')
            except UnicodeError as error:raise ValueError('controls non-ASCII symbol') from error
            if index==0 and text and info>>4 in (1,2):raise ValueError('controls ELF has undefined imports')
            if text and index!=0 and info>>4 in (1,2):
                if text in symbols and symbols[text]!=value:raise ValueError('duplicate controls symbol')
                symbols[text]=value;symbol_sizes[text]=symbol_bytes
    for name in (*MOV_HOOKS.values(),'fx_native_pack_entry'):
        if name not in symbols or not any(s['flags']&1 and s['va']<=symbols[name]<s['va']+s['memory_bytes'] for s in segments):
            raise ValueError('required controls entry/hook symbol missing: '+name)
    if entry!=symbols['fx_native_pack_entry'] or entry%4:raise ValueError('controls entry differs from exported pack entry')
    # Optional absolute mask marker emitted by the builder must match selection.
    if symbols.get('fx_native_pack_feature_mask')!=mask:
        raise ValueError('controls feature selection differs')
    return dict(entry=entry,symbols=symbols,symbol_sizes=symbol_sizes,segments=segments,sha256=sha(blob),features=selected(mask))



def _elf_symbol_bytes(controls,name,n):
    address=controls['symbols'].get(name)
    if address is None or controls['symbol_sizes'].get(name,0)<n:
        raise ValueError('controls metadata symbol missing or short: '+name)
    for segment in controls['segments']:
        if segment['va']<=address and address+n<=segment['va']+segment['memory_bytes']:
            at=address-segment['va']
            return segment['data'][at:at+n]
    raise ValueError('controls metadata is outside sealed PT_LOAD: '+name)


def verify_controls_locale(controls,text):
    count=text.get('loaded_count',text.get('text_count'))
    directories=int(text['offsets_directory'],0) if isinstance(text['offsets_directory'],str) else text['offsets_directory']
    pools=int(text['pools_directory'],0) if isinstance(text['pools_directory'],str) else text['pools_directory']
    wanted=struct.pack('<4I2Q',1,32,text['languages'],count,directories,pools)
    descriptor='fx_controls_locale';export='fx_controls_locale_export'
    for name,n in ((descriptor,32),(export,80)):
        address=controls['symbols'].get(name,-1)
        if controls['symbol_sizes'].get(name)!=n or not any(not(s['flags']&2) and s['va']<=address and address+n<=s['va']+s['memory_bytes'] for s in controls['segments']):
            raise ValueError('locale descriptor/export must be exact immutable RO PT_LOAD symbols')
    raw=_elf_symbol_bytes(controls,descriptor,32)
    if raw!=wanted:raise ValueError('locale descriptor differs from exact native text bounds/directories')
    address=controls['symbols'][descriptor];export_address=controls['symbols'][export]
    expected_export=struct.pack('<64sIIQ',b'camera.locale.text',1,2,address)
    if _elf_symbol_bytes(controls,export,80)!=expected_export:
        raise ValueError('locale DATA export name/version/address differs')
    return dict(name='camera.locale.text',version=1,flags=2,address=hex(address),bytes=32,
        sha256=sha(raw),export_address=hex(export_address),export_bytes=80,export_sha256=sha(expected_export))


def verify_controls_dependencies(controls,mask):
    # This is a host audit of this producer's pack. The resident core sees only
    # the generic entry/provider ABI and opaque capsule identities.
    if _elf_symbol_bytes(controls,'fx_controls_dependency_digest',32).hex()!=COMPILED_SHA256:
        raise ValueError('controls pinned native metadata digest differs')
    expected_layout=(0x46584e50,1,mask,40,56,104,80)
    if struct.unpack('<7I',_elf_symbol_bytes(controls,'fx_controls_metadata',28))!=expected_layout:
        raise ValueError('controls entry ABI or feature metadata differs')
    disabled=[]
    if not mask&1:disabled.extend(n for n in controls['symbols'] if n.startswith('fx_framing_'))
    if not mask&8:disabled.extend(n for n in controls['symbols'] if n.startswith(('fx_photo_tools_','fx_controls_photo_','fx_controls_precapture_')))
    if not mask&6 and 'fx_suite_video_resource' in controls['symbols']:disabled.append('fx_suite_video_resource')
    if disabled:raise ValueError('controls contains disabled feature code: '+','.join(sorted(disabled)))
    compiled=json.loads((VENDOR/'resources/compiled.json').read_text())
    expected={(row['name'],row['version'],1,int(row['address'],0)) for row in _native_exports(mask,compiled)}
    count=struct.unpack('<I',_elf_symbol_bytes(controls,'fx_controls_dependency_count',4))[0]
    if count!=len(expected):raise ValueError('controls dependency count differs from selected groups')
    raw=_elf_symbol_bytes(controls,'fx_controls_dependency_table',count*80) if count else b''
    actual=[]
    for at in range(0,len(raw),80):
        name,version,flags,address=struct.unpack_from('<64sIIQ',raw,at)
        end=name.find(b'\0')
        if end<0 or any(name[end:]):raise ValueError('controls dependency name is not bounded/padded')
        try:text=name[:end].decode('ascii')
        except UnicodeError as error:raise ValueError('controls dependency name is not ASCII') from error
        actual.append((text,version,flags,address))
    if len(set(actual))!=len(actual) or set(actual)!=expected:
        raise ValueError('controls native dependency names/versions/addresses differ')
    verify_controls_locale(controls,pinned_native_text_layout(mask))
    return True


def _runtime_address(offset,n):
    if INIT_FILE_BEGIN<=offset and offset+n<=INIT_FILE_END:
        return INIT_RAM_BEGIN+offset-INIT_FILE_BEGIN,'init-ram'
    if offset<INIT_FILE_END and offset+n>INIT_FILE_BEGIN:
        raise ValueError('window crosses the initialized-RAM mapping boundary')
    return BASE+offset,'image-va'


def _native_exports(mask,compiled):
    exports=[]
    for name,bit,unit,symbol in NATIVE_EXPORTS:
        if mask&bit:
            exports.append(dict(name=name,version=1,address=compiled['units'][unit]['symbols'][symbol]['va']))
    return exports


def _recipe(stock,mask):
    checked_upstream()
    from features import compose
    features=selected(mask)
    eg=stock+binascii.crc_hqx(stock,0).to_bytes(2,'big')
    image,detail,rows=compose(eg,features,code_source='prebuilt')
    result=bytes(image.new[:-2])
    recipes=json.loads((VENDOR/'resources/combinations.json').read_text())
    recipe=recipes['profiles']['+'.join(features)]
    expected=[recipes['patches'][i] for i in recipe['patches']]
    if rows!=expected:raise ValueError('composer windows differ from pinned independent combination recipe')
    rebuilt=bytearray(stock)
    for row in rows:
        at=int(row['offset'],0);n=row['bytes']
        if at<0 or at+n>len(stock) or sha(stock[at:at+n])!=row['old_sha256'] or sha(result[at:at+n])!=row['new_sha256']:
            raise ValueError('exact recipe preimage/new-image digest differs')
        rebuilt[at:at+n]=result[at:at+n]
    _spans_disjoint([(int(r['offset'],0),int(r['offset'],0)+r['bytes']) for r in rows],'upstream recipe')
    if rebuilt!=result:raise ValueError('recipe does not account for every composed MAIN byte')
    return result,rows,recipe['id']



def derive_native_text(stock,upstream,rows):
    # This is the offset-table bound, not the native accepted-ID limit. The
    # latter is shared with font/attribute lookups and the core namespace.
    count_address=0xa0281cd5c
    count_offset=INIT_FILE_BEGIN+count_address-INIT_RAM_BEGIN
    original=stock[count_offset:count_offset+4]
    original_count=int.from_bytes(original,'little')
    if original_count!=65535:raise ValueError('original native text-count sentinel preimage differs')
    original_directories=struct.unpack_from('<36Q',stock,0x311c5a0)
    original_stride=original_directories[1]-original_directories[0]
    if not original_stride or original_stride%4 or any(p!=original_directories[0]+i*original_stride for i,p in enumerate(original_directories)):
        raise ValueError('original 36-language text table stride differs')
    stock_count=original_stride//4-1
    tables=[r for r in rows if r['purpose']=='shared 36 language offset tables']
    pools=[r for r in rows if r['purpose']=='shared selected-feature strings']
    if len(tables)!=1 or len(pools)!=1 or tables[0]['bytes']%(36*4):
        raise ValueError('shared native text tables/pool are not one exact 36-language bank')
    table_offset=int(tables[0]['offset'],0);pool_offset=int(pools[0]['offset'],0)
    entries=tables[0]['bytes']//(36*4);count=entries-1;stride=entries*4
    if not stock_count<count<65536:raise ValueError('derived native text count outside supported width')
    for language in range(36):
        directory=0x311c5a0+language*8
        address=struct.unpack_from('<Q',upstream,directory)[0]
        if address!=BASE+table_offset+language*stride:raise ValueError('native text directory stride differs')
        original_table=struct.unpack_from('<Q',stock,directory)[0]-BASE
        original_pool=struct.unpack_from('<Q',stock,0x387af58+language*8)[0]-BASE
        at=address-BASE
        prefix=(stock_count+1)*4
        if upstream[at:at+prefix]!=stock[original_table:original_table+prefix]:
            raise ValueError('native original text offset prefix differs')
        # Slot stock_count is the original endpoint/spacer. New translated
        # strings begin at stock_count+1; every new endpoint is inside the
        # actual shared selected-feature UTF-16 pool, not an inferred address.
        for ident in range(stock_count+1,count):
            start,stop=struct.unpack_from('<ii',upstream,at+ident*4)
            start+=original_pool;stop+=original_pool
            if not pool_offset<=start<=stop<=pool_offset+pools[0]['bytes'] or (stop-start)%2:
                raise ValueError('native selected text endpoint escapes actual string pool')
    info=dict(original_count=original_count,stock_text_count=stock_count,loaded_count=count,languages=36,entries_per_language=entries,
        table_address=hex(BASE+table_offset),table_bytes=tables[0]['bytes'],count_address=hex(count_address),
        offsets_directory=hex(BASE+0x311c5a0),pools_directory=hex(BASE+0x387af58),
        native_acceptance_managed_by_core=True,runtime_count_source='published-font-bank-capacity',
        derivation='actual selected table byte count, all 36 directory strides, unchanged stock prefixes and in-pool UTF-16 endpoints')
    return info


def _mov64_sequence(code,register):
    if len(code)!=16:raise ValueError('native bootstrap MOV sequence width differs')
    result=0
    for i,word in enumerate(struct.unpack('<4I',code)):
        expected=(0xd2800000 if i==0 else 0xf2800000)|(i<<21)|register
        if word&0xffe0001f!=expected:raise ValueError('native bootstrap MOV sequence differs')
        result|=((word>>5)&0xffff)<<(i*16)
    return result


def native_resource_supplements(stock,upstream,rows,mask):
    # This initializer ran before AppFirstStartComplete. Its argument hook alone
    # cannot replace the cached BSS root. Derive the exact old/new pointer and
    # destination from the original instructions, not a fictitious file mapping.
    if not mask&4:return [],[]
    site=0x403ee56c;setter=0x4032dc34
    before=stock[site-BASE:site-BASE+20];after=upstream[site-BASE:site-BASE+20]
    original=_mov64_sequence(before[:16],0);replacement=_mov64_sequence(after[:16],0)
    word=struct.unpack_from('<I',before,16)[0];imm=word&0x3ffffff
    target=site+16+((imm if imm<0x2000000 else imm-0x4000000)*4)
    if word>>26!=0x25 or target!=setter or after[8:]!=before[8:]:
        raise ValueError('native icon-root initializer call differs')
    body=stock[setter-BASE:setter-BASE+24]
    address=_mov64_sequence(body[:16],8)
    if body[16:]!=bytes.fromhex('000100f9c0035fd6'):
        raise ValueError('native resource-root setter store/return differs')
    capability=dict(name='camera.locale.icon-root',address=hex(address),bytes=8,
                    record_kind='pointer',cold_start_only=True)
    if capability not in arena_profile()['resource_roots']:
        raise ValueError('native resource root lacks exact foundation capability')
    initializer=[r for r in rows if int(r['offset'],0)==site-BASE and r['bytes']==8]
    tables=[r for r in rows if int(r['offset'],0)==replacement-BASE]
    if len(initializer)!=1 or len(tables)!=1 or tables[0]['bytes']%24:
        raise ValueError('native icon initializer/table recipe differs')
    entries=tables[0]['bytes']//24-1;stock_entries=entries-4
    if stock_entries<=0 or not BASE<=original<original+stock_entries*24<=BASE+len(stock):
        raise ValueError('native original icon table bounds differ')
    prefix=stock_entries*24;at=replacement-BASE
    if (upstream[at:at+prefix]!=stock[original-BASE:original-BASE+prefix] or
        any(upstream[at+entries*24:at+(entries+1)*24]) or
        any(stock[original-BASE+prefix:original-BASE+prefix+8])):
        raise ValueError('native icon table prefix/end record differs')
    pngs=[r for r in rows if r['purpose'].startswith('log: L2 icon PNGs:')]
    if len(pngs)!=1:raise ValueError('native icon PNG recipe differs')
    png=BASE+int(pngs[0]['offset'],0);png_end=png+pngs[0]['bytes']
    for i in range(stock_entries,entries):
        kind,n,r0,r1,pointer=struct.unpack_from('<4IQ',upstream,at+i*24)
        if kind!=0x100 or r0 or r1 or not n or not png<=pointer<pointer+n<=png_end:
            raise ValueError('native icon entry escapes actual PNG payload')
        if upstream[pointer-BASE:pointer-BASE+8]!=b'\x89PNG\r\n\x1a\n':
            raise ValueError('native icon entry is not PNG data')
    info=dict(**capability,original_root=hex(original),loaded_root=hex(replacement),
        initializer_address=hex(site),original_initializer=before.hex(),loaded_initializer=after.hex(),
        setter_address=hex(setter),original_setter=body.hex(),stock_entries=stock_entries,loaded_entries=entries,
        derivation='stock MOV argument and setter store destination; selected relocated table keeps stock prefix and bounds four PNG entries',
        preimage_scope='runtime cold-bootstrap root, independently observed on the visible workbench; not an original MAIN file window')
    item=dict(patch=Patch(HOOK,POINTER,address,struct.pack('<Q',original),struct.pack('<Q',replacement)),
        source='runtime-resource-root-supplement',file_offset=None,mapping='runtime-resource-root',
        purpose='cold-bootstrap cached native icon table root after late capsule installation')
    return [item],[info]


def build(stock,mask,controls_elf=None):
    verify_arena(stock)
    upstream,rows,recipe_id=_recipe(stock,mask)
    if any(upstream[CONTROLS_BEGIN-BASE:CONTROLS_END-BASE]):
        raise ValueError('upstream selected features overlap the reserved controls slot')
    controls=read_controls_elf(controls_elf,mask) if controls_elf is not None else None
    if controls:verify_controls_dependencies(controls,mask)
    native_text=derive_native_text(stock,upstream,rows)
    native_locale=verify_controls_locale(controls,native_text) if controls else None
    resources,native_resources=native_resource_supplements(stock,upstream,rows,mask)
    items=list(resources);boot=[];refinements=[]
    for row in rows:
        offset=int(row['offset'],0);n=row['bytes'];va=BASE+offset
        old=stock[offset:offset+n];new=upstream[offset:offset+n]
        if va==COPY_END_SITE:
            if n!=8 or old!=COPY_END_OLD:raise ValueError('unexpected BOOT_ONLY copy-end recipe')
            boot.append(dict(**row,classification='BOOT_ONLY',original=old.hex(),upstream=new.hex(),
                runtime_action='excluded; foundation reserves the full zero arena before heap initialization'))
            continue
        address,mapping=_runtime_address(offset,n)
        if ARENA_BEGIN<=va and va+n<=ARENA_END:
            if any(old):raise ValueError('IMAGE allocation is not original blank arena')
            matches=[(lo,hi) for lo,hi in CODE_AREAS if lo<=va<hi]
            flags=EXEC if matches else 0
            if matches and va+n>matches[0][1]:raise ValueError('native code window exceeds reviewed executable allocation')
            kind=IMAGE
        elif va<0x41300000:
            if mapping!='image-va' or va%4 or n%4:raise ValueError('unexpected native instruction window')
            kind=HOOK;flags=INSTRUCTION
        else:
            kind=HOOK;flags=POINTER if address%8==0 and n==8 and 0x40000000<=int.from_bytes(new,'little')<BASE+STOCK_BYTES else DATA
        items.append(dict(patch=Patch(kind,flags,address,old,new),source='upstream',file_offset=offset,
                          mapping=mapping,purpose=row['purpose']))
    if len(boot)!=1:raise ValueError('exactly one explicit BOOT_ONLY copy-end prerequisite expected')
    if controls:
        for segment in controls['segments']:
            n=segment['memory_bytes'];va=segment['va'];flags=EXEC if segment['flags']&1 else WRITE if segment['flags']&2 else 0
            old=stock[va-BASE:va-BASE+n]
            if any(old):raise ValueError('controls segment allocation is not blank stock')
            items.append(dict(patch=Patch(IMAGE,flags,va,old,segment['data']),source='controls',file_offset=va-BASE,
                              mapping='image-va',purpose='fixed native controls ELF PT_LOAD'))
        for site,name in MOV_HOOKS.items():
            at=site-BASE;old=stock[at:at+4];native=upstream[at:at+4]
            expected=struct.pack('<I',0xf9000268 if site==0x40cebe3c else 0xf81e0ff4)
            if old!=expected:raise ValueError('native MOV lifetime stock entry differs')
            if native!=old:
                if site!=0x40ceef10 or not mask&1 or native!=_word_branch(site,0x40960870):
                    raise ValueError('unapproved MOV hook overlap')
                matching=[x for x in items if x['patch'].address==site and len(x['patch'].new)==4]
                if len(matching)!=1:raise ValueError('upstream close refinement has no exact single record')
                items.remove(matching[0])
            refined=_word_branch(site,controls['symbols'][name])
            refinements.append(dict(site=hex(site),upstream=native.hex(),final=refined.hex(),symbol=name,
                preserves='upstream framing writer-close chain' if native!=old else 'original displaced instruction and continuation'))
            items.append(dict(patch=Patch(HOOK,INSTRUCTION,site,old,refined),source='controls-hook',file_offset=at,
                              mapping='image-va',purpose='MOV lifetime native controls entry'))
    items.sort(key=lambda x:(x['patch'].kind,x['patch'].address))
    patches=[x['patch'] for x in items]
    # A hook depending on a loaded branch/pointer target names that earlier IMAGE.
    for index,p in enumerate(patches):
        target=None
        if p.kind==HOOK and p.flags==INSTRUCTION and len(p.new)==4:
            word=int.from_bytes(p.new,'little')
            if word&0x7c000000==0x14000000:
                imm=word&0x3ffffff;target=p.address+((imm if imm<0x2000000 else imm-0x4000000)*4)
        elif p.kind==HOOK and p.flags==POINTER and len(p.new)==8:target=int.from_bytes(p.new,'little')
        if target is not None:
            for dep,candidate in enumerate(patches[:index]):
                if candidate.kind==IMAGE and candidate.address<=target<candidate.address+len(candidate.new):
                    patches[index]=Patch(p.kind,p.flags,p.address,p.old,p.new,dep);break
    arena=arena_profile()
    if arena.get('locale_contract',{}).get('count_hook_permitted') is not False:
        raise ValueError('foundation must separate native text bounds from accepted IDs')
    identity=dict(schema=SCHEMA,stock_main_sha256=STOCK_SHA256,features=selected(mask),
        recipe_id=recipe_id,upstream_main_sha256=sha(upstream),controls_elf_sha256=controls['sha256'] if controls else None,
        native_writes_sha256=sha(b''.join(struct.pack('<IIQ',p.kind,p.flags,p.address)+p.new for p in patches)))
    package_id=int.from_bytes(bytes.fromhex(sha(canonical(identity)))[:8],'little') or 1
    blob=encode(patches,package_id,arena['requires_id'],controls['entry'] if controls else 0)
    compiled=json.loads((VENDOR/'resources/compiled.json').read_text())
    manifest=dict(**identity,feature_mask=mask,source_commit='a21fc8c04b6dd863439c4192d387b755633287f6',
        provenance_sha256=PROVENANCE_SHA256,compiled_metadata_sha256=COMPILED_SHA256,
        combination_recipe_sha256=COMBINATIONS_SHA256,package_sha256=sha(blob),package_bytes=len(blob),
        package_id=package_id,requires_id=arena['requires_id'],index_sha256=sha(make_index(blob)),entry=hex(controls['entry']) if controls else '0x0',
        foundation=arena,boot_only=boot,refinements=refinements,native_text=native_text,native_locale=native_locale,native_resources=native_resources,native_exports=_native_exports(mask,compiled),
        controls_symbols={name:hex(controls['symbols'][name]) for name in MOV_HOOKS.values()} if controls else {},
        cold_start_only=True,physical_qualified=False,deployable=controls is not None,
        runtime_mapping='image-va records use native adapter writable aliases and instruction-cache synchronization; init-ram records already name RAM',
        records=[dict(index=i,kind=p.kind,flags=p.flags,address=hex(p.address),bytes=len(p.new),depends_on=p.depends,
            old_sha256=sha(p.old),new_sha256=sha(p.new),**{k:v for k,v in item.items() if k!='patch'})
            for i,(p,item) in enumerate(zip(patches,items))])
    verify_projection(stock,blob,manifest,upstream)
    return blob,manifest


def verify_projection(stock,blob,manifest,expected=None):
    verify_arena(stock)
    parsed=decode(blob)
    if manifest.get('schema')!=SCHEMA or manifest.get('package_sha256')!=sha(blob) or manifest.get('package_bytes')!=len(blob) or manifest.get('index_sha256')!=sha(make_index(blob)):
        raise ValueError('manifest package identity differs')
    mask=manifest.get('feature_mask');selected(mask)
    if manifest.get('features')!=selected(mask) or manifest.get('foundation')!=arena_profile() or parsed['requires_id']!=arena_profile()['requires_id']:
        raise ValueError('feature selection or foundation prerequisite differs')
    if parsed['package_id']!=manifest.get('package_id') or parsed['entry']!=int(manifest.get('entry','-1'),0):
        raise ValueError('manifest opaque identity/entry differs')
    metadata=manifest.get('records')
    if not isinstance(metadata,list) or len(metadata)!=len(parsed['patches']):raise ValueError('manifest record set differs')
    projection=bytearray(stock)
    for i,(p,row) in enumerate(zip(parsed['patches'],metadata)):
        if (row.get('index'),row.get('kind'),row.get('flags'),row.get('address'),row.get('bytes'),row.get('depends_on'),row.get('old_sha256'),row.get('new_sha256'))!=(i,p.kind,p.flags,hex(p.address),len(p.new),p.depends,sha(p.old),sha(p.new)):
            raise ValueError('manifest record metadata differs')
        at=row.get('file_offset')
        if row.get('source')=='runtime-resource-root-supplement':
            if at is not None or row.get('mapping')!='runtime-resource-root':
                raise ValueError('runtime resource-root supplement must not claim a stock file mapping')
            continue
        if type(at) is not int or not 0<=at<at+len(p.old)<=len(stock) or stock[at:at+len(p.old)]!=p.old:
            raise ValueError('record preimage differs from stock projection')
        address,mapping=_runtime_address(at,len(p.new))
        if p.address!=address or row.get('mapping')!=mapping:raise ValueError('runtime/file mapping differs')
        if row.get('source')=='upstream':projection[at:at+len(p.new)]=p.new
        elif row.get('source') not in ('controls','controls-hook'):raise ValueError('unknown projection record source')
    for row in manifest.get('boot_only',[]):
        at=int(row['offset'],0);new=bytes.fromhex(row['upstream'])
        if at!=COPY_END_SITE-BASE or row.get('classification')!='BOOT_ONLY' or row.get('original')!=COPY_END_OLD.hex() or len(new)!=8:
            raise ValueError('BOOT_ONLY projection metadata invalid')
        projection[at:at+8]=new
    refinements=manifest.get('refinements',[])
    for row in refinements:
        site=int(row['site'],0)
        if site not in MOV_HOOKS:raise ValueError('unapproved refinement site')
        at=site-BASE
        projection[at:at+4]=bytes.fromhex(row['upstream'])
    if expected is None:expected,_,_=_recipe(stock,mask)
    text_rows=[dict(purpose=r['purpose'],offset=hex(r['file_offset']),bytes=r['bytes']) for r in metadata if r['source']=='upstream']
    native_text=derive_native_text(stock,expected,text_rows)
    if manifest.get('native_text')!=native_text:
        raise ValueError('derived native text bounds differ')
    if manifest.get('deployable'):
        locale=manifest.get('native_locale')
        if not isinstance(locale,dict):raise ValueError('locale descriptor metadata missing')
        symbols={'fx_controls_locale':int(locale.get('address','-1'),0),'fx_controls_locale_export':int(locale.get('export_address','-1'),0)}
        image=dict(symbols=symbols,symbol_sizes={'fx_controls_locale':32,'fx_controls_locale_export':80},segments=[dict(va=p.address,memory_bytes=len(p.new),flags=5 if p.flags==EXEC else 6 if p.flags==WRITE else 4,data=p.new) for p in parsed['patches'] if p.kind==IMAGE])
        if verify_controls_locale(image,native_text)!=locale:raise ValueError('locale descriptor metadata differs')
    elif manifest.get('native_locale') is not None:raise ValueError('upstream-only diagnostics cannot claim a locale provider')
    if any(p.address<int(native_text['count_address'],0)+4 and int(native_text['count_address'],0)<p.address+len(p.new) for p in parsed['patches']):
        raise ValueError('native acceptance limit must not be rewritten by a capsule')
    resources,native_resources=native_resource_supplements(stock,expected,text_rows,mask)
    actual_resources=[p for p,r in zip(parsed['patches'],metadata) if r['source']=='runtime-resource-root-supplement']
    # Dependencies are assigned after sorting; retain the exact independently
    # derived target dependency when comparing this supplemental pointer.
    wanted=[]
    for item in resources:
        p=item['patch'];target=int.from_bytes(p.new,'little')
        deps=[i for i,q in enumerate(parsed['patches']) if q.kind==IMAGE and q.address<=target<q.address+len(q.new)]
        if len(deps)!=1:raise ValueError('resource-root target has no exact loaded IMAGE')
        wanted.append(Patch(p.kind,p.flags,p.address,p.old,p.new,deps[0]))
    if manifest.get('native_resources')!=native_resources or actual_resources!=wanted:
        raise ValueError('derived native resource-root supplement differs')
    if bytes(projection)!=expected or manifest.get('upstream_main_sha256')!=sha(expected):
        raise ValueError('de-layered capsule does not reconstruct the exact selected upstream MAIN')
    return dict(passed=True,package_sha256=sha(blob),stock_main_sha256=STOCK_SHA256,
        upstream_main_sha256=sha(expected),features=selected(mask),records=len(parsed['patches']),
        boot_only_windows=len(manifest.get('boot_only',[])),physical_qualified=False)


def create(firmware,output,mask,controls=None,upstream_only=False):
    if (controls is None)!=upstream_only:raise ValueError('provide controls ELF, or explicitly request nondeployable upstream-only diagnostics')
    out=Path(output).resolve()
    if not out.is_relative_to(ROOT/'build') or out.exists():raise ValueError('a new owned build output directory is required')
    stock=read_main(firmware)
    controls_bytes=Path(controls).read_bytes() if controls else None
    blob,manifest=build(stock,mask,controls_bytes)
    # All provenance, preimages, bounds, reconstruction and input reads finish
    # before creating any output. Existing source, firmware and deliveries stay intact.
    out.mkdir(parents=True)
    (out/'FENESTRA').mkdir()
    (out/'FENESTRA/BOOT.FNC').write_bytes(blob)
    (out/'FENESTRA/BOOT.IDX').write_bytes(make_index(blob))
    if controls_bytes is not None:(out/'controls.elf').write_bytes(controls_bytes)
    (out/'capsule.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
    return manifest


def verify_directory(stock,directory):
    source=Path(directory).resolve(strict=True)
    manifest=json.loads((source/'capsule.json').read_text(encoding='utf-8'))
    blob=(source/'FENESTRA/BOOT.FNC').read_bytes()
    verify_index((source/'FENESTRA/BOOT.IDX').read_bytes(),blob)
    result=verify_projection(stock,blob,manifest)
    controls=(source/'controls.elf').read_bytes() if manifest.get('deployable') else None
    rebuilt,expected=build(stock,manifest['feature_mask'],controls)
    if blob!=rebuilt or manifest!=expected:
        raise ValueError('capsule differs from exact controls ELF and pinned recipe regeneration')
    result['controls_regeneration_verified']=controls is not None
    return blob,manifest,result



def create_catalog(firmware,controls_root,output):
    out=Path(output).resolve()
    if not out.is_relative_to(ROOT/'build') or out.exists():raise ValueError('a new owned build output directory is required')
    stock=read_main(firmware);source=Path(controls_root).resolve(strict=True)
    products=[]
    for mask in range(1,16):
        control=(source/str(mask)/'controls.elf').read_bytes()
        blob,manifest=build(stock,mask,control)
        products.append((mask,blob,manifest,control))
    # Publish nothing until every selected native image and prerequisite passes.
    out.mkdir(parents=True)
    rows=[]
    for mask,blob,manifest,control in products:
        directory=out/str(mask);(directory/'FENESTRA').mkdir(parents=True)
        (directory/'FENESTRA/BOOT.FNC').write_bytes(blob)
        (directory/'FENESTRA/BOOT.IDX').write_bytes(make_index(blob))
        (directory/'capsule.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
        (directory/'controls.elf').write_bytes(control)
        rows.append(dict(feature_mask=mask,features=selected(mask),directory=str(mask),
            package_sha256=sha(blob),package_bytes=len(blob),package_id=manifest['package_id']))
    catalog=dict(schema='fenestra.z8-native-capsule-catalog/v1',stock_main_sha256=STOCK_SHA256,
        foundation=arena_profile(),packages=rows,cold_start_only=True,physical_qualified=False)
    (out/'catalog.json').write_text(json.dumps(catalog,indent=2)+'\n',encoding='utf-8',newline='\n')
    return catalog


def compose_selection(firmware,catalog_dir,output,mask):
    out=Path(output).resolve()
    if not out.is_relative_to(ROOT/'build') or out.exists():raise ValueError('a new owned build output directory is required')
    selected(mask)
    source=Path(catalog_dir).resolve(strict=True)
    catalog=json.loads((source/'catalog.json').read_text(encoding='utf-8'))
    if catalog.get('schema')!='fenestra.z8-native-capsule-catalog/v1' or catalog.get('stock_main_sha256')!=STOCK_SHA256 or catalog.get('foundation')!=arena_profile():
        raise ValueError('catalog target or foundation differs')
    entries=catalog.get('packages')
    if not isinstance(entries,list) or sorted(row.get('feature_mask',0) for row in entries)!=list(range(1,16)):
        raise ValueError('catalog must describe exactly all fifteen combinations')
    row=next(row for row in entries if row['feature_mask']==mask)
    directory=(source/row['directory']).resolve(strict=True)
    if not directory.is_relative_to(source):raise ValueError('catalog selection escapes its source directory')
    stock=read_main(firmware);blob,manifest,_=verify_directory(stock,directory)
    if manifest['feature_mask']!=mask or sha(blob)!=row['package_sha256'] or len(blob)!=row['package_bytes'] or manifest['package_id']!=row['package_id']:
        raise ValueError('catalog selection identity differs')
    out=Path(output).resolve()
    if not out.is_relative_to(ROOT/'build') or out.exists():raise ValueError('a new owned build output directory is required')
    control=(directory/'controls.elf').read_bytes()
    if sha(control)!=manifest['controls_elf_sha256']:raise ValueError('controls ELF changed during selection')
    out.mkdir(parents=True);(out/'FENESTRA').mkdir()
    (out/'FENESTRA/BOOT.FNC').write_bytes(blob)
    (out/'FENESTRA/BOOT.IDX').write_bytes(make_index(blob))
    (out/'controls.elf').write_bytes(control)
    (out/'capsule.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
    return dict(features=selected(mask),package_sha256=sha(blob),package_bytes=len(blob),cold_start_required=True)


def install(firmware,capsule_dir,card_root,replace=False):
    blob,manifest,_=verify_directory(read_main(firmware),capsule_dir)
    if not manifest.get('deployable') or not decode(blob)['entry']:
        raise ValueError('diagnostic upstream-only packages cannot be installed')
    card=Path(card_root).resolve(strict=True)
    # Source/build archives and historical deliveries can never be install roots.
    if card.is_relative_to(ROOT) or ROOT.is_relative_to(card):raise ValueError('card root must be outside the workspace')
    target=(card/'FENESTRA').resolve()
    if not target.is_relative_to(card):raise ValueError('card FENESTRA path escapes selected card root')
    destination=target/'BOOT.FNC';index_path=target/'BOOT.IDX'
    if (destination.exists() or index_path.exists()) and not replace:
        raise ValueError('BOOT.FNC/BOOT.IDX exists; use --replace for an intentional cold-start update')
    if destination.is_symlink() or index_path.is_symlink():raise ValueError('symlink is not an install target')
    target.mkdir(exist_ok=True)
    index=make_index(blob)
    pending=[(destination,blob),(index_path,index)];staged=[]
    try:
        for final,data in pending:
            temporary=target/(final.name+'.tmp-'+sha(blob)[:16])
            with temporary.open('xb') as stream:
                staged.append((temporary,final))
                stream.write(data);stream.flush();os.fsync(stream.fileno())
        # Cold-start updates only. Publishing the index last makes an interrupted
        # two-file update fail closed on the old/new full-file SHA mismatch.
        for temporary,final in staged:os.replace(temporary,final)
    finally:
        for temporary,_ in staged:
            if temporary.exists():temporary.unlink()
    if destination.read_bytes()!=blob or index_path.read_bytes()!=index:
        raise ValueError('installed card capsule/index bytes failed readback')
    return dict(installed=str(destination),package_sha256=sha(blob),cold_start_required=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    make=commands.add_parser('create');make.add_argument('--firmware',type=Path,required=True)
    make.add_argument('--features',type=feature_mask,required=True);make.add_argument('--output-dir',type=Path,required=True)
    native=make.add_mutually_exclusive_group(required=True)
    native.add_argument('--controls',type=Path);native.add_argument('--upstream-only',action='store_true')
    check=commands.add_parser('verify');check.add_argument('--firmware',type=Path,required=True);check.add_argument('--capsule-dir',type=Path,required=True)
    check.add_argument('--controls',type=Path)
    put=commands.add_parser('install');put.add_argument('--firmware',type=Path,required=True);put.add_argument('--capsule-dir',type=Path,required=True)
    put.add_argument('--card-root',type=Path,required=True);put.add_argument('--replace',action='store_true')
    allpacks=commands.add_parser('create-catalog')
    allpacks.add_argument('--firmware',type=Path,required=True);allpacks.add_argument('--controls-root',type=Path,required=True)
    allpacks.add_argument('--output-dir',type=Path,required=True)
    compose=commands.add_parser('compose')
    compose.add_argument('--firmware',type=Path,required=True);compose.add_argument('--catalog-dir',type=Path,required=True)
    compose.add_argument('--features',type=feature_mask,required=True);compose.add_argument('--output-dir',type=Path,required=True)
    commands.add_parser('arena-profile')
    args=parser.parse_args(argv)
    if args.command=='create':
        manifest=create(args.firmware,args.output_dir,args.features,args.controls,args.upstream_only)
        result={k:manifest[k] for k in ('features','package_sha256','package_bytes','deployable','physical_qualified')}
    elif args.command=='verify':
        directory=args.capsule_dir;stock=read_main(args.firmware)
        blob,manifest,result=verify_directory(stock,directory)
        if args.controls:
            rebuilt,expected=build(stock,manifest['feature_mask'],args.controls.read_bytes())
            if blob!=rebuilt or manifest!=expected:raise ValueError('capsule differs from exact controls ELF and pinned recipe regeneration')
            result['controls_regeneration_verified']=True
    elif args.command=='create-catalog':
        catalog=create_catalog(args.firmware,args.controls_root,args.output_dir)
        result=dict(packages=len(catalog['packages']),foundation_sha256=catalog['foundation']['profile_sha256'],physical_qualified=False)
    elif args.command=='compose':result=compose_selection(args.firmware,args.catalog_dir,args.output_dir,args.features)
    elif args.command=='install':result=install(args.firmware,args.capsule_dir,args.card_root,args.replace)
    else:result=arena_profile()
    print(json.dumps(result))

if __name__=='__main__':main()
