"""Build independent WLM native control components; never access a camera."""
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
LLVM = Path('C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/Llvm/x64/bin')
PACK = 'nativepacks/wlm_controls/'
FEATURES = {'framing': 1, 'prores': 2, 'h265': 4, 'log_colour': 8, 'photo_tools': 16}
BASES = {'bridge': 0x408d0000, 'prores': 0x408e0000, 'h265': 0x408f0000,
         'log_colour': 0x40900000, 'framing': 0x40910000, 'photo_tools': 0x40920000,
         'locale': 0x40930000}
DEPENDENCIES = {
    'prores': ('controls.dependency.tier_republish.prores', 0x40a41250, 'quality', 'tier_star_republish'),
    'h265': ('controls.dependency.tier_republish.h265', 0x40a41250, 'quality', 'tier_star_republish'),
    'framing': ('controls.movie_close_original.framing', 0x40960870, 'opengate', 'writer_close_hook'),
    'photo_tools': ('controls.dependency.psm_worker.photo_tools', 0x409a44cc, 'pixelshift', 'psm_worker')}

def sha(data): return hashlib.sha256(data).hexdigest()

def load_segments(elf, base):
    if elf[:7] != b'\x7fELF\x02\x01\x01': raise ValueError('ELF64 LE required')
    typ,machine,version,entry,phoff,shoff,flags,ehsize,phsize,phcount,shsize,shcount,shnames = struct.unpack_from('<HHIQQQIHHHHHH', elf, 16)
    if (typ,machine,version,ehsize,phsize,shsize) != (2,183,1,64,56,64): raise ValueError('AArch64 ET_EXEC required')
    segments = []
    for i in range(phcount):
        kind,perm,off,va,physical,filesz,memsz,alignment = struct.unpack_from('<IIQQQQQQ',elf,phoff+56*i)
        if kind in (2,3,7): raise ValueError('no dynamic interpreter or TLS')
        if kind != 1 or not memsz: continue
        if perm not in (4,5,6) or filesz>memsz or off+filesz>len(elf): raise ValueError('invalid load segment')
        if va < base or va+memsz > base+65536: raise ValueError('component escaped its independent slot')
        raw=elf[off:off+filesz]
        segments.append(dict(va=va,flags=perm,file_offset=off,file_bytes=filesz,memory_bytes=memsz,
                             alignment=alignment,sha=sha(raw),memory_sha256=sha(raw+bytes(memsz-filesz))))
    for i in range(shcount):
        _,kind,_,_,_,size,_,_,_,_ = struct.unpack_from('<IIQQQQIIQQ',elf,shoff+64*i)
        if kind in (4,9) and size: raise ValueError('runtime relocations are unsupported')
    if not any(s['flags']&1 and s['va']<=entry< s['va']+s['file_bytes'] for s in segments): raise ValueError('entry outside EXEC')
    return entry,segments

def validate_locale(locale):
    if not isinstance(locale,dict) or set(locale)!={'languages','text_count','offsets_directory','pools_directory'}: raise ValueError('exact locale descriptor fields required')
    if any(type(value) is not int for value in locale.values()): raise ValueError('integer locale fields required')
    if not 0<locale['text_count']<=8192 or locale['languages']!=36: raise ValueError('invalid Z8 locale dimensions')
    for name in ('offsets_directory','pools_directory'):
        if not 0<locale[name]<2**64 or locale[name]%8: raise ValueError('unaligned locale directory')

