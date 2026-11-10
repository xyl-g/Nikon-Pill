"""Encode and inspect canonical WLM v4 containers without device access.

Only the inner v2/v3 service frontend executes as an EL0 module. Native
components remain separately identified payloads. Every byte after the 192-byte
header belongs to the exact 64-byte directory, frontend or a component; padding,
hidden gaps and trailing bytes are rejected. Hashes detect corruption, not trust.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from module_header import validate_package,PACKAGE_LIMIT
from wlm_native import decode_component,ident,name_of

MAGIC=b'FENESTRA'
VERSION=4
HEADER=192
COMPONENT=64
MAX_COMPONENTS=32
LIMIT=16*1024*1024
NATIVE_ABI=1
SHARED=1
DEFAULT_CAPABILITIES=0x7f0000

def digest(data):return hashlib.sha256(data).digest()

def module_ident(name):
 if not isinstance(name,str) or not 0<len(name)<16 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in name):raise ValueError('canonical lowercase module id required')
 return name.encode('ascii').ljust(16,b'\0')

def module_name(raw):
 if len(raw)!=16:raise ValueError('module id width')
 try:name=raw.split(b'\0',1)[0].decode('ascii')
 except UnicodeError as error:raise ValueError('module id is not ASCII') from error
 if module_ident(name)!=raw:raise ValueError('module id padding')
 return name

def binary(value,label):
 if not isinstance(value,(bytes,bytearray,memoryview)):raise ValueError(label+' must be bytes')
 return bytes(value)

def frontend_check(frontend,target,capabilities):
 if not 128<len(frontend)<=PACKAGE_LIMIT:raise ValueError('frontend exceeds the 1 MiB module package limit')
 if struct.unpack_from('<I',frontend,8)[0] not in (2,3):raise ValueError('bundle frontend must be an ordinary v2/v3 service module')
 return validate_package(frontend,target,capabilities=capabilities)

def encode_bundle(frontend:bytes,module_id:str,components:list[bytes],target:bytes)->bytes:
 """Return a canonical v4 file; keep component order and reject duplicate ids."""
 frontend=binary(frontend,'frontend');target=binary(target,'target');mid=module_ident(module_id)
 if len(target)!=32:raise ValueError('target SHA256 must be 32 bytes')
 frontend_check(frontend,target,DEFAULT_CAPABILITIES)
 if not isinstance(components,(list,tuple)) or len(components)>MAX_COMPONENTS:raise ValueError('at most 32 native components are permitted')
 payloads=[];seen=set()
 for value in components:
  data=binary(value,'component');metadata=decode_component(data,target=target);name=metadata['id']
  if name in seen:raise ValueError('duplicate component id in one bundle')
  seen.add(name);payloads.append((data,metadata))
 front_offset=HEADER+len(payloads)*COMPONENT;cursor=front_offset+len(frontend);table=bytearray();body=bytearray(frontend)
 for data,metadata in payloads:
  table.extend(struct.pack('<16s4I32s',ident(metadata['id']),metadata['version'],int(not metadata['selector_bit']),cursor,len(data),digest(data)))
  body.extend(data);cursor+=len(data)
  if cursor>LIMIT:raise ValueError('bundle exceeds the 16 MiB package limit')
 if cursor>LIMIT:raise ValueError('bundle exceeds the 16 MiB package limit')
 content=bytes(table+body);header=bytearray(HEADER);header[:8]=MAGIC
 struct.pack_into('<4I',header,8,VERSION,HEADER,1,1);header[24:56]=target;header[56:88]=digest(content)
 struct.pack_into('<4I',header,88,cursor,0,front_offset,len(frontend));header[104:136]=digest(frontend)
 struct.pack_into('<4I',header,136,HEADER,len(payloads),COMPONENT,NATIVE_ABI);header[152:168]=mid
 result=bytes(header)+content
 decode_bundle(result,module_id=module_id,target=target)
 return result

def decode_bundle(data,*,module_id=None,target=None,capabilities=DEFAULT_CAPABILITIES):
 """Validate before returning frontend bytes and component rows with data bytes.

 The embedded target is always checked against every inner payload. Supplying
 target/module_id also binds the file to an expected camera and filename.
 Component metadata comes from the bounded native-component parser; selected
 capsule expansion and target-memory conflict checks remain compose operations.
 """
 data=binary(data,'bundle')
 if not HEADER<=len(data)<=LIMIT or data[:8]!=MAGIC:raise ValueError('bundle size or magic')
 if struct.unpack_from('<4I',data,8)!=(VERSION,HEADER,1,1):raise ValueError('unsupported WLM bundle version/header/ABI/ISA')
 embedded_target=data[24:56]
 if target is not None:
  target=binary(target,'target')
  if len(target)!=32 or embedded_target!=target:raise ValueError('bundle target SHA256 differs')
 if any(data[168:192]):raise ValueError('reserved WLM bundle header bytes set')
 size,flags,front_offset,front_bytes=struct.unpack_from('<4I',data,88)
 table,count,stride,native_abi=struct.unpack_from('<4I',data,136)
 if size!=len(data) or flags or native_abi!=NATIVE_ABI:raise ValueError('bundle size/flags/native ABI')
 name=module_name(data[152:168])
 if module_id is not None and module_ident(module_id)!=data[152:168]:raise ValueError('bundle module id differs from filename')
 if count>MAX_COMPONENTS or table!=HEADER or stride!=COMPONENT or front_offset!=HEADER+count*COMPONENT:raise ValueError('noncanonical 64-byte component directory')
 if front_bytes<=128 or front_bytes>PACKAGE_LIMIT or front_offset>len(data) or front_bytes>len(data)-front_offset:raise ValueError('frontend extent exceeds bundle or module capacity')
 if digest(data[HEADER:])!=data[56:88]:raise ValueError('bundle body checksum differs')
 frontend=data[front_offset:front_offset+front_bytes]
 if digest(frontend)!=data[104:136]:raise ValueError('frontend checksum differs')
 frontend_metadata=frontend_check(frontend,embedded_target,capabilities)
 cursor=front_offset+front_bytes;components=[];seen=set()
 for i in range(count):
  raw_id,version,component_flags,offset,component_bytes,checksum=struct.unpack_from('<16s4I32s',data,table+i*COMPONENT)
  try:component_id=name_of(raw_id)
  except (ValueError,UnicodeError) as error:raise ValueError('component directory id') from error
  if component_id in seen:raise ValueError('duplicate component id in one bundle')
  if not version or component_flags&~SHARED or offset!=cursor or not component_bytes or offset>len(data) or component_bytes>len(data)-offset:raise ValueError('noncanonical component directory extent/version/flags')
  raw=data[offset:offset+component_bytes]
  if digest(raw)!=checksum:raise ValueError('component directory checksum differs')
  metadata=decode_component(raw,target=embedded_target)
  if metadata['id']!=component_id or metadata['version']!=version or component_flags!=int(not metadata['selector_bit']):raise ValueError('component directory disagrees with inner native component')
  seen.add(component_id);cursor+=component_bytes
  components.append(dict(metadata,flags=component_flags,offset=offset,bytes=component_bytes,data=raw))
 if cursor!=len(data):raise ValueError('hidden or trailing WLM bundle bytes')
 return dict(version=VERSION,module_id=name,target=embedded_target,package_bytes=len(data),package_sha256=digest(data).hex(),
             frontend=frontend,frontend_offset=front_offset,frontend_bytes=front_bytes,frontend_sha256=digest(frontend).hex(),
             frontend_metadata=frontend_metadata,components=components,component_count=count,native_abi=NATIVE_ABI)

def summary(parsed):
 return dict(version=parsed['version'],module_id=parsed['module_id'],target_sha256=parsed['target'].hex(),package_bytes=parsed['package_bytes'],package_sha256=parsed['package_sha256'],
             frontend_bytes=parsed['frontend_bytes'],frontend_sha256=parsed['frontend_sha256'],frontend=parsed['frontend_metadata'],component_count=parsed['component_count'],
             components=[dict(id=row['id'],version=row['version'],shared=bool(row['flags']&SHARED),selector_bit=row['selector_bit'],rank=row['rank'],bytes=row['bytes'],sha256=row['sha256'],variants=len(row['variants'])) for row in parsed['components']])

def main():
 parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
 pack=sub.add_parser('pack');pack.add_argument('--frontend',type=Path,required=True);pack.add_argument('--module-id',required=True);pack.add_argument('--component',type=Path,action='append',default=[]);pack.add_argument('--target',required=True);pack.add_argument('--output',type=Path,required=True)
 inspect=sub.add_parser('inspect');inspect.add_argument('bundle',type=Path);inspect.add_argument('--module-id');inspect.add_argument('--target')
 args=parser.parse_args()
 if args.command=='pack':
  target=bytes.fromhex(args.target);blob=encode_bundle(args.frontend.read_bytes(),args.module_id,[p.read_bytes() for p in args.component],target)
  args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes(blob);result=decode_bundle(blob,module_id=args.module_id,target=target)
 else:result=decode_bundle(args.bundle.read_bytes(),module_id=args.module_id,target=bytes.fromhex(args.target) if args.target else None)
 print(json.dumps(summary(result),indent=2))

if __name__=='__main__':main()
