"""Execute bounded original Z8 UI lookup and event-condition ABI paths.

Original AArch64 instructions run in Unicorn with original tables and explicit
synthetic runtime roots. UI hardware, native side effects and RTOS timing are
outside this evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from unicorn import UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_X20,UC_ARM64_REG_X22,UC_ARM64_REG_PC
from verify_z8_native_abi import OriginalLab,SCRATCH,STOP,require
from z8_ui_profile import get_profile,verify_ui_fields

def page_lookup(data):
    p=get_profile();lab=OriginalLab(data)
    root=p['native_pages']['page_roots'];zero=p['native_pages']['page_zero']
    lab.w64(root+16,zero)
    for page in range(1,p['native_pages']['stop']):
        require(lab.call(0x403db694,2,page)==zero+page*96,'original page lookup differs')
    for page in (0,1,663,p['native_pages']['stop']-1,0x10000|663):
        require(lab.call(0x403db694,2,page)==zero+(page&65535)*96,'original page masking differs')
    for group in (7,0xffffffff):
        require(lab.call(0x403db694,group,663)==0,'invalid original page group accepted')
    return lab.receipt('ui.page.lookup.original',native_pages=p['native_pages']['stop']-1,
        scope='Real original 96-byte selector/mask/group arithmetic; runtime group-2 root is initialized explicitly.')

def text_lookup(data):
    p=get_profile();lab=OriginalLab(data);text=p['text']
    lab.w32(text['count_address'],text['base'])
    lab.w64(text['pool_root'],0x40160000+text['blob_directory_file'])
    lab.w64(text['offset_root'],0x40160000+text['table_directory_file'])
    tested=0;outputs=SCRATCH+0x100
    ids=(0,640,889,text['base']-1,text['base'],0xffff)
    for language in range(text['languages']):
        table=struct.unpack_from('<Q',data,text['table_directory_file']+language*8)[0]
        blob=struct.unpack_from('<Q',data,text['blob_directory_file']+language*8)[0]
        for identity in ids:
            index=identity if identity<text['base']else 0
            first,last=struct.unpack('<II',lab.read(table+4*index,8))
            lab.w64(outputs,0);lab.w32(outputs+8,0xffffffff)
            lab.call(0x4032e130,language,identity,outputs,outputs+8)
            pointer,length=lab.u64(outputs),lab.u32(outputs+8)
            require(pointer==blob+first and length==last-first,'original text getter ABI differs')
            require(lab.read(pointer,length)==data[blob+first-0x40160000:blob+last-0x40160000],'original text bytes differ')
            tested+=1
    return lab.receipt('ui.text.lookup.all_languages',languages=text['languages'],strings=text['base'],
        calls=tested,ids=list(ids),scope='Real original text lookup and output stores; 36 original string/offset tables, explicit runtime roots/count.')

def condition_caller(data):
    p=get_profile();lab=OriginalLab(data);c=p['condition_abi'];page=SCRATCH+0x1000
    descriptor=SCRATCH+0x2000;vector=SCRATCH+0x3000;events=SCRATCH+0x4000
    condition=STOP+0x100;callback=STOP+0x200
    lab.write(page,bytes(96));lab.w32(page,(0x7000<<16)|2);lab.w64(page+80,descriptor)
    lab.write(descriptor,bytes(64));lab.w64(descriptor+24,vector);lab.w64(vector,events)
    lab.write(events,struct.pack('<QQQQQQQQ',0x286,callback,condition,0x68c,0x3bb,0,0,0x68c))
    # Native notification is the sole intercepted downstream original boundary.
    # Its sentinel x0 proves a condition callback does not receive the page in x0.
    lab.intercept(0x4038bd1c,'native.ui.event.notification',lambda args:0x8899)
    observed=[]
    def capture(args):
        observed.append(dict(x0=lab.u.reg_read(UC_ARM64_REG_X0),
                             source_page=lab.u.reg_read(UC_ARM64_REG_X20),
                             selected_event=lab.u.reg_read(UC_ARM64_REG_X22)))
        return 1
    lab.intercept(condition,'owned.condition.capture',capture)
    def stop_after_condition(uc,address,size,user):
        if address==c['return_address']:
            uc.reg_write(UC_ARM64_REG_PC,STOP);uc.emu_stop()
    lab.u.hook_add(UC_HOOK_CODE,stop_after_condition)
    lab.call(c['address'],page,0x286,0xdeadbeef)
    require(observed==[dict(x0=0x8899,source_page=page,selected_event=events)],'native condition register contract differs')
    return lab.receipt('ui.event.condition.registers',observed=observed,
        scope='Real original dispatcher selection/search and indirect condition call. Stops immediately after condition return; notification is synthetic, no UI transition or drawing occurs.')

def verify(data):
    static=verify_ui_fields(data)
    return dict(schema='fenestra.z8-ui-native-execution/v1',application_sha256=hashlib.sha256(data).hexdigest(),
        original_static_checks=len(static['checks']),checks=[page_lookup(data),text_lookup(data),condition_caller(data)],
        runtime_ready=False,limitations=['No camera display/input hardware, USB, scheduler or power lifecycle was exercised.',
        'Runtime roots/count and owned page/event data are explicit synthetic state.',
        'Condition notification and callback boundaries are reported; execution stops before the original action/transition.'])

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=verify(args.firmware.read_bytes())
    with args.output.open('x')as output:json.dump(result,output,indent=2);output.write('\n')
    print(json.dumps(dict(checks=len(result['checks']),original_instructions=sum(c['original_instructions_executed']for c in result['checks']),
        original_static_checks=result['original_static_checks'],output=str(args.output)),indent=2))
