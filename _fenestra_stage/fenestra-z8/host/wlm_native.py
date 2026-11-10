"""WLM v4 native components: independent payloads, shared library dedup and exact variants.
No device access. Compression is bounded LZSS over content-defined blocks; all
sizes, hashes, targets and selectors are checked again by the cold C loader.
"""
from dataclasses import dataclass
import hashlib
import struct
from z8_native_capsule import decode as decode_capsule,encode as encode_capsule,Patch,IMAGE,HOOK,EXEC,INSTRUCTION,POINTER,NONE
HEADER=160;VARIANT=64;BLOCK=16;LIMIT=16*1024*1024
MAGIC=b'WLMNAT1\0';MAX_VARIANTS=64;MAX_BLOCKS=32768

def digest(data):return hashlib.sha256(data).digest()
def ident(name):
 if not isinstance(name,str)or not 0<len(name)<16 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in name):raise ValueError('canonical component id required')
 return name.encode('ascii').ljust(16,b'\0')
def name_of(raw):
 if len(raw)!=16:raise ValueError('component id width')
 name=raw.split(b'\0',1)[0].decode('ascii');
 if ident(name)!=raw:raise ValueError('component id padding')
 return name

def lzss_pack(data):
 table={};out=bytearray();pos=0
 while pos<len(data):
  flag_at=len(out);out.append(0);flags=0
  for bit in range(8):
   if pos==len(data):break
   length=0;distance=0;key=data[pos:pos+3]
   if len(key)==3:
    candidates=table.get(key,())
    for at in reversed(candidates[-32:]):
     if pos-at>4096:break
     n=0
     while n<18 and pos+n<len(data)and data[at+n]==data[pos+n]:n+=1
     if n>length:length=n;distance=pos-at
     if n==18:break
   take=length if length>=3 else 1
   if take==1:flags|=1<<bit;out.append(data[pos])
   else:out.extend(struct.pack('<H',((take-3)<<12)|(distance-1)))
   for at in range(pos,pos+take):
    k=data[at:at+3]
    if len(k)==3:
     a=table.setdefault(k,[]);a.append(at)
     if len(a)>64:del a[:32]
   pos+=take
  out[flag_at]=flags
 return bytes(out)

def lzss_unpack(data,raw_bytes):
 out=bytearray();at=0
 while len(out)<raw_bytes:
  if at==len(data):raise ValueError('truncated LZSS flags')
  flags=data[at];at+=1
  for bit in range(8):
   if len(out)==raw_bytes:
    if flags>>bit:raise ValueError('noncanonical LZSS flags')
    break
   if flags&(1<<bit):
    if at==len(data):raise ValueError('truncated LZSS literal')
    out.append(data[at]);at+=1
   else:
    if at+2>len(data):raise ValueError('truncated LZSS reference')
    ref=struct.unpack_from('<H',data,at)[0];at+=2;distance=(ref&4095)+1;n=(ref>>12)+3
    if distance>len(out)or n>raw_bytes-len(out):raise ValueError('invalid LZSS reference')
    for _ in range(n):out.append(out[-distance])
 if at!=len(data):raise ValueError('trailing LZSS bytes')
 return bytes(out)

GEAR=tuple(int.from_bytes(hashlib.sha256(bytes([i])).digest()[:4],'little')for i in range(256))
def chunks(data):
 start=0;roll=0
 for at,value in enumerate(data,1):
  roll=((roll<<1)+GEAR[value])&0xffffffff
  if at-start>=256 and (not roll&1023 or at-start==4096):
   yield data[start:at];start=at;roll=0
 if start<len(data):yield data[start:]

