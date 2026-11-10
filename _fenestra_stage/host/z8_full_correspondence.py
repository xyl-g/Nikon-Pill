"""Whole-code Z9 C5.32 -> Z8 C3.11 static correspondences, never an ABI proof.

Inputs are exact original applications. Searches are restricted to initialized
code before file offset 0x1164000; data is never a normalized-code candidate.
RAM/data addresses are recovered from paired instructions, not a uniform delta.
"""
import argparse
from array import array
import hashlib
import json
import os
import tempfile
from pathlib import Path
import re
import struct
import sys
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_REG, ARM64_OP_MEM
from platform_profiles import PROFILES, BASE

ROOT = Path(__file__).resolve().parents[1]
CODE_END = 0x1164000
PINS = {'z9': PROFILES['z9-c532']['sha256'], 'z8': '2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'}
EXTRA = {'hook.system':0x40d65a98,'hook.navigate':0x403afa44,'hook.ptp.lookup':0x407e2e0c,
         'hook.ptp.session':0x407e1834,'hook.ptp.operations':0x4035705c,'hook.rtos.printf':0x40c05714,
         'ptp.payload':0x407e337c,'ptp.capacity':0x407e33b8,'ptp.incoming':0x407e3460,
         'ptp.response':0x407e346c,'ptp.send':0x407e375c,
         'card.capacity.call0':0x402dc99c,'card.capacity.function0':0x408b94f8,
         'card.capacity.call1':0x408b9538,'card.capacity.function1':0x408bb1f8,
         'card.capacity.call2':0x408bb248,'card.capacity.function2':0x408bcd54,
         'card.capacity.loop':0x408bcda4,'card.capacity.error':0x408bb270,
         'linux.queue.initialize':0x40c9d184,'linux.channels.initialize':0x40c9eb90,'linux.callback.register':0x40ca0130}


def sha(data):return hashlib.sha256(data).hexdigest()

