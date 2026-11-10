"""Explicit C3.11 stock-MAIN entry profiles for independent WLMs.

This extracts 16-byte entry preimages from one exact original application. It
never discovers functions, executes them, accesses a camera, or qualifies a
native function's signature, thread affinity or property semantics.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from z8_container import application_from_bytes

ROOT=Path(__file__).resolve().parents[1]
BASE=0x40160000
STOCK_BYTES=81807709
STOCK_SHA256='2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
SCHEMA='fenestra.z8-native-profile/v1'
WINDOW=16
ABI_U64_8=1
ABI_AAPCS64=5
NATIVE_CAPABILITY=1<<19
ESCAPE_FLAG=4
NAME=re.compile(r'[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z')
SYMBOL=re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_main(data):
    if len(data)!=STOCK_BYTES or digest(data)!=STOCK_SHA256:
        raise ValueError('exact original Z8 C3.11 MAIN length/SHA-256 required')


def read_main(path):
    with Path(path).open('rb') as stream:
        data=stream.read(STOCK_BYTES+1)
    check_main(data)
    application,receipt=application_from_bytes(data)
    if receipt['input_kind']!='application' or application!=data:
        raise ValueError('original MAIN application input required')
    return application


def _function(row):
    if not isinstance(row,dict) or set(row)-{'name','address','abi','version','flags'}:
        raise ValueError('explicit function name/address and supported metadata required')
    name=row.get('name')
    if not isinstance(name,str) or len(name)>63 or not NAME.fullmatch(name):
        raise ValueError('function name must be a bounded ASCII binding identity')
    address=row.get('address')
    if isinstance(address,str):
        try:address=int(address,0)
        except ValueError as error:raise ValueError('invalid function address') from error
    if isinstance(address,bool) or not isinstance(address,int) or address%4 or not BASE<=address<=BASE+STOCK_BYTES-WINDOW:
        raise ValueError('function entry must be aligned and wholly inside stock MAIN')
    abi=row.get('abi',ABI_U64_8)
    version=row.get('version',1)
    flags=row.get('flags',0)
    if isinstance(abi,bool) or not isinstance(abi,int) or abi not in (ABI_U64_8,ABI_AAPCS64):
        raise ValueError('supported explicit integer/AAPCS64 ABI required')
    if isinstance(version,bool) or not isinstance(version,int) or not 0<version<=0xffffffff:
        raise ValueError('nonzero uint32 binding version required')
    if isinstance(flags,bool) or not isinstance(flags,int) or flags&~ESCAPE_FLAG:
        raise ValueError('only optional native-address escape flag is supported')
    return dict(name=name,address=address,abi=abi,version=version,flags=flags)


def _canonical(profile):
    return json.dumps(profile,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode('ascii')


def build_profile(main,functions):
    check_main(main)
    rows=[_function(row) for row in functions]
    if not 1<=len(rows)<=64 or len({r['name'] for r in rows})!=len(rows):
        raise ValueError('1..64 explicit, uniquely named functions required')
    rows.sort(key=lambda r:r['name'])
    entries=[]
    for row in rows:
        at=row['address']-BASE
        preimage=main[at:at+WINDOW]
        if not any(preimage):raise ValueError('all-zero entry preimage is not a supported native function')
        entries.append(dict(name=row['name'],kind=1,abi=row['abi'],version=row['version'],flags=row['flags'],
            capabilities=NATIVE_CAPABILITY,bytes=0,address=hex(row['address']),original=preimage.hex()))
    result=dict(schema=SCHEMA,target='z8-c311',stock_main_bytes=STOCK_BYTES,stock_main_sha256=STOCK_SHA256,
        entry_preimage_bytes=WINDOW,native_begin=hex(BASE),native_end=hex(BASE+STOCK_BYTES),
        functions=entries,semantic_qualified=False,
        runtime_preimage_verification_required=True,
        limitations=['An entry preimage does not qualify its signature, thread, pointer lifetimes or behavior.',
                     'FX_BIND_DECLARE stores original bytes as metadata; the module must explicitly verify them before native use.'])
    result['profile_sha256']=digest(_canonical(result))
    return result


def verify_profile(main,profile):
    check_main(main)
    if not isinstance(profile,dict) or profile.get('schema')!=SCHEMA:
        raise ValueError('native-profile schema differs')
    unsigned=dict(profile);claimed=unsigned.pop('profile_sha256',None)
    if not isinstance(claimed,str) or claimed!=digest(_canonical(unsigned)):
        raise ValueError('native-profile document digest differs')
    entries=profile.get('functions')
    if not isinstance(entries,list):
        raise ValueError('explicit function records required')
    functions=[]
    for row in entries:
        if not isinstance(row,dict):raise ValueError('invalid function record')
        try:functions.append({key:row[key] for key in ('name','address','abi','version','flags')})
        except KeyError as error:raise ValueError('function record missing metadata') from error
    expected=build_profile(main,functions)
    if profile!=expected:
        raise ValueError('native profile differs from exact stock or its explicit declaration metadata')
    return dict(passed=True,profile_sha256=expected['profile_sha256'],stock_main_sha256=STOCK_SHA256,
                functions=len(entries),entry_preimage_bytes=WINDOW,semantic_qualified=False)


def render_c(profile,symbol='fx_z8_native_profile'):
    if not SYMBOL.fullmatch(symbol):raise ValueError('bounded C identifier required')
    guard=symbol.upper()+'_DATA_H'
    rows=['/* Generated entry preimages only; native behavior remains unqualified. */',
          '#ifndef '+guard,'#define '+guard,'#include "fenestra_native_profile.h"','']
    # FxBindingInfo is the existing copied declaration wire structure. No native
    # pointer, executable callback or core build identity is baked into a WLM.
    rows.append('static const FxBindingInfo '+symbol+'_bindings[]={')
    for entry in profile['functions']:
        original=','.join('0x'+entry['original'][i:i+2] for i in range(0,32,2))
        rows.append('    {.name='+json.dumps(entry['name'])+',.kind=FX_BIND_FUNCTION,.abi='+str(entry['abi'])+'u,'+
                    '.version='+str(entry['version'])+'u,.flags='+str(entry['flags'])+'u,'+
                    '.capabilities=FX_SDK_NATIVE,.bytes=0u,.address=UINT64_C('+entry['address']+'),.original={'+original+'}},')
    rows.extend(['};','',
        'static const uint8_t '+symbol+'_stock_main_sha256[32]={'+','.join('0x'+profile['stock_main_sha256'][i:i+2] for i in range(0,64,2))+'};',
        'static const uint8_t '+symbol+'_sha256[32]={'+','.join('0x'+profile['profile_sha256'][i:i+2] for i in range(0,64,2))+'};',
        '#define '+symbol.upper()+'_COUNT '+str(len(profile['functions']))+'u',
        '#define '+symbol.upper()+'_NATIVE_BEGIN UINT64_C('+profile['native_begin']+')',
        '#define '+symbol.upper()+'_NATIVE_END UINT64_C('+profile['native_end']+')',
        '#endif',''])
    return '\n'.join(rows)


def create(main_path,output,functions,symbol='fx_z8_native_profile'):
    main=read_main(main_path);profile=build_profile(main,functions)
    # Check the output text before publishing any artifact. Never replace an
    # input or an existing directory, including a symlink to an existing path.
    header=render_c(profile,symbol)
    out=Path(output).resolve()
    if not out.is_relative_to(ROOT/'build') or out.exists():
        raise ValueError('new owned build directory required')
    out.mkdir(parents=True)
    (out/'native-profile.json').write_text(json.dumps(profile,ensure_ascii=True,indent=2)+'\n',encoding='utf-8',newline='\n')
    (out/'native-profile.h').write_text(header,encoding='utf-8',newline='\n')
    return profile


def _explicit(value,abi):
    name,sep,address=value.partition('=')
    if not sep:raise argparse.ArgumentTypeError('use NAME=ADDRESS')
    try:return _function(dict(name=name,address=address,abi=abi))
    except ValueError as error:raise argparse.ArgumentTypeError(str(error)) from error


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    make=commands.add_parser('create')
    make.add_argument('--firmware',type=Path,required=True)
    make.add_argument('--output-dir',type=Path,required=True)
    make.add_argument('--symbol',default='fx_z8_native_profile')
    make.add_argument('--function',type=lambda v:_explicit(v,ABI_U64_8),action='append',default=[])
    make.add_argument('--aapcs64-function',type=lambda v:_explicit(v,ABI_AAPCS64),action='append',default=[])
    check=commands.add_parser('verify')
    check.add_argument('--firmware',type=Path,required=True)
    check.add_argument('--profile',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.command=='create':
        profile=create(args.firmware,args.output_dir,[*args.function,*args.aapcs64_function],args.symbol)
        print(json.dumps(dict(profile_sha256=profile['profile_sha256'],functions=len(profile['functions']),
                              semantic_qualified=False)))
    else:
        raw=json.loads(args.profile.read_text(encoding='utf-8'))
        print(json.dumps(verify_profile(read_main(args.firmware),raw)))

if __name__=='__main__':main()