def encode_component(name,version,selector_bit,rank,variants,*,target,requires_id):
 cid=ident(name)
 if len(target)!=32 or not 0<version<=0xffffffff or selector_bit<0 or selector_bit>0xffffffff or selector_bit and selector_bit&(selector_bit-1)or not 0<=rank<=0xffffffff or not 0<requires_id<1<<64:raise ValueError('component contract')
 if not 1<=len(variants)<=MAX_VARIANTS:raise ValueError('variant count')
 blobs=[];block_index={};blocks=[];rows=[]
 for row in variants:
  mask,value,raw=row['mask'],row['value'],bytes(row['capsule'])
  if not 0<=mask<=0xffffffff or not 0<=value<=0xffffffff or value&~mask:raise ValueError('variant selector')
  parsed=decode_capsule(raw)
  if raw[32:64]!=target or parsed['requires_id']!=requires_id:raise ValueError('capsule target/dependency')
  refs=[]
  for part in chunks(raw):
   key=digest(part);index=block_index.get(key)
   if index is None:
    index=len(blocks);block_index[key]=index;packed=lzss_pack(part);codec=1 if len(packed)<len(part)else 0;blocks.append((part,packed if codec else part,codec))
   elif blocks[index][0]!=part:raise ValueError('block digest collision')
   refs.append(index)
  stream=struct.pack('<I',len(refs))+struct.pack('<'+'I'*len(refs),*refs)
  rows.append((mask,value,stream,len(raw),digest(raw)))
 if len(blocks)>MAX_BLOCKS:raise ValueError('component block count')
 for i,(m,v,*_)in enumerate(rows):
  if any(not ((v^p[1])&m&p[0])for p in rows[:i]):raise ValueError('overlapping variant selectors')
 voff=HEADER;boff=voff+len(rows)*VARIANT;cursor=boff+len(blocks)*BLOCK;body=bytearray();vtable=bytearray();btable=bytearray()
 for mask,value,stream,raw_n,raw_sha in rows:
  vtable.extend(struct.pack('<6I32s8s',mask,value,2,cursor,len(stream),raw_n,raw_sha,bytes(8)));body.extend(stream);cursor+=len(stream)
 for part,packed,codec in blocks:
  btable.extend(struct.pack('<4I',len(part),cursor,len(packed),codec));body.extend(packed);cursor+=len(packed)
 content=bytes(vtable+btable+body)
 if HEADER+len(content)>LIMIT:raise ValueError('component too large')
 header=bytearray(HEADER);header[:8]=MAGIC;struct.pack_into('<2I',header,8,1,HEADER);header[16:32]=cid
 struct.pack_into('<8I',header,32,version,selector_bit,rank,len(rows),voff,VARIANT,HEADER+len(content),int(not selector_bit));struct.pack_into('<Q',header,64,requires_id)
 header[72:104]=target;header[104:136]=digest(content);struct.pack_into('<3I',header,136,len(blocks),boff,BLOCK)
 result=bytes(header)+content;decode_component(result,target=target,requires_id=requires_id)
 return result

def decode_component(data,*,target=None,requires_id=None,selection=None):
 if not HEADER<=len(data)<=LIMIT or data[:8]!=MAGIC or struct.unpack_from('<2I',data,8)!=(1,HEADER):raise ValueError('component header')
 name=name_of(data[16:32]);version,bit,rank,n,voff,stride,size,flags=struct.unpack_from('<8I',data,32);required=struct.unpack_from('<Q',data,64)[0]
 count,boff,bstride=struct.unpack_from('<3I',data,136)
 if not version or bit and bit&(bit-1)or flags!=int(not bit)or size!=len(data)or not 1<=n<=MAX_VARIANTS or voff!=HEADER or stride!=VARIANT or boff!=HEADER+n*VARIANT or bstride!=BLOCK or not 1<=count<=MAX_BLOCKS or boff+count*BLOCK>len(data)or any(data[148:160])or digest(data[HEADER:])!=data[104:136]:raise ValueError('component metadata/integrity')
 if target is not None and data[72:104]!=target or requires_id is not None and required!=requires_id:raise ValueError('component target/dependency')
 lower=boff+count*BLOCK;blocks=[];ranges=[]
 for i in range(count):
  raw_n,offset,packed_n,codec=struct.unpack_from('<4I',data,boff+i*BLOCK)
  if not 0<raw_n<=4096 or not packed_n or offset<lower or offset+packed_n>len(data)or codec not in(0,1)or codec==0 and raw_n!=packed_n:raise ValueError('component block')
  blocks.append((raw_n,offset,packed_n,codec));ranges.append((offset,offset+packed_n))
 variants=[];chosen=[]
 for i in range(n):
  mask,value,codec,offset,packed_n,raw_n,raw_sha,reserved=struct.unpack_from('<6I32s8s',data,voff+i*VARIANT)
  if value&~mask or codec!=2 or offset<lower or offset+packed_n>len(data)or not 128<=raw_n<=LIMIT or packed_n<4 or any(reserved):raise ValueError('component variant')
  if any(not((value^p['value'])&mask&p['mask'])for p in variants):raise ValueError('ambiguous variants')
  refs=struct.unpack_from('<I',data,offset)[0]
  if not refs or packed_n!=4+refs*4 or refs>65536:raise ValueError('block references')
  indices=struct.unpack_from('<'+'I'*refs,data,offset+4)
  if any(j>=count for j in indices)or sum(blocks[j][0]for j in indices)!=raw_n:raise ValueError('block extent')
  item=dict(mask=mask,value=value,indices=indices,raw_bytes=raw_n,sha256=raw_sha.hex());variants.append(item);ranges.append((offset,offset+packed_n))
  if selection is not None and selection&mask==value:chosen.append(item)
 previous=lower
 # Variant reference streams precede block payloads, in directory order.
 canonical=ranges[count:]+ranges[:count]
 for begin,end in canonical:
  if begin!=previous:raise ValueError('component hidden/overlapping bytes')
  previous=end
 if previous!=len(data):raise ValueError('trailing component bytes')
 result=dict(id=name,version=version,selector_bit=bit,rank=rank,variants=variants,blocks=blocks,target=data[72:104],requires_id=required,sha256=digest(data).hex())
 if selection is not None:
  if len(chosen)!=1:raise ValueError('no unique component variant')
  raw=bytearray()
  for index in chosen[0]['indices']:
   raw_n,offset,packed_n,codec=blocks[index];part=data[offset:offset+packed_n];raw.extend(lzss_unpack(part,raw_n)if codec else part)
  if digest(raw).hex()!=chosen[0]['sha256']:raise ValueError('component raw digest')
  result['capsule']=bytes(raw);result['selected_variant']=chosen[0]
 return result

