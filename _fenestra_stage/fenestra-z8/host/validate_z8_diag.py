"""Cross-check a Z8 diagnostic candidate against original bytes and saved offline execution evidence.

A passing report admits an application-image build for further testing. It does
not qualify USB hardware, physical shutdown, warm restart or a camera update.
"""
import argparse,hashlib,json,struct
from pathlib import Path
from capstone import Cs,CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN
from build_z8_diag import load_bindings,copyable,MAIN_SHA256,MAIN_BYTES
from cipher_patch import atomic_new

def sha(data):return hashlib.sha256(data).hexdigest()
def demand(value,message):
    if not value:raise ValueError(message)
def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def number(value):return int(value,0)if isinstance(value,str)else value

def validate(firmware,bindings,abi_report,core_build,core_emulation,qemu_result):
    original=Path(firmware).read_bytes();p,raw=load_bindings(bindings);base=p['application']['base']
    demand(len(original)==MAIN_BYTES and sha(original)==MAIN_SHA256,'wrong original application')
    expected_native=dict(allocate=0x40d2c408,task_create=0x40185064,task_resume=0x401855e0,task_delay=0x40187b00,
        ptp_payload=0x407defe4,ptp_capacity=0x407df020,ptp_incoming=0x407df0c8,ptp_response=0x407df0d4,ptp_send=0x407df3c4)
    demand(p['native']==expected_native,'native profile is outside reviewed Z8 scope')
    demand(p['data']==dict(page_roots=0xa032b50c8,ptp_session=0xa047ce474,ptp_operations=0xa047cecd8,ptp_operations_capacity=512),'native data bindings differ')
    demand(p['code']==dict(begin=0x401a0000,end=0x40200000),'unreviewed code reservation')
    demand(p['task']==dict(attributes=0,priority=28,processor=1,affinity=0xffffffff,stack_bytes=65536,delay_ticks=10)and p['allocate_pool']==0,'unreviewed task/allocation policy')
    def at(address,size):return original[address-base:address-base+size]
    def constant(address,register,size=32):
        value=None
        for(w,)in struct.iter_unpack('<I',at(address,size)):
            if w&31!=register:continue
            op=w&0xff800000;shift=((w>>21)&3)*16;imm=(w>>5)&65535
            if op==0xd2800000:value=imm<<shift
            elif op==0xf2800000 and value is not None:value=(value&~(65535<<shift))|(imm<<shift)
        return value
    demand(constant(0x403db69c,8,16)==p['data']['page_roots'],'page getter root constant')
    demand(constant(0x407dd4a0,8,16)==p['data']['ptp_session'],'session global constant')
    demand(constant(0x407e8ed0,2,24)==p['data']['ptp_operations'],'DeviceInfo operations producer constant')
    demand(constant(0x407e8bc8,1,16)==p['data']['ptp_operations']and struct.unpack('<I',at(0x407e8bd8,4))[0]==0x321703e2,'operations capacity producer')
    a=p['anchor'];demand((a['group'],a['id'],a['descriptor'],a['page_size'],a['descriptor_offset'])==(2,0x297,0xa028ca040,96,80),'unreviewed menu anchor')
    page_file=0x4bffcc0+96*a['id'];descriptor_file=0x4b13000+a['descriptor']-0xa02808000
    demand(struct.unpack_from('<I',original,page_file)[0]==0x02970002,'page identity')
    demand(struct.unpack_from('<Q',original,page_file+80)[0]==a['descriptor'],'stock descriptor pointer')
    demand(original[descriptor_file:descriptor_file+64].hex()==a['descriptor_bytes'],'stock descriptor evidence')
    events=p['events'];demand(events==dict(message_id_offset=32,startup_complete=19,shutdown=7),'unreviewed event ABI')
    for ident,name in((19,b'eSyscCommandId_AppFirstStartComplete'),(7,b'eSyscCommandId_BSShutdown')):
        n,pointer=struct.unpack_from('<I4xQ',original,0x43f907f8-base+ident*88)
        demand(n==ident and at(pointer,len(name)+1)==name+b'\0','system event identity')
    cs=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN);cs.detail=True
    hook_evidence=[]
    for name,address,size in(('startup',0x40d60ec8,268),('ptp_lookup',0x407dea74,172),('ptp_session',0x407dd4a0,56),('ptp_operations',0x40356a74,80)):
        h=p['hooks'][name];demand(h['address']==address and h['continuation']==address+16 and at(address,16).hex()==h['original'],'hook preimage/continuation')
        copyable(at(address,16));ins=list(cs.disasm(at(address,size),address));demand(sum(i.size for i in ins)==size,'incomplete hook body disassembly')
        # All four reviewed bodies do not consume x16. The trampoline may use IP0.
        demand(not any('x16'in [i.reg_name(r)for r in i.regs_access()[0]]for i in ins),'x16 input is live')
        hook_evidence.append(dict(name=name,address=hex(address),bytes=size,body_sha256=sha(at(address,size)),x16_read=False))
    demand(not any(original[p['code']['begin']-base:p['code']['end']-base]),'occupied RX reservation')
    abi=read_json(abi_report);demand(abi.get('application_sha256')==MAIN_SHA256 and abi.get('software_validation')=='completed','native instruction evidence')
    required={'ptp.lookup.stock_tables_and_cache','ptp.payload_capacity_incoming','ptp.session_change_notification','ptp.operations_u16_array_copy','task.wrapper_create_and_start_boundary','kernel.task_argument_and_context_guards','ptp.response_and_data_packet_construction'}
    demand({r['name']for r in abi['checks']}==required and all(r['original_instructions_executed']>0 for r in abi['checks']),'missing native execution paths')
    build=read_json(core_build);emulation=read_json(core_emulation);q=read_json(qemu_result);observed=q.get('fenestra_z8_diagnostic',{})
    demand(build['base_sha256']==MAIN_SHA256 and build['bindings_sha256']==sha(raw),'compiled binding identity')
    binary=Path(core_build).parent/'core.bin';code=binary.read_bytes();demand(sha(code)==build['code_sha256'],'compiled core identity')
    demand(emulation.get('passed')is True and emulation.get('tests_run',0)>=12 and emulation.get('code_sha256')==build['code_sha256'],'core execution tests incomplete')
    demand(q.get('error')is None and q.get('owned_qemu_stopped')is True and observed.get('native_worker_observed')is True,'native QEMU worker not observed')
    demand(observed.get('core_bytes_equal')is True and observed.get('descriptor_clone_equal')is True and observed.get('error')==0,'QEMU code/descriptor/state differs')
    demand(observed.get('magic')==0x465a3844 and observed.get('phase')==3 and observed.get('ptp_enabled')==1 and observed.get('ready')==1 and observed.get('worker_running')==1 and observed.get('shutdown')==0 and 0<observed.get('task_id',0)<0x10000,'QEMU endpoint not ACTIVE')
    changes=[];candidate=bytearray(original);begin=number(build['code_begin'])-base;candidate[begin:begin+len(code)]=code
    symbols={'startup':'fx_z8_system_dispatch','ptp_lookup':'fx_z8_ptp_lookup','ptp_session':'fx_z8_ptp_session','ptp_operations':'fx_z8_ptp_operations'}
    for name,h in p['hooks'].items():
        delta=build['symbols'][symbols[name]]-h['address'];demand(delta%4==0 and -(1<<27)<=delta<(1<<27),'branch range')
        offset=h['address']-base;struct.pack_into('<I',candidate,offset,0x14000000|((delta//4)&0x3ffffff));changes.append((offset,at(h['address'],4)))
    demand(sha(candidate)==q.get('firmware_sha256'),'QEMU did not execute this exact reconstructed candidate')
    candidate[begin:begin+len(code)]=bytes(len(code))
    for offset,window in changes:candidate[offset:offset+4]=window
    demand(bytes(candidate)==original,'original reconstruction differs')
    checks={k:True for k in('task_abi','ptp_abi','events','anchor','native_bindings','hook_windows','executable_space','scratch_x16')}
    evidence={name:dict(path=str(path),sha256=sha(Path(path).read_bytes()))for name,path in dict(abi=abi_report,build=core_build,emulation=core_emulation,qemu=qemu_result).items()}
    return dict(schema='fenestra.z8-diag-validation/v1',target='z8-c311',bindings_sha256=sha(raw),application_sha256=MAIN_SHA256,passed=True,checks=checks,
        scope='Reviewed fixed native ABI plus bounded original/extension instruction execution and one modeled cold boot; application-image admission only.',
        qemu_candidate_sha256=q['firmware_sha256'],code_sha256=build['code_sha256'],native_task_id=observed['task_id'],hooks=hook_evidence,evidence=evidence,
        physical_qualified=False,flashable=False,module_loading_supported=False,
        limitations=['No physical USB link, camera shutdown, warm restart or update installation was tested.','QEMU includes synthetic hardware inputs and peripheral models.','Arbitrary startup/shutdown concurrency before owner publication is not proved.','Only status/echo/identity are implemented; no memory, RTOS monitor, Linux or WLM loader.'])

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in('firmware','bindings','abi-report','core-build','core-emulation','qemu-result','output'):parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args();r=validate(a.firmware,a.bindings,a.abi_report,a.core_build,a.core_emulation,a.qemu_result);atomic_new(a.output,(json.dumps(r,indent=2)+'\n').encode());print(json.dumps({k:r[k]for k in('target','passed','native_task_id','physical_qualified','flashable')}))
if __name__=='__main__':main()