def write_json(path,data):
    path=Path(path)
    descriptor,temporary=tempfile.mkstemp(prefix='.correspondence-',suffix='.json',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(data,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)

def norm_word(word):
    if word & 0x7c000000 == 0x14000000:return word & 0xfc000000
    if word & 0xff000010 == 0x54000000:return word & 0xff00001f
    if word & 0x7e000000 == 0x34000000:return word & 0xff00001f
    if word & 0x7e000000 == 0x36000000:return word & 0xfff8001f
    if word & 0x1f000000 == 0x10000000:return word & 0x9f00001f
    if word & 0x1f000000 == 0x11000000:return word & 0xffc003ff
    if word & 0x1f800000 == 0x12800000:return word & 0xffe0001f
    return word


def normalize(data):
    source=array('I');source.frombytes(data[:len(data)//4*4])
    if sys.byteorder!='little':source.byteswap()
    result=array('I',(norm_word(word) for word in source))
    if sys.byteorder!='little':result.byteswap()
    return result.tobytes()


def occurrences(haystack, needle, limit=512):
    result=[];at=-1
    if not needle:return result
    while len(result)<limit:
        at=haystack.find(needle,at+1)
        if at<0:break
        if not at&3:result.append(BASE+at)
    return result


def in_rom(value,image):return BASE<=value<BASE+len(image)
def in_ram(value):return 0xa00000000<=value<0xa40000000


def shared_source_lines(text):
    """Retain source branches possible for profile 1; unknown feature flags stay possible."""
    active=True;stack=[]
    for number,line in enumerate(text.splitlines(),1):
        directive=re.match(r'\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)',line)
        if directive:
            kind,expression=directive.groups()
            if kind in ('if','ifdef','ifndef'):
                match=re.search(r'FX_PLATFORM_PROFILE\s*(==|!=)\s*(\d+)',expression)
                selected=None if not match else ((int(match[2])==1)==(match[1]=='=='))
                stack.append((active,selected))
                active=active and selected is not False
            elif kind=='elif' and stack:
                parent,selected=stack[-1]
                match=re.search(r'FX_PLATFORM_PROFILE\s*(==|!=)\s*(\d+)',expression)
                branch=None if not match else ((int(match[2])==1)==(match[1]=='=='))
                active=parent and selected is not True and branch is not False
                stack[-1]=(parent, True if selected is True or branch is True else None)
            elif kind=='else' and stack:
                parent,selected=stack[-1];active=parent and selected is not True
            elif kind=='endif' and stack:
                active,_=stack.pop()
        elif active:
            yield number,line


def inventory(z9):
    items={}
    def put(address,source,line,label,kind='unknown',scope='z9'):
        key=hex(address)
        item=items.setdefault(key,dict(z9=key,kind=kind,labels=[],sources=[]))
        if kind!='unknown':item['kind']=kind
        if label and label not in item['labels']:item['labels'].append(label)
        record=dict(path=source,line=line,scope=scope)
        if record not in item['sources']:item['sources'].append(record)
    profile=PROFILES['z9-c532']
    for name,address in profile['functions'].items():put(address,'host/platform_profiles.py',0,name,'function')
    for name,(address,size) in profile['tables'].items():
        put(address,'host/platform_profiles.py',0,name,'table');items[hex(address)]['bytes']=size
    for name,address in EXTRA.items():put(address,'host/z8_full_correspondence.py',0,name,'function')
    paths=list((ROOT/'src/platform/z9_c532').rglob('*'))
    paths += [path for directory in ('src','include') for path in (ROOT/directory).glob('*') if path.is_file()]
    for path in sorted(set(paths)):
        if not path.is_file() or path.suffix not in ('.c','.h','.S','.inc'):continue
        scope='z9-platform' if 'z9_c532' in path.parts else 'shared-profile1-possible'
        text=path.read_text(encoding='utf-8')
        lines=enumerate(text.splitlines(),1) if scope=='z9-platform' else shared_source_lines(text)
        for line_number,line in lines:
            for match in re.finditer(r'\b(0x[0-9a-fA-F]+)(?:[uUlL]+)?\b',line):
                address=int(match[1],16)
                if not in_rom(address,z9) and not in_ram(address):continue
                label=line.strip()[:300]
                kind='function' if re.search(r'\bFN\s*\(',line) and address<BASE+CODE_END else 'unknown'
                if in_ram(address):kind='global'
                put(address,str(path.relative_to(ROOT)).replace('\\','/'),line_number,label,kind,scope)
    return list(sorted(items.values(),key=lambda row:int(row['z9'],16)))


class Matcher:
    def __init__(self,z9,z8):
        self.z9,self.z8=z9,z8
        self.raw9,self.raw8=z9[:CODE_END],z8[:CODE_END]
        self.norm9,self.norm8=normalize(self.raw9),normalize(self.raw8)
        self.dis=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN);self.dis.detail=True
        self.rows={};self.mapping={};self.conflicts=[];self.requested=set()

    def instructions(self,image,address,length=1024):
        offset=address-BASE
        if offset<0 or offset>=CODE_END:return []
        return list(self.dis.disasm(image[offset:min(offset+length,CODE_END)],address))

    def prefix_limit(self,address):
        instructions=self.instructions(self.z9,address,512)
        if not instructions:return 0
        for ins in instructions:
            if ins.mnemonic in ('ret','br','eret','b'):
                return min(512,ins.address+4-address)
        return min(512,len(instructions)*4)

    def admit(self,old,new,method,**evidence):
        previous=self.mapping.get(old)
        if previous and previous['address']!=hex(new):
            self.conflicts.append(dict(z9=hex(old),previous=previous,candidate=hex(new),method=method,evidence=evidence))
            return False
        if previous:return False
        self.mapping[old]=dict(address=hex(new),status='unique_static_correspondence',abi_validated=False,
                               evidence=dict(method=method,**evidence))
        return True

    def seed(self,item):
        old=int(item['z9'],16);offset=old-BASE
        self.requested.add(old)
        limit=self.prefix_limit(old)
        if not limit:return
        candidates=[];method=None;used=0
        for kind,hay,needle in (('exact-code-prefix',self.raw8,self.raw9),('normalized-code-prefix',self.norm8,self.norm9)):
            sizes=sorted(set(size for size in (8,12,16,24,32,48,64,96,128,192,256,384,512,limit) if size<=limit))
            for size in sizes:
                candidates=occurrences(hay,needle[offset:offset+size]);used=size;method=kind
                if len(candidates)==1 and size>=16:
                    self.admit(old,candidates[0],kind,window=size,z9_original_sha256=sha(self.z9[offset:offset+size]),
                               z8_original_sha256=sha(self.z8[candidates[0]-BASE:candidates[0]-BASE+size]))
                    break
                if not candidates:break
            if old in self.mapping:break
        self.rows[old]=dict(z9=hex(old),labels=item['labels'],prefix_limit=limit,
                           method=method,window=used,candidates=[hex(x) for x in candidates],abi_validated=False)

    def contextual(self):
        additions=0
        for old,row in self.rows.items():
            if old in self.mapping or not any(self.z9[old-BASE:old-BASE+16]):continue
            for before,after in ((16,32),(32,64),(64,128),(128,256)):
                start=old-BASE-before;end=old-BASE+after
                if start<0 or end>CODE_END:continue
                pattern=self.norm9[start:end]
                candidates=occurrences(self.norm8,pattern)
                if len(candidates)==1:
                    new=candidates[0]+before
                    if self.admit(old,new,'bounded-neighbor-code-context',before_bytes=before,after_bytes=after,
                                  window_sha256=sha(pattern),note='Includes nearby original code for short-wrapper disambiguation; not an ABI proof.'):
                        additions+=1
                    break
        return additions

    def cfg_instructions(self,image,address,maximum=16384):
        pending=[address];seen={}
        while pending and len(seen)<4096:
            block=pending.pop()
            if not address<=block<min(address+maximum,BASE+CODE_END):continue
            for ins in self.instructions(image,block,min(1024,address+maximum-block)):
                if ins.address in seen:break
                seen[ins.address]=ins
                if ins.mnemonic in ('ret','br','eret','b'):break
                if (ins.mnemonic.startswith('b.') or ins.mnemonic in ('cbz','cbnz','tbz','tbnz')):
                    target=ins.operands[-1]
                    if target.type==ARM64_OP_IMM and address<=target.imm<address+maximum:pending.append(target.imm)
        return [seen[at] for at in sorted(seen)]

    def paired(self,old,new,maximum=4096):
        left_cfg=self.cfg_instructions(self.z9,old);right_cfg=self.cfg_instructions(self.z8,new)
        if len(left_cfg)==len(right_cfg) and all(a.address-old==b.address-new and norm_word(int.from_bytes(a.bytes,'little'))==norm_word(int.from_bytes(b.bytes,'little')) for a,b in zip(left_cfg,right_cfg)):
            return list(zip(left_cfg,right_cfg))
        left=self.instructions(self.z9,old,maximum);right=self.instructions(self.z8,new,maximum)
        pairs=[]
        for a,b in zip(left,right):
            if a.address-old!=b.address-new or norm_word(int.from_bytes(a.bytes,'little'))!=norm_word(int.from_bytes(b.bytes,'little')):break
            pairs.append((a,b))
            if a.mnemonic in ('ret','br','eret','b'):break
        return pairs

    def edges(self):
        additions=0
        for old,record in list(self.mapping.items()):
            if old not in self.requested or not BASE<=old<BASE+CODE_END:continue
            new=int(record['address'],16)
            for a,b in self.paired(old,new):
                if a.mnemonic not in ('bl','b') or not a.operands or a.operands[0].type!=ARM64_OP_IMM:continue
                target=a.operands[0].imm;candidate=b.operands[0].imm
                if not BASE<=target<BASE+CODE_END or not BASE<=candidate<BASE+CODE_END:continue
                # A direct edge under an aligned instruction prefix supports a
                # small wrapper target too, but never proves its callable ABI.
                off9,off8=target-BASE,candidate-BASE
                length=min(16,self.prefix_limit(target))
                if length<4 or self.norm9[off9:off9+length]!=self.norm8[off8:off8+length]:continue
                if self.admit(target,candidate,'paired-direct-call-edge',caller_z9=hex(old),caller_z8=hex(new),
                              site_z9=hex(a.address),site_z8=hex(b.address),prefix_bytes=length):additions+=1
        return additions

    def resolve_short(self):
        additions=0
        for old,row in self.rows.items():
            if old in self.mapping:continue
            left=self.instructions(self.z9,old,32)
            if not left:continue
            a=left[0]
            if a.mnemonic=='b' and a.operands[0].type==ARM64_OP_IMM and a.operands[0].imm in self.mapping:
                wanted=int(self.mapping[a.operands[0].imm]['address'],16)
                found=[]
                for candidate_text in row['candidates']:
                    candidate=int(candidate_text,16)
                    right=self.instructions(self.z8,candidate,4)
                    if right and right[0].mnemonic=='b' and right[0].operands[0].imm==wanted:found.append(candidate)
                if len(found)==1 and self.admit(old,found[0],'tail-thunk-to-matched-target',target_z9=hex(a.operands[0].imm),target_z8=hex(wanted)):
                    additions+=1
        return additions

    def refs(self,instructions):
        registers={};result=[]
        for ins in instructions:
            ops=ins.operands
            before=dict(registers)
            try:
                _,written=ins.regs_access()
                for reg in written:registers.pop(self.dis.reg_name(reg),None)
            except Exception:pass
            name=ins.mnemonic
            if ops and ops[0].type==ARM64_OP_REG:
                dest=ins.reg_name(ops[0].reg)
                if name in ('mov','movz','movn') and len(ops)>1 and ops[1].type==ARM64_OP_IMM:
                    registers[dest]=ops[1].imm & ((1<<64)-1)
                elif name=='movk' and len(ops)>1 and ops[1].type==ARM64_OP_IMM:
                    shift=ops[1].shift.value
                    registers[dest]=(before.get(dest,0)&~(0xffff<<shift))|((ops[1].imm&0xffff)<<shift)
                elif name in ('adr','adrp') and len(ops)>1 and ops[1].type==ARM64_OP_IMM:
                    registers[dest]=ops[1].imm
                elif name in ('add','sub') and len(ops)>2 and ops[1].type==ARM64_OP_REG and ops[2].type==ARM64_OP_IMM:
                    base=before.get(ins.reg_name(ops[1].reg))
                    if base is not None:registers[dest]=base+(1 if name=='add' else -1)*(ops[2].imm<<ops[2].shift.value)
                elif name=='mov' and len(ops)>1 and ops[1].type==ARM64_OP_REG:
                    source=before.get(ins.reg_name(ops[1].reg))
                    if source is not None:registers[dest]=source
                value=registers.get(dest)
                if value is not None and (in_ram(value) or BASE+CODE_END<=value<BASE+len(self.z9)):
                    result.append(dict(site=ins.address,kind='constructed-address',value=value,register=dest))
            for op in ops:
                if op.type==ARM64_OP_MEM:
                    base=before.get(ins.reg_name(op.mem.base))
                    if base is not None and not op.mem.index:
                        value=base+op.mem.disp
                        if in_ram(value) or BASE+CODE_END<=value<BASE+len(self.z9):
                            result.append(dict(site=ins.address,kind='memory-reference',value=value,register=ins.reg_name(op.mem.base)))
            if name in ('bl','blr'):
                for number in range(19):registers.pop('x'+str(number),None);registers.pop('w'+str(number),None)
        return result

    def whole_code_global_refs(self,requested):
        """Find constructed constants in any native block, not only function heads."""
        requested=set(requested);observations={};scanned=0
        words=array('I');words.frombytes(self.raw9[:len(self.raw9)//4*4])
        if sys.byteorder!='little':words.byteswap()
        for index in range(len(words)-3):
            first=words[index]
            if first&0xff800000!=0xd2800000:continue
            register=first&31;parts=words[index:index+4]
            if not all(word&0xff800000==0xf2800000 and word&31==register for word in parts[1:]):continue
            value=0
            for word in parts:value=(value&~(0xffff<<(((word>>21)&3)*16)))|(((word>>5)&0xffff)<<(((word>>21)&3)*16))
            if not any(abs(value-old)<0x4000 for old in requested):continue
            begin=BASE+4*index
            refs=self.refs(self.instructions(self.z9,begin,160));scanned+=1
            for ref in refs:
                if ref['value'] not in requested:continue
                at=ref['site']-BASE
                for before,after in ((16,32),(32,64),(64,128),(128,256)):
                    if at-before<0 or at+after>CODE_END:continue
                    matches=occurrences(self.norm8,self.norm9[at-before:at+after])
                    if len(matches)!=1:continue
                    other_site=matches[0]+before;other_begin=other_site-(ref['site']-begin)
                    if other_begin<BASE:continue
                    others=self.refs(self.instructions(self.z8,other_begin,160))
                    found=[r for r in others if r['site']==other_site and r['kind']==ref['kind'] and r['register']==ref['register']]
                    if len(found)==1:
                        target=found[0]['value']
                        if in_ram(ref['value'])!=in_ram(target):break
                        evidence=dict(site_z9=hex(ref['site']),site_z8=hex(other_site),construction_z9=hex(begin),
                                      construction_z8=hex(other_begin),kind=ref['kind'],context_before=before,context_after=after)
                        observations.setdefault(ref['value'],{}).setdefault(target,[]).append(evidence)
                    break
        for old,found in observations.items():
            if len(found)==1:
                new,sites=next(iter(found.items()))
                self.admit(old,new,'whole-code-materialized-reference',reference_sites=sites)
            else:
                self.conflicts.append(dict(z9=hex(old),method='whole-code-materialized-reference',
                                           candidates={hex(a):sites for a,sites in found.items()}))
        return dict(materialization_sequences_examined=scanned,observed_globals=len(observations),
                    observations=[dict(z9=hex(old),candidates=[dict(address=hex(new),reference_sites=sites) for new,sites in found.items()]) for old,found in sorted(observations.items())])

    def globals(self,requested):
        observations={}
        requested=set(requested)
        for old,record in list(self.mapping.items()):
            if not BASE<=old<BASE+CODE_END:continue
            new=int(record['address'],16)
            pairs=self.paired(old,new)
            if len(pairs)<4:continue
            left=self.refs([a for a,b in pairs]);right=self.refs([b for a,b in pairs])
            right_lookup={(r['site']-new,r['kind'],r['register']):r for r in right}
            for ref in left:
                if ref['value'] not in requested:continue
                other=right_lookup.get((ref['site']-old,ref['kind'],ref['register']))
                if not other:continue
                address=ref['value'];candidate=other['value']
                if in_ram(address)!=in_ram(candidate):continue
                site=dict(function_z9=hex(old),function_z8=hex(new),site_z9=hex(ref['site']),site_z8=hex(other['site']),
                          kind=ref['kind'],paired_instructions=len(pairs))
                observations.setdefault(address,{}).setdefault(candidate,[]).append(site)
        rows=[]
        for old in sorted(requested):
            found=observations.get(old,{})
            row=dict(z9=hex(old),abi_validated=False,candidates=[dict(address=hex(value),reference_sites=sites) for value,sites in sorted(found.items())])
            if len(found)==1:
                new,sites=next(iter(found.items()))
                self.admit(old,new,'paired-materialized-address-reference',reference_sites=sites)
                row.update(status='unique_static_correspondence',z8=hex(new))
            else:row['status']='ambiguous' if found else 'unresolved'
            rows.append(row)
        return rows



def native_linux_evidence(z9,z8):
    """Original initialization/dispatch/store instructions with named API boundaries."""
    from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE
    from unicorn.arm64_const import UC_ARM64_REG_X0,UC_ARM64_REG_LR,UC_ARM64_REG_PC,UC_ARM64_REG_SP
    records=[]
    specifications=[('z9',z9,0x40c9eb90,0x40c9d184,0x40ca0130,0x40c9ec0c,0xa02eb2220,0xa02eb0000,0x4127c438,0x4127cb00,0x412827d0,0x41edc6b0),
                    ('z8',z8,0x40c9d9c8,0x40c9c5f8,0x40c9ea50,0x40c9da40,0xa02e93218,0xa02e91000,0x412774c0,0x41277b88,0x4127d850,0x42a00108)]
    for name,image,initialize,queue_init,register,receive,base,queue_base,pool_create,queue_create,format_text,table in specifications:
        u=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
        u.mem_map(BASE,(len(image)+4095)&~4095);u.mem_write(BASE,image)
        u.mem_map(0xa02800000,0x2800000)
        u.mem_map(0x60000000,0x100000);u.mem_map(0x61000000,0x100000);u.mem_map(0x62000000,4096)
        boundaries={};events=[];executed=[0];stop=0x62000000
        def hook(uc,address,size,user):
            if address==stop:uc.emu_stop();return
            if address in boundaries:
                args=[uc.reg_read(UC_ARM64_REG_X0+i) for i in range(8)]
                events.append(dict(address=hex(address),args=[hex(a) for a in args[:4]]))
                value=boundaries[address](args)
                if value=='stop':uc.emu_stop();return
                uc.reg_write(UC_ARM64_REG_X0,(value or 0)&((1<<64)-1));uc.reg_write(UC_ARM64_REG_PC,uc.reg_read(UC_ARM64_REG_LR));return
            executed[0]+=1
        u.hook_add(UC_HOOK_CODE,hook)
        def call(address,*args,stopped=False):
            u.reg_write(UC_ARM64_REG_SP,0x600f0000);u.reg_write(UC_ARM64_REG_LR,stop)
            for i in range(8):u.reg_write(UC_ARM64_REG_X0+i,args[i] if i<len(args) else 0)
            u.emu_start(address,0,count=200000)
            if not stopped and u.reg_read(UC_ARM64_REG_PC)!=stop:raise AssertionError('Linux native prefix did not return')
        captured=[]
        boundaries[queue_init]=lambda args:0
        boundaries[register]=lambda args:captured.append(args[:4]) or 0
        call(initialize,0xa0f800000)
        if len(captured)!=2 or captured[0] != [base,0,0,receive] or captured[1][0]!=base+0x320 or captured[1][1:3]!=[1,0]:
            raise AssertionError('Linux channel role/receive callback initialization differs')
        del boundaries[register]
        register_stop=register+0x14
        boundaries[register_stop]=lambda args:'stop'
        call(register,base,0,0,receive,stopped=True)
        callback=base+0x318
        if struct.unpack('<Q',u.mem_read(callback,8))[0]!=receive:raise AssertionError('callback +0x318 store differs')
        boundaries.clear();allocations=[];queues=[]
        def allocate(args):
            value=0x61010000+len(allocations)*0x1000;allocations.append(value);return value
        def queue(args):
            value=0x61030000+len(queues)*0x100;queues.append(value);return value
        boundaries[pool_create]=allocate;boundaries[queue_create]=queue;boundaries[format_text]=lambda args:0
        parameter=0x61000000;u.mem_write(parameter,struct.pack('<QI',0xa0f800000,0x8000))
        call(queue_init,parameter)
        queue_pointer=queue_base+0x10
        if len(allocations)!=8 or len(queues)!=16 or struct.unpack('<Q',u.mem_read(queue_pointer,8))[0]!=queues[0]:
            raise AssertionError('queue manager slot-zero +0x10 write differs')
        boundaries.clear();dispatch=[]
        targets=struct.unpack_from('<5Q',image,table-BASE)
        for target in targets:boundaries[target]=lambda args,target=target:dispatch.append((target,args[0])) or 0
        for opcode in (1,2,4,0x10001):
            call(receive,opcode,parameter)
            if dispatch[-1]!=(targets[opcode&0xffff],parameter):raise AssertionError('receive dispatch mask/index/payload differs')
        records.append(dict(target=name,initializer=hex(initialize),receive=hex(receive),callback=hex(callback),queue_pointer=hex(queue_pointer),
                            channel_zero=[hex(a) for a in captured[0]],channel_one=[hex(a) for a in captured[1]],
                            callback_store_instruction=hex(register+0x10),queue_allocations=len(allocations),queue_objects=len(queues),
                            dispatch_table=hex(table),dispatch_cases=4,original_instructions_executed=executed[0],
                            intercepted_api_boundaries=sorted(set(item['address'] for item in events))))
    return dict(schema='fenestra.z8-linux-native-boundaries/v1',software_validation='completed',abi_validated=False,runtime_ready=False,
                checks=records,scope='Real channel initialization, callback field store, queue-pointer slot store and receive dispatch instructions. Pool/queue creation, formatting and receive handlers are intercepted boundaries; no Linux process, queue transport, USB or hardware ran.')


def capacity_evidence(z9,z8,mappings):
    def translated(old):return int(mappings[hex(old)]['address'],16)
    chain=[]
    for call,target in ((0x402dc99c,0x408b94f8),(0x408b9538,0x408bb1f8),(0x408bb248,0x408bcd54)):
        new_call,new_target=translated(call),translated(target)
        observations=[]
        for image,site,wanted in ((z9,call,target),(z8,new_call,new_target)):
            word=struct.unpack_from('<I',image,site-BASE)[0];displacement=word&0x3ffffff
            if displacement&(1<<25):displacement-=1<<26
            actual=site+4*displacement
            if word>>26!=0x25 or actual!=wanted:raise AssertionError('card FCB call-chain destination differs')
            observations.append(dict(site=hex(site),target=hex(actual),original=struct.pack('<I',word).hex()))
        chain.append(dict(z9=observations[0],z8=observations[1]))
    for old,expected in ((0x408bcda4,(0xf1007abf,0x54ffff43)),(0x408bb270,(0x52812035,0x72b00015))):
        if struct.unpack_from('<II',z9,old-BASE)!=expected or struct.unpack_from('<II',z8,translated(old)-BASE)!=expected:
            raise AssertionError('native FCB bound/error instructions differ')
    return dict(schema='fenestra.z8-card-capacity-correspondence/v1',capacity=30,exhausted='0x80000901',chain=chain,
                loop=hex(translated(0x408bcda4)),error=hex(translated(0x408bb270)),abi_validated=False,runtime_ready=False,
                scope='Original static BL targets, CMP #30 loop and FCB exhaustion-code construction; no live filesystem allocation was performed.')


def merge_verified_roles(address_map,z9,z8):
    native=native_linux_evidence(z9,z8)
    left,right=native['checks']
    for field,old,new in (('receive',0x40c9ec0c,0x40c9da40),('callback',0xa02eb2538,0xa02e93530),('queue_pointer',0xa02eb0010,0xa02e91010)):
        if left[field]!=hex(old) or right[field]!=hex(new):raise AssertionError('native role result differs')
        address_map['mappings'][hex(old)]=dict(address=hex(new),status='unique_static_correspondence',abi_validated=False,
            evidence=dict(method='original-native-software-role-check',source='host/z8_full_correspondence.py:native_linux_evidence',
                          report='build/z8-full-port/correspondence/linux-native.json',field=field))
    # These callbacks are identified by the original editor page's event table,
    # not a guessed address delta. Peer tool checks all new entries against ROM.
    editor={0x405065e0:0x4050d034,0x405065e4:0x4050d038,0x405065e8:0x4050d03c,0x405065ec:0x4050d040,
            0x405065f8:0x4050d04c,0x405065fc:0x4050d050,0x40506634:0x4050d088,0x40506638:0x4050d08c,
            0x40506640:0x4050d094,0x40506644:0x4050d098,0x403b217c:0x403b1c64}
    for old,new in editor.items():
        existing=address_map['mappings'].get(hex(old))
        if existing and existing['address']!=hex(new):raise AssertionError('editor event correspondence conflicts')
        address_map['mappings'][hex(old)]=dict(address=hex(new),status='unique_static_correspondence',abi_validated=False,
            evidence=dict(method='original-editor-event-table-role',source='host/z8_ui_profile.py:PROFILE.editor.events',
                          z9_table='0x431bf4f0',z8_table='0x43d9c360'))
    address_map['unresolved']=[item for item in address_map['unresolved'] if item['z9'] not in address_map['mappings']]
    return native,capacity_evidence(z9,z8,address_map['mappings'])



def coverage_evidence(address_map,z9,z8):
    from z8_ui_profile import get_profile,verify_ui_fields
    import build_z8_core
    required=json.loads((ROOT/'src/platform/z8_c311_full/required-addresses.json').read_text())['addresses']
    mappings=address_map['mappings'];missing=[old for old in required if old not in mappings]
    if missing:raise AssertionError('required full native addresses unresolved: '+','.join(missing))
    named=[];tables=[]
    for name,old in PROFILES['z9-c532']['functions'].items():
        if hex(old) not in mappings:raise AssertionError('named function unresolved: '+name)
        named.append(dict(name=name,z9=hex(old),z8=mappings[hex(old)]['address'],abi_validated=False))
    for name,(old,size) in PROFILES['z9-c532']['tables'].items():
        if hex(old) not in mappings:raise AssertionError('named table unresolved: '+name)
        tables.append(dict(name=name,z9=hex(old),z8=mappings[hex(old)]['address'],bytes=size,abi_validated=False))
    ui=get_profile();ui_checks=verify_ui_fields(z8);hooks=[]
    for old,symbol in build_z8_core.ENTRY_HOOKS.items():
        if hex(old) not in mappings:raise AssertionError('entry hook unresolved')
        new=int(mappings[hex(old)]['address'],16)
        hooks.append(dict(symbol=symbol,kind='entry',z9=hex(old),z8=hex(new),original_z8=z8[new-BASE:new-BASE+16].hex(),abi_validated=False))
    for address,symbol in sorted({**ui['ui_hooks'],**ui['font_sites']}.items()):
        hooks.append(dict(symbol=symbol,kind='ui/font callsite',z8=hex(address),original_z8=z8[address-BASE:address-BASE+4].hex(),abi_validated=False))
    unresolved=[];excluded=[]
    for item in address_map['unresolved']:
        row=dict(item)
        if row['z9']=='0x428a2f98':
            row.update(classification='numeric_algorithm_constant_not_address',evidence='src/sha256.c:11 SHA-256 round constants; accidental numeric range overlap')
            excluded.append(row);continue
        if row['z9'] in ('0x401a0000','0xa0f801000'):
            row.update(classification='local_reservation_or_guard_policy',evidence='code cave origin / IPC queue-block upper-bound policy; builder supplies explicitly')
        elif row['z9'] in ('0xa02b6f4c8','0xa02be8df8','0xa03def068','0xa03df2938','0xa03df3d8c','0xa057b0a20','0xa057b0eec','0xa057b2250'):
            if 'src/observation.c' in build_z8_core.SOURCES:raise AssertionError('ZR observation addresses entered Z8 full source set')
            row.update(classification='not_enabled_shared_translation_unit',evidence='src/observation.c fixed ZR capture experiment is absent from build_z8_core.SOURCES')
        else:raise AssertionError('unreviewed native correspondence gap '+row['z9'])
        unresolved.append(row)
    address_map['unresolved']=unresolved;address_map['excluded_numeric_constants']=excluded
    return dict(schema='fenestra.z8-full-correspondence-coverage/v1',target='z8-c311',runtime_ready=False,abi_validated=False,
                inputs=PINS,required_native_addresses=dict(total=len(required),resolved=len(required)-len(missing),missing=missing,
                    entries=[dict(z9=old,z8=mappings[old]['address'],sources=refs,abi_validated=False) for old,refs in required.items()]),
                named_functions=dict(total=len(named),resolved=len(named),entries=named),
                named_tables=dict(total=len(tables),resolved=len(tables),entries=tables),
                hooks=dict(total=len(hooks),resolved=len(hooks),entries=hooks),
                ui_original_checks=len(ui_checks) if isinstance(ui_checks,list) else ui_checks,
                unresolved_review=unresolved,excluded_numeric_constants=excluded,
                scope='Address coverage and original byte/layout assertions only. Scoped native software-execution reports remain separate; static correspondences do not establish complete ABI or hardware qualification.')


def run(output):
    z9=(ROOT/'inputs/z9-c532-main.bin').read_bytes();z8=(ROOT/'inputs/z8-c311-main.bin').read_bytes()
    for name,data in (('z9',z9),('z8',z8)):
        if sha(data)!=PINS[name]:raise ValueError(name+' original application SHA differs')
    items=inventory(z9);matcher=Matcher(z9,z8)
    functions=[item for item in items if BASE<=int(item['z9'],16)<BASE+CODE_END]
    requested_data=[int(item['z9'],16) for item in items if int(item['z9'],16)>=BASE+CODE_END]
    for item in functions:matcher.seed(item)
    contextual_additions=matcher.contextual()
    rounds=[]
    for index in range(8):
        changes=matcher.edges()+matcher.resolve_short();rounds.append(changes)
        if not changes:break
    globals_rows=matcher.globals(requested_data)
    whole_globals=matcher.whole_code_global_refs(requested_data)
    from z8_ui_profile import get_profile
    ui_profile=get_profile()
    # The peer UI tool independently validates the Z8 layout/whole-image identity.
    for old_text,new in ui_profile['address_map'].items():
        matcher.admit(int(old_text,16),new,'native-ui-structure-profile',source='host/z8_ui_profile.py:get_profile',peer_verification='original layout/namespace and hook instruction assertions')
    function_rows=[]
    for item in functions:
        old=int(item['z9'],16);row=matcher.rows.get(old,dict(z9=hex(old),labels=item['labels'],abi_validated=False))
        if old in matcher.mapping:row.update(z8=matcher.mapping[old]['address'],status='unique_static_correspondence',evidence=matcher.mapping[old]['evidence'])
        else:row['status']='ambiguous' if row.get('candidates') else 'unresolved'
        function_rows.append(row)
    unresolved=[dict(z9=item['z9'],labels=item['labels'],kind=item['kind']) for item in items if int(item['z9'],16) not in matcher.mapping]
    address_map=dict(schema='fenestra.z8-full-correspondence/v1',target='z8-c311',inputs=PINS,
                     code_search=dict(base=hex(BASE),file_end=hex(CODE_END),data_searched_as_code=False),
                     abi_validated=False,runtime_ready=False,
                     mappings={hex(old):value for old,value in sorted(matcher.mapping.items())},unresolved=unresolved,
                     limitations=['Unique static matching is not proof of ABI, layout, startup or hardware equivalence.',
                                  'Shared source feature branches are conservatively included when not excluded by profile 1.',
                                  'Data/global mappings require paired materialization/reference sites; unknown addresses never default to identity.'])
    native,capacity=merge_verified_roles(address_map,z9,z8)
    coverage=coverage_evidence(address_map,z9,z8)
    for row in function_rows+globals_rows:
        if row['z9'] in address_map['mappings']:
            record=address_map['mappings'][row['z9']];row.update(z8=record['address'],status=record['status'],evidence=record['evidence'])
    matcher.mapping={int(old,16):record for old,record in address_map['mappings'].items()}
    unresolved=address_map['unresolved']
    summary=dict(schema='fenestra.z8-full-correspondence-summary/v1',inventory_addresses=len(items),requested_functions=len(functions),
                 resolved_functions=sum(int(item['z9'],16) in matcher.mapping for item in functions),requested_data=len(requested_data),
                 resolved_data=sum(int(item['z9'],16) in matcher.mapping for item in items if int(item['z9'],16) in requested_data),
                 total_including_callgraph=len(matcher.mapping),unresolved=len(unresolved),callgraph_round_additions=rounds,
                 conflicts=len(matcher.conflicts),contextual_additions=contextual_additions,abi_validated=False,runtime_ready=False)
    output=Path(output).resolve()
    allowed=ROOT/'build/z8-full-port/correspondence'
    if output!=allowed.resolve():raise ValueError('use owned build/z8-full-port/correspondence directory')
    output.mkdir(parents=True,exist_ok=True)
    for name,data in (('inventory',items),('functions',function_rows),('globals',globals_rows),('whole-code-globals',whole_globals),('address-map',address_map),('conflicts',matcher.conflicts),('linux-native',native),('card-capacity',capacity),('coverage',coverage),('summary',summary)):
        write_json(output/(name+'.json'),data)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'build/z8-full-port/correspondence')
    parser.add_argument('--coverage-only',action='store_true',help='audit existing static map against the complete full-core manifest')
    parser.add_argument('--merge-native-only',action='store_true',help='merge bounded native role checks into an existing static map')
    args=parser.parse_args()
    if args.merge_native_only or args.coverage_only:
        output=args.output_dir.resolve()
        if output!=(ROOT/'build/z8-full-port/correspondence').resolve():raise ValueError('owned correspondence directory required')
        address_map=json.loads((output/'address-map.json').read_text())
        z9=(ROOT/'inputs/z9-c532-main.bin').read_bytes();z8=(ROOT/'inputs/z8-c311-main.bin').read_bytes()
        if sha(z9)!=PINS['z9'] or sha(z8)!=PINS['z8']:raise ValueError('original identities differ')
        native,capacity=merge_verified_roles(address_map,z9,z8)
        coverage=coverage_evidence(address_map,z9,z8)
        for name,data in (('address-map',address_map),('linux-native',native),('card-capacity',capacity),('coverage',coverage)):
            write_json(output/(name+'.json'),data)
        print(json.dumps(dict(software_validation='completed',linux_bindings=3,card_capacity=capacity['capacity'],runtime_ready=False)))
    else:
        print(json.dumps(run(args.output_dir),indent=2))

if __name__=='__main__':main()
