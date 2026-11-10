"""Execute original Z9/Z8 serialized file wrappers at explicit backend boundaries.

The two applications are whole-SHA pinned. Original wrapper instructions run;
mutex and original filesystem backend calls are named, synthetic boundaries.
This proves the tested software argument/return/output behavior, not storage,
DMA, cache coherence, directory encoding, atomicity or hardware reliability.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from capstone.arm64 import ARM64_OP_IMM
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED, __version__ as unicorn_version
from unicorn.arm64_const import UC_ARM64_REG_TPIDR_EL1, UC_ARM64_REG_PSTATE, UC_ARM64_REG_CPACR_EL1
from cipher_patch import atomic_new
from platform_profiles import PROFILES
from verify_z8_native_abi import OriginalLab, BASE, STACK, SCRATCH, CONTEXT, STOP

ROOT=Path(__file__).resolve().parents[1]
Z8_SHA='2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
PINS={'z9':PROFILES['z9-c532']['sha256'],'z8':Z8_SHA}
API_NAMES=('open','read','write','seek64','close','directory.open','directory.next','directory.close','rename')
OLD_API={name:PROFILES['z9-c532']['functions']['camera.card.'+name] for name in API_NAMES}


def need(condition,message):
    if not condition:raise AssertionError(message)


def admit(data,model):
    if hashlib.sha256(data).hexdigest()!=PINS[model]:raise ValueError(model+' original application SHA-256 differs')


class FileLab(OriginalLab):
    """Reuse OriginalLab instruction/return/boundary machinery for either pinned image.

    Only ROM and synthetic caller RAM are admitted here. A wrapper unexpectedly
    reading native filesystem state outside the declared boundaries is an error.
    """
    def __init__(self,image,model):
        self.application=image;self.model=model
        self.u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);self.pages=set();self.boundaries={}
        self.boundary_calls=[];self.instructions=0;self.executed=set()
        for address,length in ((STACK,0x10000),(SCRATCH,0x20000),(CONTEXT,0x1000),(STOP,0x1000)):
            self.u.mem_map(address,length);self.pages.update(range(address,address+length,4096))
        self.u.reg_write(UC_ARM64_REG_TPIDR_EL1,CONTEXT)
        self.u.reg_write(UC_ARM64_REG_PSTATE,5)
        self.u.reg_write(UC_ARM64_REG_CPACR_EL1,3<<20)
        self.u.hook_add(UC_HOOK_MEM_UNMAPPED,self._unmapped)
        self.u.hook_add(UC_HOOK_CODE,self._instruction)

    def _page(self,address):
        page=address&~4095
        if page in self.pages:return
        if not BASE<=page<BASE+len(self.application):
            raise AssertionError('wrapper accessed native filesystem state beyond declared backend boundary: '+hex(address))
        self.u.mem_map(page,4096)
        self.u.mem_write(page,self.application[page-BASE:page-BASE+4096]);self.pages.add(page)


def discover_wrapper(image,address,acquire,release):
    decoder=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN);decoder.detail=True
    instructions=[];calls=[]
    for instruction in decoder.disasm(image[address-BASE:address-BASE+512],address):
        instructions.append(instruction)
        if instruction.mnemonic=='bl' and instruction.operands[0].type==ARM64_OP_IMM:
            calls.append((instruction.address,instruction.operands[0].imm))
        if instruction.mnemonic=='ret':break
    need(instructions and instructions[-1].mnemonic=='ret','no bounded wrapper return')
    targets={target for _,target in calls}-{acquire,release}
    need(len(targets)==1,'file wrapper has unreviewed additional native boundary')
    need(any(target==acquire for _,target in calls) and any(target==release for _,target in calls),'serialized mutex calls absent')
    backend=next(iter(targets))
    return dict(address=address,backend=backend,acquire=acquire,release=release,
                original_bytes=image[address-BASE:instructions[-1].address-BASE+4].hex(),
                backend_call_sites=[site for site,target in calls if target==backend])


def platform_wrappers(images,mappings):
    result={}
    for model,image in images.items():
        translated=lambda address:address if model=='z9' else int(mappings[hex(address)]['address'],16)
        acquire,release=translated(0x40d30004),translated(0x40d2ffb0)
        result[model]={name:discover_wrapper(image,translated(address),acquire,release) for name,address in OLD_API.items()}
    for name in API_NAMES:
        left,right=result['z9'][name],result['z8'][name]
        entry=mappings.get(hex(left['backend']))
        if entry is not None:need(int(entry['address'],16)==right['backend'],'backend call target correspondence differs: '+name)
    return result


def make_lab(image,model,wrapper):
    lab=FileLab(image,model);locks=[]
    lab.intercept(wrapper['acquire'],'native.filesystem.mutex.acquire',lambda a:locks.append(('acquire',a[0])) or 0)
    lab.intercept(wrapper['release'],'native.filesystem.mutex.release',lambda a:locks.append(('release',a[0])) or 0)
    return lab,locks


def finish_case(lab,locks,case,wrapper,result,details):
    expected_mutex=0xa02ea1428 if lab.model=='z9' else 0xa02e81dd8
    need(locks==[('acquire',expected_mutex),('release',expected_mutex)],'file wrapper lock balance/identity differs: '+case)
    return dict(case=case,model=lab.model,entry=hex(wrapper['address']),backend=hex(wrapper['backend']),
                return_u32=result&0xffffffff,mutex=hex(expected_mutex),mutex_acquire_release_balanced=True,
                original_instructions_executed=lab.instructions,
                backend_boundary_calls=[r for r in lab.boundary_calls if r['name']=='native.filesystem.backend'],
                details=details)


def scalar_case(image,model,wrapper,name,status):
    lab,locks=make_lab(image,model,wrapper);path=SCRATCH+0x100;other=SCRATCH+0x200;output=SCRATCH+0x300
    lab.write(path,b'A:\\FENESTRA\\sample.wlm\0');lab.write(other,b'A:\\FENESTRA\\other.wlm\0')
    lab.write(output,b'\xa5'*0x230)
    if name=='open':arguments=(path,0x100000123,0x100000020);expected=[path,0x123,0x20]
    elif name=='rename':arguments=(path,other);expected=[path,other]
    elif name=='directory.open':arguments=(path,output);expected=[path,output]
    elif name=='directory.next':arguments=(31,output);expected=[31,output]
    else:arguments=(31,);expected=[31]
    observed=[];payload=bytes(range(256))*2+bytes(range(48))
    def backend(args):
        observed.append(args[:len(expected)])
        need(observed[-1]==expected,'file scalar argument forwarding differs: '+name)
        if name=='directory.open' and status==0:lab.w32(output,31)
        if name=='directory.next' and status==0:lab.write(output,payload)
        return status
    lab.intercept(wrapper['backend'],'native.filesystem.backend',backend)
    result=lab.call(wrapper['address'],*arguments)
    need(len(observed)==1 and result&0xffffffff==status&0xffffffff,'file scalar status forwarding differs: '+name)
    if name=='directory.open':need(lab.u32(output)==(31 if status==0 else 0xa5a5a5a5),'directory handle output differs')
    if name=='directory.next':
        need(lab.read(output,0x230)==(payload if status==0 else b'\xa5'*0x230),'directory entry bytes changed unexpectedly')
    return finish_case(lab,locks,name+'.'+hex(status&0xffffffff),wrapper,result,dict(arguments=expected,backend_status=status&0xffffffff))


def transfer_case(image,model,wrapper,name,label,plan,expected_status,expected_done):
    lab,locks=make_lab(image,model,wrapper);buffer=SCRATCH+0x1000;actual=SCRATCH+0x3000;requested=7
    lab.write(buffer,bytes(range(32)));lab.w32(actual,0xaaaaaaaa)
    calls=[];done=0
    def backend(args):
        nonlocal done
        index=len(calls);need(index<len(plan),'unexpected extra native transfer/retry')
        amount,status=plan[index]
        need(args[0]==31 and args[1]==buffer+done and args[2]==requested-done,'native transfer buffer/remaining count differs')
        if name=='read':need(args[3]==actual,'read actual pointer ABI differs')
        else:need(STACK<=args[3]<STACK+0x10000 and args[3]!=actual,'write backend count must use native local accumulator')
        calls.append(dict(fd=args[0],buffer_delta=args[1]-buffer,request=args[2],actual_pointer_is_caller=args[3]==actual,actual=amount,status=status))
        lab.w32(args[3],amount)
        if name=='read' and amount:lab.write(args[1],bytes(range(done,done+amount)))
        done+=amount
        return status
    lab.intercept(wrapper['backend'],'native.filesystem.backend',backend)
    result=lab.call(wrapper['address'],31,buffer,requested,actual)
    need(len(calls)==len(plan),'planned native transfer sequence was not consumed')
    need(result&0xffffffff==expected_status and lab.u32(actual)==expected_done,'transfer final status/count differs: '+name+'.'+label)
    if name=='read':need(lab.read(buffer,expected_done)==bytes(range(expected_done)),'read backend payload was not preserved')
    return finish_case(lab,locks,name+'.'+label,wrapper,result,dict(calls=calls,final_actual=lab.u32(actual),requested=requested))


def seek_case(image,model,wrapper,origin,backend_result):
    lab,locks=make_lab(image,model,wrapper);output=SCRATCH+0x300;offset=0x123456789abcdef0
    position=0xfedcba9876543210;lab.w64(output,0x1111111111111111);observed=[]
    def backend(args):
        need(args[:4]==[31,offset,output,origin],'seek64 backend order/width differs')
        observed.append(args[:4])
        # The real seek backend returns BOOL: 1 writes position, 0 fails.
        # Other values below exercise wrapper normalization only; they are
        # synthetic boundaries, not valid native success/error conventions.
        if backend_result==1:lab.w64(output,position)
        return backend_result
    lab.intercept(wrapper['backend'],'native.filesystem.backend',backend)
    result=lab.call(wrapper['address'],31,offset,origin,output)
    need(len(observed)==1 and result==int(backend_result!=0),'seek64 return must normalize nonzero backend result to 1')
    need(lab.u64(output)==(position if backend_result==1 else 0x1111111111111111),'seek64 output width/BOOL handling differs')
    return finish_case(lab,locks,'seek64.'+str(origin)+'.'+hex(backend_result),wrapper,result,
                       dict(offset=hex(offset),origin=origin,backend_result=backend_result,
                            fixture_kind='native-bool' if backend_result in (0,1) else 'noncanonical-normalization-only',
                            output_position=hex(lab.u64(output)),
                            backend_argument_order=['fd','offset64','position64_pointer','origin']))


def verify(z8,z9,mappings):
    images={'z9':z9,'z8':z8}
    for model,image in images.items():admit(image,model)
    wrappers=platform_wrappers(images,mappings)
    checks=[]
    for model,image in images.items():
        for name in ('open','close','directory.open','directory.next','directory.close','rename'):
            for status in ((31,0x80000901,0xffffffff) if name=='open' else (0,0x80000030,0x80005000)):
                checks.append(scalar_case(image,model,wrappers[model][name],name,status))
        for name in ('read','write'):
            plans=[('full',[(7,0)],0,7),('partial_then_full',[(3,0),(4,0)],0,7),
                   ('zero_progress',[(0,0)],0,0),('immediate_error',[(0,0x80000002)],0x80000002,0),
                   ('partial_then_error',[(3,0),(1,0x80000002)],0x80000002,4)]
            if name=='write':
                plans += [('backpressure_then_success',[(3,0x80005010),(4,0)],0,7),
                          ('repeated_backpressure',[(2,0x80005010),(0,0x80005010)],0x80005010,2),
                          ('full_with_backpressure',[(7,0x80005010)],0,7)]
            for label,plan,status,done in plans:checks.append(transfer_case(image,model,wrappers[model][name],name,label,plan,status,done))
        for origin in (0,1,2):
            for backend_result in (0,1,0x80000002):checks.append(seek_case(image,model,wrappers[model]['seek64'],origin,backend_result))
    comparable={}
    for check in checks:
        normalized={key:value for key,value in check.items() if key in ('case','return_u32','details')}
        previous=comparable.setdefault(check['case'],normalized)
        need(previous==normalized,'Z9/Z8 wrapper observable semantics differ: '+check['case'])
    return dict(schema='fenestra.z8-file-native-boundaries/v1',software_validation='completed',engine='Unicorn '+unicorn_version,
                target='z8-c311',runtime_ready=False,abi_validated=False,inputs=PINS,
                interfaces=[dict(name=name,z9=hex(OLD_API[name]),z8=hex(wrappers['z8'][name]['address']),
                                 z9_backend=hex(wrappers['z9'][name]['backend']),z8_backend=hex(wrappers['z8'][name]['backend'])) for name in API_NAMES],
                models_checked=2,cases_per_model=len(comparable),executed_cases=len(checks),checks=checks,
                findings=['Serialized file wrappers share one native filesystem mutex per image and balance acquire/release in every tested path.',
                          'Open forwards path and 32-bit flags/mode; raw descriptor/error return bits are preserved.',
                          'Read/write aggregate actual byte counts and advance the buffer for tested partial transfers.',
                          'Write treats backend 0x80005010 specially; complete progress normalizes that status to success, and repeated backpressure terminates as tested.',
                          'Seek64 preserves full 64-bit offset/output and reorders the backend arguments; native BOOL fixtures use 1 for success and 0 for failure. A separate noncanonical fixture checks nonzero normalization only.',
                          'Directory handle/entry output and directory end/error codes pass through the wrapper unchanged in tested scenarios.'],
                limitations=['Mutex operations and original filesystem backend functions are intercepted boundaries with explicit return/output fixtures.',
                             'Synthetic seek boundaries do not execute the real backend or establish hardware behavior; noncanonical return values are only wrapper-normalization probes.',
                             'No card/controller/sector I/O, file task, directory-name encoding, cache/DMA coherence or persistence was exercised.',
                             'The backends are original file-library entry boundaries, not an emulation of their implementations or a real device driver.',
                             'This is bounded software ABI evidence for listed cases only; complete ABI and hardware qualification remain separate.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--z8',type=Path,default=ROOT/'inputs/z8-c311-main.bin')
    parser.add_argument('--z9',type=Path,default=ROOT/'inputs/z9-c532-main.bin')
    parser.add_argument('--correspondence',type=Path,default=ROOT/'build/z8-full-port/correspondence/address-map.json')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    mappings=json.loads(args.correspondence.read_text())['mappings']
    result=verify(args.z8.read_bytes(),args.z9.read_bytes(),mappings)
    encoded=json.dumps(result,indent=2)+'\n'
    if args.output:atomic_new(args.output,encoded.encode())
    print(encoded,end='')

if __name__=='__main__':main()