def build(component, out, llvm, locale=None):
    out.mkdir(parents=True,exist_ok=True)
    def tool(name):
        path=llvm/(name+('.exe' if sys.platform=='win32' else ''))
        if not path.is_file(): raise FileNotFoundError(path)
        return str(path)
    cc,ld,nm=(tool(n) for n in ('clang','ld.lld','llvm-nm'))
    bit=FEATURES.get(component,0);base=BASES[component]
    metadata_path=ROOT/'vendor/NikonFirmwarePatch-features/resources/compiled.json'
    metadata_raw=metadata_path.read_bytes();native=json.loads(metadata_raw)['units']
    dep=DEPENDENCIES.get(component)
    if dep and int(native[dep[2]]['symbols'][dep[3]]['va'],0)!=dep[1]: raise ValueError('pinned native dependency changed')
    generated=[]
    if bit:
        header=['/* Generated exact selected native dependency; no feature aggregation. */',
                '#define FX_WLM_DEPENDENCY_COUNT '+str(int(dep is not None))+'u',
                '#define FX_WLM_TIER_REPUBLISH UINT64_C('+hex(dep[1] if component in ('prores','h265') else 0)+')',
                '#define FX_WLM_PSM_WORKER UINT64_C('+hex(dep[1] if component=='photo_tools' else 0)+')',
                'const uint8_t fx_wlm_dependency_digest[32]={'+','.join(hex(v) for v in hashlib.sha256(metadata_raw).digest())+'};',
                'const uint32_t fx_wlm_dependency_count='+str(int(dep is not None))+'u;',
                'const FxNativePackSymbol fx_wlm_dependency='+
                    ('{.name="'+dep[0]+'",.version=1u,.flags=FX_NATIVE_PACK_SYMBOL_FUNCTION,.address=UINT64_C('+hex(dep[1])+')};' if dep else '{0};')]
        path=out/'fx_wlm_dependencies.h';path.write_text('\n'.join(header)+'\n',encoding='utf-8');generated.append(path)
    if component=='locale':
        if not locale: raise ValueError('locale needs explicit selected text descriptor')
        validate_locale(locale)
        header=['/* Descriptor for the exact selected native text tables. */']
        for name,value in locale.items():
            macro='FX_WLM_'+name.upper();header.append('#define '+macro+' '+('UINT64_C('+hex(value)+')' if name.endswith('directory') else str(value)+'u'))
        path=out/'fx_wlm_locale.h';path.write_text('\n'.join(header)+'\n',encoding='utf-8');generated.append(path)
    if component=='bridge':
        sources=[PACK+'movie_bridge.c',PACK+'hooks.S','src/memory.c'];entry_name='fx_wlm_movie_bridge_entry';rank=100
    elif component=='locale':
        sources=[PACK+'locale.c'];entry_name='fx_wlm_locale_entry';rank=90
    else:
        sources=[PACK+'backend.c','src/suite_video.c','src/memory.c'];entry_name='fx_wlm_pack_entry';rank=10+list(FEATURES).index(component)
        if component=='framing': sources+=['src/framing.c']
        if component=='photo_tools': sources+=['nativepacks/feature_controls/photo_backend.c','nativepacks/feature_controls/precapture_subscription.c','src/photo_tools.c']
    commands=[];inputs=set(sources);objects=[]
    def run(command):
        subprocess.run(command,cwd=ROOT,check=True,timeout=60);commands.append(command)
    for i,source in enumerate(sources):
        obj=out/(str(i)+'.o');depfile=out/(str(i)+'.d')
        flags=['--target=aarch64-none-elf','-std=c11','-O2','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow',
               '-ffreestanding','-fno-builtin','-mgeneral-regs-only','-mstrict-align','-mno-outline-atomics',
               '-fno-stack-protector','-fno-unwind-tables','-fno-asynchronous-unwind-tables',
               '-ffunction-sections','-fdata-sections','-I','include','-I','nativepacks/wlm_controls','-I',str(out),
               '-DFX_FREESTANDING=1','-DFX_WLM_FEATURE='+str(bit)]
        if source.endswith('.S'): flags=['--target=aarch64-none-elf']
        run([cc,*flags,'-MMD','-MF',str(depfile),'-MT','fx_core','-c',source,'-o',str(obj)])
        inputs.update(dependencies(depfile,ROOT));objects.append(str(obj))
    elf_path=out/'controls.elf'
    extra=['--undefined=fx_wlm_component_metadata']
    if bit: extra+=['--undefined=fx_wlm_dependency_digest','--undefined=fx_wlm_dependency_count','--undefined=fx_wlm_dependency']
    run([ld,'-m','aarch64elf','-z','max-page-size=4096','-T',PACK+'controls.ld','--gc-sections',
         '--defsym=fx_wlm_base='+hex(base),'--defsym=fx_native_pack_entry='+entry_name,
         '--defsym=fx_native_pack_feature_mask='+str(bit),*extra,*objects,'-o',str(elf_path)])
    elf=elf_path.read_bytes();entry,segments=load_segments(elf,base)
    symbols={p[2]:int(p[0],16) for line in subprocess.check_output([nm,'--defined-only','--extern-only',str(elf_path)],text=True).splitlines() if len(p:=line.split())==3}
    for i,segment in enumerate(segments):
        name='controls.segment'+str(i)+'.bin';start=segment['file_offset'];(out/name).write_bytes(elf[start:start+segment['file_bytes']]);segment['file']=name
    inputs.update([PACK+'build.py',PACK+'controls.ld','vendor/NikonFirmwarePatch-features/resources/compiled.json'])
    if bit:
        exports=[dict(name='controls.movie_open.'+component,version=1,flags=1,address=symbols['fx_wlm_movie_open']),dict(name='controls.movie_close.'+component,version=1,flags=1,address=symbols['fx_wlm_movie_close'])]
        if dep: exports.append(dict(name=dep[0],version=1,flags=1,address=dep[1]))
    elif component=='bridge':
        exports=[dict(name='controls.video_open_hook',version=1,flags=1,address=symbols['fx_wlm_video_open_hook']),dict(name='controls.video_close_hook',version=1,flags=1,address=symbols['fx_wlm_video_close_hook'])]
    else: exports=[dict(name='camera.locale.text',version=1,flags=2,address=symbols['fx_wlm_locale'])]
    providers=[] if not bit else [dict(name={'framing':'camera.video.framing','prores':'camera.video.prores','h265':'camera.video.h265','log_colour':'camera.video.log_colour','photo_tools':'camera.photo.tools'}[component],version=1)]
    result=dict(schema=1,abi=1,component=component,component_id=(component if bit else 'shared.'+('movie' if component=='bridge' else 'locale')),
                exports=exports,providers=providers,kind='control' if bit else 'shared',features=bit,features5=bit,feature_bits=FEATURES,
                fixed_base=base,limit=base+65536,entry=entry,stage_rank=rank,
                dependencies=[] if not dep else [dict(name=dep[0],version=1,address=dep[1])],
                native_metadata_sha256=sha(metadata_raw),locale_descriptor=locale if component=='locale' else None,
                elf=str(elf_path),elf_sha256=sha(elf),symbols=symbols,segments=segments,
                sources_sha256={name:sha((ROOT/name).read_bytes()) for name in sorted(inputs)},
                generated_sha256={str(p.name):sha(p.read_bytes()) for p in generated},
                compiler=subprocess.check_output([cc,'--version'],text=True).splitlines()[0],commands=commands,
                resident=True,native_execution_tested=False)
    (out/'controls.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',choices=['all',*BASES],default='all')
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--llvm',type=Path,default=LLVM)
    parser.add_argument('--text-count',type=lambda v:int(v,0));parser.add_argument('--languages',type=int,default=36)
    parser.add_argument('--offsets-directory',type=lambda v:int(v,0));parser.add_argument('--pools-directory',type=lambda v:int(v,0))
    args=parser.parse_args();out=args.out.resolve()
    locale=None
    if args.component=='locale':
        if any(v is None for v in (args.text_count,args.offsets_directory,args.pools_directory)): parser.error('locale requires --text-count, --offsets-directory and --pools-directory')
        locale=dict(languages=args.languages,text_count=args.text_count,offsets_directory=args.offsets_directory,pools_directory=args.pools_directory)
    names=[*FEATURES,'bridge'] if args.component=='all' else [args.component]
    results=[build(name,out/name if args.component=='all' else out,args.llvm,locale) for name in names]
    if args.component=='all':
        (out/'catalog.json').write_text(json.dumps(dict(schema=1,components=[dict(component=r['component'],component_id=r['component_id'],features5=r['features5'],metadata=r['component']+'/controls.json',elf_sha256=r['elf_sha256']) for r in results]),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(passed=True,components=[dict(component=r['component'],features5=r['features5'],entry=hex(r['entry']),elf_sha256=r['elf_sha256']) for r in results]),indent=2))

if __name__=='__main__': main()
