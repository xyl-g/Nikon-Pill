"""Independent finite verification of the complete Z8 feature-suite composition.

The expected upstream windows/hashes come from its pinned reviewed recipe,
not the composer report. Both layers are removed to reconstruct all stock bytes.
"""
import argparse
import binascii
import hashlib
import json
from pathlib import Path
import struct
from z8_container import ROOT, read_input, _load_external_reader, verify_application
from verify_z8_core import verify as verify_core
from z8_feature_suite import VENDOR, checked_upstream, FEATURES, COMBINATIONS_SHA256


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify_layers(stock, core, suite, report):
    require(len(stock)==len(core)==len(suite), 'layer image length differs')
    require(report.get('feature_suite_backend',{}).get('abi')==1, 'feature-suite base required')
    raw=(VENDOR/'resources/combinations.json').read_bytes()
    require(sha(raw)==COMBINATIONS_SHA256, 'reviewed combination recipe differs')
    recipes=json.loads(raw)
    expected=[recipes['patches'][i] for i in recipes['profiles']['+'.join(FEATURES)]['patches']]
    core_windows=[(0x40000,0x40000+report['code_bytes'])]
    core_windows += [(int(c['address'],16)-0x40160000,int(c['address'],16)-0x40160000+4) for c in report['changes']]
    native=bytearray(suite)
    close_at=0x40ceef10-0x40160000
    close_branch=struct.pack('<I',0x14000000|(((0x40960870-0x40ceef10)//4)&0x3ffffff))
    for begin,end in core_windows:
        require(suite[begin:end]==core[begin:end], 'combined core bytes differ')
        native[begin:end]=close_branch if (begin,end)==(close_at,close_at+4) else stock[begin:end]
    restored=bytearray(native)
    end=0
    for edit in expected:
        at=int(edit['offset'],16);stop=at+edit['bytes']
        require(0<=end<=at<stop<=len(stock), 'reviewed windows overlap or exceed image')
        overlap=[(a,b) for a,b in core_windows if at<b and a<stop]
        require(not overlap or (overlap==[(close_at,close_at+4)] and at==close_at and stop==close_at+4),
                'unregistered upstream/core overlap')
        require(sha(stock[at:stop])==edit['old_sha256'],'stock recipe preimage differs')
        require(sha(native[at:stop])==edit['new_sha256'],'upstream patch bytes differ: '+edit['purpose'])
        restored[at:stop]=stock[at:stop]
        end=stop
    require(restored==stock,'unlisted changes outside the two verified layers')
    # The composite language directory includes signed offsets to native text.
    # Check every actual extension ID, all languages, and the stock endpoint.
    language_checks=0
    for language in range(36):
        table=struct.unpack_from('<Q',native,0x311c5a0+8*language)[0]-0x40160000
        pool=struct.unpack_from('<Q',native,0x387af58+8*language)[0]-0x40160000
        offsets=struct.unpack_from('<3226i',native,table)
        for ident in range(3137,3225):
            a,b=pool+offsets[ident],pool+offsets[ident+1]
            require(0<=a<=b<=len(native) and (b-a)%2==0 and b-a<=4096,'extended text range differs')
            bytes(native[a:b]).decode('utf-16le')
            language_checks+=1
    return dict(passed=True, original_reconstructed=True, native_windows=len(expected),
                core_windows=len(core_windows), extended_language_strings=language_checks,
                native_sha256=sha(native), suite_sha256=sha(suite),
                upstream_recipe_sha256=COMBINATIONS_SHA256)


def verify(firmware, build, external_project):
    checked_upstream()
    build=Path(build).resolve(strict=True)
    require(build.is_relative_to(ROOT/'build'), 'owned suite build required')
    raw,kind=read_input(firmware)
    require(kind=='package','original encrypted C3.11 package required')
    reader,_=_load_external_reader(external_project)
    original=reader(raw);stock=verify_application(bytes(original.eg[:-2]))
    core_dir=build/'core'
    base_validation=verify_core(build/'stock.main',core_dir)
    core=(core_dir/'z8-core.main').read_bytes()
    suite=(build/'z8-suite.main').read_bytes()
    report=json.loads((core_dir/'build.json').read_text(encoding='utf-8'))
    layers=verify_layers(stock,core,suite,report)
    packet=(build/'Z_8_0311.bin').read_bytes();decoded=reader(packet)
    require(bytes(decoded.eg[:-2])==suite and binascii.crc_hqx(decoded.eg,0)==0,'container MAIN readback differs')
    require(len(decoded.modules)==len(original.modules)==5,'container module count changed')
    for index,(a,b) in enumerate(zip(original.modules,decoded.modules)):
        require((a['name'],a['start'],a['bytes'])==(b['name'],b['start'],b['bytes']),'module directory changed')
        if index!=2:
            start,end=a['start'],a['start']+a['bytes']
            require(a==b and original.body[start:end]==decoded.body[start:end],'non-MAIN component changed')
    start=original.modules[2]['start'];end=start+original.modules[2]['bytes']
    require(original.body[:start]==decoded.body[:start] and original.body[end:-16]==decoded.body[end:-16]
            and original.body[-14:]==decoded.body[-14:],'container plaintext outside MAIN changed')
    manifest=json.loads((build/'suite.json').read_text(encoding='utf-8'))
    require(manifest['features']==list(FEATURES) and manifest['candidate_sha256']==sha(suite)
            and manifest['package_sha256']==sha(packet),'suite manifest identity differs')
    return dict(schema='fenestra.z8-feature-suite-verification/v1',passed=True,target='z8-c311',
                features=list(FEATURES),layers=layers,base_core=base_validation,
                package_sha256=sha(packet),candidate_sha256=sha(suite),non_main_components_unchanged=True,
                physical_qualified=False,installed=False,
                limitation='Finite byte/ABI verification; no physical capture or card qualification.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('firmware','build','external-project','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();result=verify(a.firmware,a.build,a.external_project)
    require(not a.output.exists(),'new validation output required')
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('passed','target','candidate_sha256','package_sha256')}))

if __name__=='__main__':
    main()
