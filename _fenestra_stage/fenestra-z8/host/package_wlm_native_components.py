"""Encode owned native catalogs and verify every installed WLM combination.

Native components are embedded resources, never card-level BOOT files.
"""
import argparse
import hashlib
import json
from pathlib import Path
from z8_native_capsule import decode,encode,Patch,IMAGE,WRITE,NONE
from z8_native_arena import arena_profile
from z8_wlm_native_recipes import BITS,COMPONENTS
from build_wlm_native_components import RANKS
from wlm_native import encode_component,decode_component,compose_components

DEPENDENCIES={'framing':['shared.locale','shared.menu','shared.movie'],
 'prores':['shared.locale','shared.menu','shared.quality','shared.movie'],
 'h265':['shared.locale','shared.menu','shared.quality','shared.movie'],
 'log_colour':['shared.locale','shared.menu','shared.movie'],
 'photo_tools':['shared.locale','shared.movie']}

def sha(data):return hashlib.sha256(data).hexdigest()
def signature(p):return p.kind,p.flags,p.address,p.old,p.new

def load_catalog(directory):
 directory=Path(directory).resolve();m=json.loads((directory/'catalog.json').read_text())
 profile=arena_profile()
 if m['arena_profile']!=profile or m['feature_bits']!=BITS:raise ValueError('catalog arena or feature identity differs')
 target=bytes.fromhex(m['target_main_sha256']);required=profile['requires_id'];result={}
 for c in m['components']:
  if c.get('id')not in RANKS or c.get('version')!=1 or c.get('selector_bit')!=BITS.get(c['id'],0)or c.get('rank')!=RANKS[c['id']]:raise ValueError('catalog component identity/entry rank differs')
  variants=[]
  for row in c['variants']:
   file=(directory/row['capsule']).resolve()
   if not file.is_relative_to(directory) or not file.is_file():raise ValueError('variant path escapes catalog')
   blob=file.read_bytes()
   if len(blob)!=row['bytes']or sha(blob)!=row['sha256']:raise ValueError('variant file digest differs')
   parsed=decode(blob)
   if parsed['requires_id']!=required or bytes.fromhex(parsed['stock_main_sha256'])!=target:raise ValueError('variant target/dependency differs')
   variants.append(dict(mask=row['mask'],value=row['value'],capsule=blob))
  name=c['id']
  if name in result:raise ValueError('duplicate component catalog id')
  result[name]=(c,variants)
 if set(result)!=set(COMPONENTS)|{'shared.locale','shared.menu','shared.quality','shared.movie'}:raise ValueError('exact nine components required')
 return m,result,target,required


def selected_inputs(blobs,mask,duplicates=True):
 # This models actual WLM ownership: each installed plugin contributes itself
 # and only its declared shared resources. Duplicate shared bytes are retained
 # as inputs so the cold composer's exact dedup path is exercised.
 rows=[]
 for name in COMPONENTS:
  if mask&BITS[name]:rows.extend(blobs[n]for n in [name,*DEPENDENCIES[name]])
 return rows if duplicates else list(dict.fromkeys(rows))


