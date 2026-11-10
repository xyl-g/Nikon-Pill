"""Build independently owned fixed-address native components for WLM v4.

Only new outputs are created. Native bytes remain in their owning component;
shared exact variants never contain another feature's backend.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from z8_wlm_native_recipes import (BITS,COMPONENTS,compose_recipe,component_windows,sha,BASE)
from z8_native_capsule import (Patch,encode,decode,IMAGE,HOOK,EXEC,WRITE,INSTRUCTION,POINTER,DATA,NONE,
 CODE_AREAS,_spans_disjoint,_elf_symbol_bytes,_word_branch,derive_native_text,native_resource_supplements)
from z8_native_arena import arena_profile,verify_arena,ARENA_BEGIN,ARENA_END
from z8_native_profile import check_main

SLOTS={'shared.movie':0x408d0000,'prores':0x408e0000,'h265':0x408f0000,
 'log_colour':0x40900000,'framing':0x40910000,'photo_tools':0x40920000,'shared.locale':0x40930000}
RANKS={**{name:10+i for i,name in enumerate(COMPONENTS)},'shared.locale':90,'shared.menu':0,'shared.quality':0,'shared.movie':100}
DEPENDENCIES={'framing':('controls.movie_close_original.framing',0x40960870),
 'prores':('controls.dependency.tier_republish.prores',0x40a41250),
 'h265':('controls.dependency.tier_republish.h265',0x40a41250),
 'photo_tools':('controls.dependency.psm_worker.photo_tools',0x409a44cc)}


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def verify_elf_metadata(result,m):
 component=result['component'];bit=BITS.get(component,0)
 if m.get('component_id')!=component or m.get('features5')!=bit or m.get('features')!=bit:raise ValueError('component metadata selection differs')
 if m.get('fixed_base')!=SLOTS[component] or m.get('limit')!=SLOTS[component]+65536:raise ValueError('component slot differs')
 if m.get('stage_rank')!=RANKS[component] or m.get('entry')!=result['entry']:raise ValueError('component entry/rank differs')
 if m.get('elf_sha256')!=result['sha256']:raise ValueError('component ELF full SHA differs')
 if m.get('symbols')!=result['symbols']:raise ValueError('component symbols differ from actual ELF')
 segments=m.get('segments',[])
 if len(segments)!=len(result['segments']):raise ValueError('component segment count differs')
 for expected,actual in zip(segments,result['segments']):
  for key in ('va','flags','file_offset','file_bytes','memory_bytes'):
   if expected.get(key)!=actual[key]:raise ValueError('component segment '+key+' differs')
  if expected.get('sha')!=sha(actual['data'][:actual['file_bytes']]) or expected.get('memory_sha256')!=sha(actual['data']):raise ValueError('component segment digest differs')
 expected_deps=[] if component not in DEPENDENCIES else [dict(name=DEPENDENCIES[component][0],version=1,address=DEPENDENCIES[component][1])]
 if m.get('dependencies')!=expected_deps:raise ValueError('component dependency aliases differ')
 if bit:
  expected=[dict(name='controls.movie_open.'+component,version=1,flags=1,address=result['symbols']['fx_wlm_movie_open']),dict(name='controls.movie_close.'+component,version=1,flags=1,address=result['symbols']['fx_wlm_movie_close'])]
  expected += [dict(**d,flags=1)for d in expected_deps]
  dep_raw=_elf_symbol_bytes(result,'fx_wlm_dependency',80)
  count=struct.unpack('<I',_elf_symbol_bytes(result,'fx_wlm_dependency_count',4))[0]
  if count!=len(expected_deps):raise ValueError('component embedded dependency count differs')
  if expected_deps:
   d=expected_deps[0];raw=d['name'].encode().ljust(64,b'\0')+struct.pack('<IIQ',1,1,d['address'])
   if dep_raw!=raw:raise ValueError('component embedded dependency differs')
  elif any(dep_raw):raise ValueError('disabled dependency is nonzero')
 elif component=='shared.movie':
  expected=[dict(name='controls.video_open_hook',version=1,flags=1,address=result['symbols']['fx_wlm_video_open_hook']),dict(name='controls.video_close_hook',version=1,flags=1,address=result['symbols']['fx_wlm_video_close_hook'])]
 else:expected=[dict(name='camera.locale.text',version=1,flags=2,address=result['symbols']['fx_wlm_locale'])]
 if m.get('exports')!=expected:raise ValueError('component exact export metadata differs')
 result['exports']=expected


def read_component_elf(blob,component,metadata):
    base=SLOTS[component];mask=BITS.get(component,0)
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
        if not base<=va<va+memory_bytes<=base+65536 or physical!=va or alignment not in (0,1,4096,65536):
            raise ValueError('controls segment outside the fixed reserved slot')
        if alignment>1 and (va-at)%alignment:raise ValueError('controls segment alignment invalid')
        data=blob[at:at+file_bytes]+bytes(memory_bytes-file_bytes)
        segments.append(dict(va=va,flags=fl,file_offset=at,file_bytes=file_bytes,memory_bytes=memory_bytes,data=data))
    if not segments or min(s['va'] for s in segments)!=base:
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
    for name in ('fx_native_pack_entry',):
        if name not in symbols or not any(s['flags']&1 and s['va']<=symbols[name]<s['va']+s['memory_bytes'] for s in segments):
            raise ValueError('required controls entry/hook symbol missing: '+name)
    if entry!=symbols['fx_native_pack_entry'] or entry%4:raise ValueError('controls entry differs from exported pack entry')
    # Optional absolute mask marker emitted by the builder must match selection.
    if symbols.get('fx_native_pack_feature_mask')!=mask:
        raise ValueError('controls feature selection differs')
    result=dict(entry=entry,symbols=symbols,symbol_sizes=symbol_sizes,segments=segments,sha256=sha(blob),component=component)
    verify_elf_metadata(result,metadata)
    return result




def window_patch(w):
 va=BASE+w.offset;address=w.address;n=len(w.new)
 if ARENA_BEGIN<=va and va+n<=ARENA_END:
  if any(w.old):raise ValueError('native IMAGE is not original zero arena')
  code=[(lo,hi)for lo,hi in CODE_AREAS if lo<=va<hi]
  if code and va+n>code[0][1]:raise ValueError('native code escaped reviewed allocation')
  return Patch(IMAGE,EXEC if code else 0,address,w.old,w.new)
 if va<0x41300000:
  if va!=address or va%4 or n%4:raise ValueError('invalid native instruction window')
  flags=INSTRUCTION
 else:flags=POINTER if address%8==0 and n==8 and BASE<=int.from_bytes(w.new,'little')<BASE+81807709 else DATA
 if address<0xa0281cd60 and 0xa0281cd5c<address+n:raise ValueError('native accepted-count mutation is prohibited')
 return Patch(HOOK,flags,address,w.old,w.new)


def text_layout(stock,recipe):
 rows=[w.metadata()for w in recipe.windows]
 text=derive_native_text(stock,recipe.image,rows)
 return dict(languages=text['languages'],text_count=text['loaded_count'],offsets_directory=int(text['offsets_directory'],0),pools_directory=int(text['pools_directory'],0))


def component_capsule(stock,recipe,owner,controls=None):
 patches=[window_patch(w)for w in component_windows(recipe,owner)] if owner!='shared.movie'else []
 if owner=='log_colour':
  resource_rows=[dict(w.metadata(),purpose=('log: '+w.purpose if w.owner=='log_colour' else w.purpose))for w in recipe.windows]
  items,_=native_resource_supplements(stock,recipe.image,resource_rows,4)
  patches.extend(x['patch']for x in items)
 if controls:
  if controls['component']!=owner:raise ValueError('component ELF owner differs')
  for segment in controls['segments']:
   at=segment['va']-BASE;n=segment['memory_bytes'];old=stock[at:at+n]
   if len(old)!=n or any(old):raise ValueError('independent ELF segment is not original blank arena')
   flags=EXEC if segment['flags']&1 else WRITE if segment['flags']&2 else 0
   patches.append(Patch(IMAGE,flags,segment['va'],old,segment['data']))
  if owner=='shared.locale':
   expected=text_layout(stock,recipe);raw=struct.pack('<4I2Q',1,32,expected['languages'],expected['text_count'],expected['offsets_directory'],expected['pools_directory'])
   if _elf_symbol_bytes(controls,'fx_wlm_locale',32)!=raw:raise ValueError('locale descriptor differs from actual recipe tables')
   va=controls['symbols']['fx_wlm_locale']
   if not any(not s['flags']&2 and s['va']<=va and va+32<=s['va']+s['memory_bytes']for s in controls['segments']):raise ValueError('locale descriptor is not sealed read-only')
  if owner=='shared.movie':
   for site,symbol,word in ((0x40cebe3c,'fx_wlm_video_open_hook',0xf9000268),(0x40ceef10,'fx_wlm_video_close_hook',0xf81e0ff4)):
    old=stock[site-BASE:site-BASE+4]
    if old!=struct.pack('<I',word):raise ValueError('MOV stock instruction preimage differs')
    patches.append(Patch(HOOK,INSTRUCTION,site,old,_word_branch(site,controls['symbols'][symbol])))
 patches.sort(key=lambda p:(p.kind,p.address))
 if not patches:raise ValueError('empty native component')
 # Identity is local: no selection mask, unrelated recipe MAIN or other ELFs.
 identity=hashlib.sha256(owner.encode()+b'\0'+b''.join(struct.pack('<IIQI',p.kind,p.flags,p.address,len(p.new))+p.old+p.new for p in patches)).digest()
 dependency=arena_profile()['requires_id'];dependency=int(dependency,0)if isinstance(dependency,str)else dependency
 return encode(patches,int.from_bytes(identity[:8],'little')or 1,dependency,controls['entry']if controls else 0)


def verify_collection(stock,recipe,components):
 destinations=[];records=[]
 for owner,blob in components.items():
  decoded=decode(blob)
  for row in decoded['patches']:
   address=row.address;old=row.old;new=row.new
   destinations.append((address,address+len(new)));records.append((address,old,new,owner))
 _spans_disjoint(destinations,'selected component destination')
 by_address={a:(old,new,owner)for a,old,new,owner in records}
 for window in recipe.windows:
  if window.owner=='foundation.reserve-only' or window.address==0x40ceef10:continue
  if by_address.get(window.address)!=(window.old,window.new,window.owner):raise ValueError('selected components do not reconstruct native recipe window')
 if 0xa0281cd5c in by_address:raise ValueError('forbidden native global count HOOK')
 return dict(records=len(records),write_bytes=sum(len(new)for _,_,new,_ in records),owners=sorted(components),recipe_sha256=sha(recipe.image),exact_projection=True)


def create_catalog(firmware,controls_root,output):
 stock=Path(firmware).read_bytes();check_main(stock);verify_arena(stock)
 controls_root=Path(controls_root).resolve();output=Path(output).resolve()
 if output.exists():raise FileExistsError('new native catalog output required')
 workspace=Path(__file__).resolve().parents[1]
 if not output.is_relative_to(workspace/'build'):raise ValueError('catalog output must be a new workspace build directory')
 if output==controls_root or output in controls_root.parents or controls_root in output.parents:raise ValueError('catalog and controls input directories must be disjoint')
 controls={}
 for owner in SLOTS:
  dirname='bridge'if owner=='shared.movie'else'locale'if owner=='shared.locale'else owner
  directory=controls_root/dirname
  controls[owner]=read_component_elf((directory/'controls.elf').read_bytes(),owner,json.loads((directory/'controls.json').read_text()))
 variants={};verification=[];layouts={}
 for mask in range(1,32):
  recipe=compose_recipe(stock,mask);layout=text_layout(stock,recipe);layouts[mask]=layout
  selected_owners=[name for name in COMPONENTS if mask&BITS[name]]+['shared.locale','shared.movie']
  if mask&15:selected_owners.append('shared.menu')
  if mask&6:selected_owners.append('shared.quality')
  collection={}
  for owner in selected_owners:
   selector_mask=31 if owner=='shared.locale'else 15 if owner=='shared.menu'else 6 if owner=='shared.quality'else 0
   value=mask&selector_mask
   blob=component_capsule(stock,recipe,owner,controls.get(owner));key=(owner,value)
   if key in variants and variants[key]['capsule']!=blob:raise ValueError('component changes with unrelated installed plugin: '+owner)
   variants[key]=dict(mask=selector_mask,value=value,capsule=blob)
   collection[owner]=blob
  verification.append(dict(mask=mask,**verify_collection(stock,recipe,collection)))
 if len({canonical(v)for v in layouts.values()})!=1:raise ValueError('locale descriptor unexpectedly needs conditional entry ELF')
 output.mkdir(parents=True)
 component_rows=[]
 for owner in (*COMPONENTS,'shared.locale','shared.menu','shared.quality','shared.movie'):
  directory=output/owner;directory.mkdir();rows=[]
  for (name,value),variant in sorted(variants.items()):
   if name!=owner:continue
   filename=str(value)+'.fnc';blob=variant['capsule'];(directory/filename).write_bytes(blob)
   rows.append(dict(mask=variant['mask'],value=value,capsule=owner+'/'+filename,bytes=len(blob),sha256=sha(blob)))
  component_rows.append(dict(id=owner,version=1,selector_bit=BITS.get(owner,0),rank=RANKS[owner],variants=rows))
 result=dict(schema='fenestra.wlm-native-component-catalog/v1',target_main_sha256=sha(stock),arena_profile=arena_profile(),feature_bits=BITS,
  components=component_rows,selection_verification=verification,locale_descriptor=next(iter(layouts.values())),
  controls={k:dict(elf_sha256=v['sha256'],entry=hex(v['entry']),exports=v['exports'])for k,v in controls.items()},
  qualification=dict(pinned_recipe_reconstruction=True,all_31_owner_combinations=True,native_transaction_executed=False,physical_camera=False))
 (output/'catalog.json').write_text(json.dumps(result,indent=2)+'\n')
 return result


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--stock',type=Path,required=True);parser.add_argument('--controls',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
 args=parser.parse_args();result=create_catalog(args.stock,args.controls,args.out)
 print(json.dumps(dict(passed=True,components=len(result['components']),selections=len(result['selection_verification']),catalog=str(args.out/'catalog.json'))))

if __name__=='__main__':main()
