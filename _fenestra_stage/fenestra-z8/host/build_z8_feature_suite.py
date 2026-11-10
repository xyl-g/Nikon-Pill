"""Build a complete native feature suite and its isolated card plugins.

Offline only. Original inputs/current delivery are never overwritten. This is
an experimental core candidate; inserting WLMs cannot upgrade the old core.
"""
import argparse
import binascii
import hashlib
import json
from pathlib import Path
import subprocess
import struct
import sys

sys.dont_write_bytecode=True
from z8_container import ROOT, read_input, _load_external_reader, verify_application
from z8_feature_suite import checked_upstream, compose, FEATURES, VENDOR
from cipher_patch import make_patch, apply_patch


def sha(data):
    return hashlib.sha256(data).hexdigest()


def merge_layers(stock, core, native, core_windows, native_windows, refinements=None):
    if len(stock)!=len(core) or len(stock)!=len(native):
        raise ValueError('layer lengths differ')
    refinements=refinements or {}
    for a,b in core_windows:
        if not 0<=a<b<=len(stock):
            raise ValueError('invalid core window')
        for c,d in native_windows:
            if a<d and c<b and ((a,b)!=(c,d) or refinements.get((a,b))!=native[a:b]):
                raise ValueError('unregistered core/upstream window overlap')
    for a,b in native_windows:
        if not 0<=a<b<=len(stock):
            raise ValueError('invalid upstream window')
    # Each input layer must account for its entire mutation independently.
    for image,windows in ((core,core_windows),(native,native_windows)):
        restored=bytearray(image)
        for a,b in windows:
            restored[a:b]=stock[a:b]
        if restored!=stock:
            raise ValueError('unlisted layer mutation')
    result=bytearray(native)
    for a,b in core_windows:
        result[a:b]=core[a:b]
    return bytes(result)


def build(firmware, external_project, output, *, llvm_bin=None, modules=True):
    upstream=checked_upstream()
    out=Path(output).resolve()
    if not out.is_relative_to(ROOT/'build') or out.exists():
        raise ValueError('new owned build directory required')
    raw,kind=read_input(firmware)
    if kind!='package':
        raise ValueError('full exact original encrypted C3.11 package required')
    reader,container_origin=_load_external_reader(external_project)
    original=reader(raw);stock=verify_application(bytes(original.eg[:-2]))
    native,detail,rows=compose(stock)
    # These are additional native entry/context prerequisites of WLM controls.
    from z8_framing_profile import verify_original
    from z8_photo_tools_profile import validate_metadata
    framing_abi=verify_original(stock)
    photo_abi=validate_metadata(json.loads((VENDOR/'resources/compiled.json').read_text(encoding='utf-8')))
    out.mkdir(parents=True)
    (out/'stock.main').write_bytes(stock)
    (out/'upstream.main').write_bytes(native)
    from build_z8_core import build as build_core
    core_report=build_core(out/'stock.main',out/'core',llvm_bin=llvm_bin,feature_suite=True)
    return finish(firmware,external_project,out,llvm_bin=llvm_bin,modules=modules)


