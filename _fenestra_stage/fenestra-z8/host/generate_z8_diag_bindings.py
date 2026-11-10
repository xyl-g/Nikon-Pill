"""Generate local Z8 C3.11 diagnostic bindings from the hash-verified original.

Addresses/ABI contracts were reviewed for this exact application. Original
instruction windows and descriptor contents are extracted locally, not bundled.
Generation alone does not qualify native hardware behavior.
"""
import argparse,json
from pathlib import Path
from cipher_patch import atomic_new
from z8_container import read_input,application_from_bytes
ROOT=Path(__file__).resolve().parents[1]

def generate(firmware,external_project=None):
 data,kind=read_input(firmware)
 application,receipt=application_from_bytes(data,external_project=external_project)
 p=json.loads((ROOT/'src/platform/z8_c311/bindings.example.json').read_text())
 p['application']['base']=0x40160000
 p['code']=dict(begin=0x401a0000,end=0x40200000)
 p['ram']=dict(begin=0xa00000000,end=0xa40000000)
 p['native']=dict(allocate=0x40d2c408,task_create=0x40185064,task_resume=0x401855e0,task_delay=0x40187b00,
    ptp_payload=0x407defe4,ptp_capacity=0x407df020,ptp_incoming=0x407df0c8,ptp_response=0x407df0d4,ptp_send=0x407df3c4)
 p['data']=dict(page_roots=0xa032b50c8,ptp_session=0xa047ce474,ptp_operations=0xa047cecd8,ptp_operations_capacity=512)
 descriptor=0xa028ca040;offset=0x4b13000+descriptor-0xa02808000
 p['anchor']=dict(group=2,id=0x297,descriptor=descriptor,descriptor_bytes=application[offset:offset+64].hex(),page_size=96,descriptor_offset=80)
 p['events']=dict(message_id_offset=32,startup_complete=19,shutdown=7)
 p['allocate_pool']=0
 p['task']=dict(attributes=0,priority=28,processor=1,affinity=0xffffffff,stack_bytes=65536,delay_ticks=10)
 for name,address in dict(startup=0x40d60ec8,ptp_lookup=0x407dea74,ptp_session=0x407dd4a0,ptp_operations=0x40356a74).items():
  at=address-p['application']['base'];p['hooks'][name]=dict(address=address,original=application[at:at+16].hex(),continuation=address+16)
 return p

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--firmware',type=Path,required=True)
 p.add_argument('--external-project',type=Path)
 p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();bindings=generate(a.firmware,a.external_project)
 atomic_new(a.output,(json.dumps(bindings,indent=2)+'\n').encode())
 print(json.dumps(dict(target='z8-c311',output=str(a.output),native_hardware_qualified=False)))
if __name__=='__main__':main()