def compose_components(components,*,target,requires_id):
 unique={};selection=0
 for data in components:
  item=decode_component(data,target=target,requires_id=requires_id);name=item['id']
  if name in unique:
   if unique[name][0]!=data:raise ValueError('conflicting component version/content')
   continue
  if selection&item['selector_bit']:raise ValueError('duplicate selection bit')
  selection|=item['selector_bit'];unique[name]=(data,item)
 if not unique:return None,dict(selection=0,entries=[],components=[])
 if len(unique)>32:raise ValueError('component count')
 patches=[];entries=[];names=[]
 for data,item in sorted(unique.values(),key=lambda p:(p[1]['rank'],p[1]['id'])):
  selected=decode_component(data,target=target,requires_id=requires_id,selection=selection);raw=selected['capsule'];capsule=decode_capsule(raw)
  if raw[32:64]!=target or capsule['requires_id']!=requires_id:raise ValueError('raw target/dependency')
  names.append(item['id'])
  if capsule['entry']:
   if capsule['entry']in entries:raise ValueError('duplicate entry')
   entries.append(capsule['entry'])
  for p in capsule['patches']:
   equal=[q for q in patches if (q.kind,q.flags,q.address,q.old,q.new)==(p.kind,p.flags,p.address,p.old,p.new)]
   if equal:continue
   if any(p.address<q.address+len(q.new)and q.address<p.address+len(p.new)for q in patches):raise ValueError('conflicting native target overlap')
   patches.append(Patch(p.kind,p.flags,p.address,p.old,p.new,NONE))
 patches.sort(key=lambda p:(p.kind,p.address))
 for i,p in enumerate(patches):
  address=None
  if p.kind==HOOK and p.flags==INSTRUCTION and len(p.new)==4:
   word=int.from_bytes(p.new,'little')
   if word&0x7c000000==0x14000000:
    imm=word&0x3ffffff;address=p.address+((imm if imm<0x2000000 else imm-0x4000000)*4)
  elif p.kind==HOOK and p.flags==POINTER and len(p.new)==8:address=int.from_bytes(p.new,'little')
  if address is not None:
   for j,q in enumerate(patches[:i]):
    if q.kind==IMAGE and q.address<=address<q.address+len(q.new):patches[i]=Patch(p.kind,p.flags,p.address,p.old,p.new,j);break
 identity=digest(b''.join(bytes.fromhex(unique[n][1]['sha256'])for n in sorted(unique)))
 package=encode_capsule(patches,int.from_bytes(identity[:8],'little')or 1,requires_id,entries[0]if entries else 0)
 return package,dict(selection=selection,entries=entries,components=names,sha256=digest(package).hex())
