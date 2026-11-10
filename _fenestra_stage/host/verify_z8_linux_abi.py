"""Run stock Z8 IPC pool/offset instructions with explicit native boundaries.

Allocator metadata helpers are boundaries, never hardware success claims.
The queue block acceptance ceiling is local policy; its actual runtime pointer
is a separate required integration observation.
"""
import argparse,json
from pathlib import Path
from unicorn.arm64_const import UC_ARM64_REG_LR
from verify_z8_native_abi import OriginalLab,STOP,require
from cipher_patch import atomic_new
import z8_container
POOL,SIZE=0xa0f000000,0x7ff000

def verify(application):
 checks=[]
 for entry,boundary in ((0x40c9d044,0x40c9e808),(0x40c9cf34,0x40c9da80)):
  lab=OriginalLab(application);seen=[]
  def capture(args):
   seen.append(args[:2]);lab.u.reg_write(UC_ARM64_REG_LR,STOP)
  lab.intercept(boundary,'native.IPC.pool.init.argument_boundary',capture)
  lab.call(entry)
  require(seen==[[POOL,SIZE]],'stock IPC pool address/length differs')
  checks.append(lab.receipt('linux.pool.parameters',entry=hex(entry),pool=hex(POOL),bytes=SIZE,scope='Actual parameter construction; original init call is the stopping boundary.'))
 lab=OriginalLab(application)
 for address,name in ((0x40d2c564,'native.mutex.init'),(0x40c7aa38,'native.index.init'),(0x40c9e59c,'native.allocator.metadata.init')):lab.intercept(address,name)
 lab.write(0xa02e93858,bytes(0x20))
 require(lab.call(0x40c9e808,POOL,SIZE)==1,'original pool init did not accept first initialization')
 require(lab.u64(0xa02e93860)==POOL and lab.u32(0xa02e93868)==SIZE and lab.read(0xa02e93858,1)==b'\x01','original pool globals differ')
 require(lab.call(0x40c9e808,POOL+4096,SIZE-4096)==0,'original duplicate pool init was not rejected')
 require(lab.u64(0xa02e93860)==POOL and lab.u32(0xa02e93868)==SIZE,'duplicate init mutated original pool identity')
 offsets=[]
 for pointer,expected in ((POOL-1,-1),(POOL,0),(POOL+17,17),(POOL+SIZE-64,SIZE-64),(POOL+SIZE-63,-1),(POOL+SIZE,-1)):
  actual=lab.signed32(0x40c9e794,pointer)
  require(actual==expected,'native IPC pointer-to-offset bounds differ')
  offsets.append(dict(pointer=hex(pointer),offset=actual))
 checks.append(lab.receipt('linux.pool.publication_and_offset',pool_global='0xa02e93860',size_global='0xa02e93868',offsets=offsets,scope='Real initializer publication/idempotence and offset bounds; allocator/mutex metadata helpers are named boundaries.'))
 return dict(schema='fenestra.z8-linux-native-abi/v1',target='z8-c311',application_sha256=z8_container.IDENTITIES['application']['sha256'],checks=checks,
             physical_qualified=False,linux_process_executed=False,queue_block_runtime_qualified=False,
             local_queue_acceptance_ceiling=hex(POOL+0x801000))

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--firmware',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 result=verify(a.firmware.read_bytes());atomic_new(a.output,(json.dumps(result,indent=2)+'\n').encode());print(json.dumps(dict(passed=True,checks=len(result['checks']))))
if __name__=='__main__':main()