def finish(firmware, external_project, output, *, llvm_bin=None, modules=True):
    """Resume only after exact core/source and original-input validation."""
    from verify_z8_core import verify as verify_core
    out=Path(output).resolve(strict=True)
    if not out.is_relative_to(ROOT/'build'):
        raise ValueError('owned build required')
    upstream=checked_upstream()
    raw,kind=read_input(firmware)
    if kind!='package':
        raise ValueError('full exact original package required')
    reader,container_origin=_load_external_reader(external_project)
    original=reader(raw);stock=verify_application(bytes(original.eg[:-2]))
    if (out/'stock.main').read_bytes()!=stock:
        raise ValueError('retained stock differs')
    native,detail,rows=compose(stock)
    if (out/'upstream.main').read_bytes()!=native:
        raise ValueError('retained upstream composition differs')
    from z8_framing_profile import verify_original
    from z8_photo_tools_profile import validate_metadata
    framing_abi=verify_original(stock)
    photo_abi=validate_metadata(json.loads((VENDOR/'resources/compiled.json').read_text(encoding='utf-8')))
    core_report=json.loads((out/'core/build.json').read_text(encoding='utf-8'))
    verify_core(out/'stock.main',out/'core')
    core=(out/'core/z8-core.main').read_bytes()
    windows=[(0x40000,0x40000+core_report['code_bytes'])]
    windows += [(int(c['address'],16)-0x40160000,int(c['address'],16)-0x40160000+4) for c in core_report['changes']]
    native_windows=[(int(e['offset'],16),int(e['offset'],16)+e['bytes']) for e in rows]
    # Chain the MOV observer through upstream's open-gate writer close hook.
    # Exactly this one original entry is refined; the upstream body stays intact.
    close=0x40ceef10;target=0x40960870
    native_close=struct.pack('<I',0x14000000|(((target-close)//4)&0x3ffffff))
    refinements={(close-0x40160000,close-0x40160000+4):native_close}
    suite=merge_layers(stock,core,native,windows,native_windows,refinements)
    (out/'z8-suite.main').write_bytes(suite)
    component=suite+binascii.crc_hqx(suite,0).to_bytes(2,'big')
    packet,decoded=original.replace_eg(component,original.modules[2]['name'])
    if bytes(decoded.eg)!=component:
        raise ValueError('container MAIN readback mismatch')
    (out/'Z_8_0311.bin').write_bytes(packet)
    manifest=dict(schema='fenestra.z8-feature-suite/v1',target='z8-c311',features=list(FEATURES),
        upstream_source_commit=upstream['source_commit'],upstream_release=upstream['release_tag'],
        source_licenses={'fenestra':'CC-BY-NC-SA-4.0 plus EXCEPTIONS.md','native-feature-backend':'GPL-3.0-only'},
        input_sha256=sha(raw),candidate_sha256=sha(suite),package_sha256=sha(packet),
        debug_build_identity=core_report['debug_build_identity'],upstream_patches=rows,
        core_code_bytes=core_report['code_bytes'],core_hook_sites=len(core_report['changes']),
        framing_abi=framing_abi,photo_abi=photo_abi,upstream_details=detail,container_origin=container_origin,
        requires_feature_suite_core=True,old_core_plugin_only_supported=False,
        physical_qualified=False,installed=False,automatic_firmware_update_performed=False,
        limitations=['New native feature-suite core required before these card controls are usable.',
                     'Open gate and RAW pre-capture use their complete native camera menu paths.',
                     'MOV lifetime guards do not prove a lock for every pre-file capture setup path.',
                     'No original-MOV analysis or full-camera qualification is implied.'])
    (out/'suite.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    from verify_z8_feature_suite import verify
    validation=verify(firmware,out,external_project)
    (out/'verification.json').write_text(json.dumps(validation,indent=2)+'\n')
    patch=out/'Fenestra-Z8-C311-feature-suite1.fxpatch'
    patch_manifest=make_patch(raw,packet,'z8-c311',patch)
    applied=out/'patch-roundtrip.bin'
    apply_patch(firmware,patch,applied)
    if sha(applied.read_bytes())!=sha(packet):
        raise ValueError('cipher patch round trip differs')
    module_rows=[]
    if modules:
        sources=[('prores','examples/prores/module.c'),('h265','examples/h265/module.c'),
                 ('log_colour','examples/log_colour/module.c'),('framing','examples/framing/module.c'),
                 ('photo_tools','modules/photo_tools/module.c')]
        card=out/'card/FENESTRA';card.mkdir(parents=True)
        for ident,source in sources:
            destination=out/'modules'/ident
            command=[sys.executable,str(ROOT/'host/build_sdk_module.py'),str(ROOT/source),'--id',ident,
                     '--target','z8-c311','--output-dir',str(destination)]
            if llvm_bin:
                command.extend(['--llvm-bin',str(llvm_bin)])
            subprocess.run(command,cwd=ROOT,check=True,timeout=60,capture_output=True,text=True)
            data=(destination/(ident+'.wlm')).read_bytes()
            (card/(ident+'.wlm')).write_bytes(data)
            module_rows.append(dict(id=ident,file=ident+'.wlm',bytes=len(data),sha256=sha(data)))
        (out/'plugins.json').write_text(json.dumps(dict(schema='fenestra.z8-feature-suite-plugins/v1',
            requires_core=core_report['debug_build_identity'],modules=module_rows,physical_qualified=False),indent=2)+'\n')
    if sha(Path(firmware).read_bytes())!=sha(raw):
        raise ValueError('original input changed during build')
    return dict(target='z8-c311',candidate_sha256=sha(suite),package_sha256=sha(packet),
                native_windows=len(rows),plugins=module_rows,patch=patch_manifest,verified=True,
                physical_qualified=False,installed=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('firmware','external-project','output-dir'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--llvm-bin',type=Path)
    p.add_argument('--no-modules',action='store_true')
    p.add_argument('--resume-after-core',action='store_true',help='resume an existing exact core after full input/source verification')
    a=p.parse_args();operation=finish if a.resume_after_core else build
    result=operation(a.firmware,a.external_project,a.output_dir,llvm_bin=a.llvm_bin,modules=not a.no_modules)
    (a.output_dir/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('target','candidate_sha256','package_sha256','native_windows','plugins','verified','physical_qualified','installed')},indent=2))

if __name__=='__main__':
    main()