def package_catalog(catalog,output):
 m,source,target,required=load_catalog(catalog);output=Path(output).resolve()
 workspace=Path(__file__).resolve().parents[1]
 if output.exists():raise FileExistsError('new component output required')
 if not output.is_relative_to(workspace/'build'):raise ValueError('component output must stay in workspace build')
 catalog=Path(catalog).resolve()
 if output==catalog or output in catalog.parents or catalog in output.parents:raise ValueError('input/output trees overlap')
 blobs={}
 for name,(info,variants)in source.items():
  blobs[name]=encode_component(name,info['version'],info['selector_bit'],info['rank'],variants,target=target,requires_id=required)
 rows=[]
 for mask in range(1,32):
  package,report=compose_components(selected_inputs(blobs,mask),target=target,requires_id=required)
  decoded=decode(package);expected=[];entries=[]
  names=[n for n in COMPONENTS if mask&BITS[n]]
  owners=set(names)
  for n in names:owners.update(DEPENDENCIES[n])
  for name in sorted(owners,key=lambda n:(source[n][0]['rank'],n)):
   candidates=[v for v in source[name][1]if mask&v['mask']==v['value']]
   if len(candidates)!=1:raise ValueError('catalog selector not unique')
   raw=decode(candidates[0]['capsule']);expected.extend(raw['patches'])
   if raw['entry']:entries.append(raw['entry'])
  if {signature(p)for p in decoded['patches']}!={signature(p)for p in expected}:raise ValueError('generic compose changed owned records')
  if report['selection']!=mask or report['entries']!=entries:raise ValueError('generic compose selection/entry order differs')
  if mask==31 and len(entries)!=7:raise ValueError('all five feature entries plus locale/movie required')
  rows.append(dict(mask=mask,passed=True,component_ids=report['components'],entry_addresses=[hex(e)for e in entries],records=len(decoded['patches']),bytes=len(package),sha256=sha(package),duplicate_shared_inputs=len(selected_inputs(blobs,mask))-len(owners)))
 # Independent update: add an explicitly synthetic private RW byte to ProRes's
 # own 64KB slot. No production or other component byte is changed or executed.
 original_hashes={n:sha(b)for n,b in blobs.items()}
 c,variants=source['prores'];original=decode(variants[0]['capsule'])
 fixture=Patch(IMAGE,WRITE,0x408eff00,b'\0',b'\xa5',NONE)
 patches=sorted([*original['patches'],fixture],key=lambda p:(p.kind,p.address))
 updated_raw=encode(patches,original['package_id']^0x100,required,original['entry'])
 updated=encode_component('prores',c['version']+1,c['selector_bit'],c['rank'],[dict(mask=0,value=0,capsule=updated_raw)],target=target,requires_id=required)
 changed=dict(blobs,prores=updated)
 composed,update_report=compose_components(selected_inputs(changed,31),target=target,requires_id=required)
 if sha(updated)==original_hashes['prores']or any(sha(changed[n])!=original_hashes[n]for n in blobs if n!='prores'):raise ValueError('update touched unrelated component')
 if not any(signature(p)==signature(fixture)for p in decode(composed)['patches']):raise ValueError('independent update was lost')
 shared=source['shared.movie'];different=encode_component('shared.movie',2,0,shared[0]['rank'],shared[1],target=target,requires_id=required)
 conflict_rejected=False
 try:compose_components([*selected_inputs(blobs,31),different],target=target,requires_id=required)
 except ValueError:conflict_rejected=True
 if not conflict_rejected:raise ValueError('conflicting shared version accepted')
 same_version=bytearray(blobs['shared.movie']);same_version[40]^=1
 # rank is header metadata: same id/version but a distinct complete SHA. The
 # decoder accepts its valid structure; the dedup layer must still reject it.
 same_version_rejected=False
 try:compose_components([*selected_inputs(blobs,31),bytes(same_version)],target=target,requires_id=required)
 except ValueError:same_version_rejected=True
 if not same_version_rejected:raise ValueError('same-version changed shared library accepted')
 output.mkdir(parents=True)
 components=[]
 for name,blob in blobs.items():
  filename=name+'.wnc';(output/filename).write_bytes(blob)
  info=source[name][0]
  components.append(dict(id=name,file=filename,bytes=len(blob),sha256=sha(blob),version=info['version'],selector_bit=info['selector_bit'],rank=info['rank'],variants=len(info['variants'])))
 result=dict(schema='fenestra.wlm-native-encoded-components/v1',passed=True,target_main_sha256=target.hex(),requires_id=hex(required),
  source_catalog=str(catalog/'catalog.json'),source_catalog_sha256=sha((catalog/'catalog.json').read_bytes()),
  components=components,plugin_dependencies=DEPENDENCIES,selections=rows,
  independent_update=dict(passed=True,fixture_only=True,changed_component='prores',old_sha256=original_hashes['prores'],new_sha256=sha(updated),other_eight_components_unchanged=True,composed_entries=[hex(e)for e in update_report['entries']],
   conflicting_shared_version_rejected=conflict_rejected,same_version_different_sha_rejected=same_version_rejected),
  qualification=dict(host_exact_composition=True,runtime_native_transaction=False,physical_camera=False))
 (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
 return result


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--catalog',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 m=package_catalog(a.catalog,a.out);print(json.dumps(dict(passed=m['passed'],components=len(m['components']),selections=len(m['selections']),manifest=str(a.out/'manifest.json'))))
if __name__=='__main__':main()
