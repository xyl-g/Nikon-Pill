"""Build five independent v4 WLMs; each contains its own native implementation.
Shared components are bundled dependencies; copying any nonempty subset suffices.
"""
import argparse,hashlib,json,sys
from pathlib import Path
from wlm_bundle import encode_bundle,decode_bundle
from wlm_native import compose_components
ROOT=Path(__file__).resolve().parents[1]
def sha(data):return hashlib.sha256(data).hexdigest()
def frontend_rows(directory, names):
 catalog=directory/'artifact-catalog.json'
 if catalog.is_file():
  return {row['id']: (directory/'frontends'/row['id'],row)
          for row in json.loads(catalog.read_text(encoding='utf-8'))['frontends']}
 rows={}
 for name in names:
  folder=directory/name
  report=json.loads((folder/'module.json').read_text(encoding='utf-8'))
  module,=report['modules']
  if report['target']!='z8-c311' or module['id']!=name or module['file']!=name+'.wlm':
   raise ValueError('unexpected frontend build: '+name)
  rows[name]=(folder,module)
 return rows

def build(components,frontends,output):
 c=Path(components).resolve(strict=True);f=Path(frontends).resolve(strict=True);out=Path(output).resolve()
 if out.exists()or not out.is_relative_to(ROOT/'build'):raise ValueError('new owned build directory required')
 doc=json.loads((c/'manifest.json').read_text());target=bytes.fromhex(doc['target_main_sha256']);required=int(doc['requires_id'],0)
 data={}
 for row in doc['components']:
  raw=(c/row['file']).read_bytes()
  if len(raw)!=row['bytes']or sha(raw)!=row['sha256']:raise ValueError('component hash differs: '+row['id'])
  data[row['id']]=raw
 names=['framing','prores','h265','log_colour','photo_tools'];frontrows=frontend_rows(f,names);out.mkdir(parents=True);card=out/'FENESTRA';card.mkdir();rows=[]
 for name in names:
  folder,row=frontrows[name];frontend=(folder/row['file']).read_bytes()
  if sha(frontend)!=row['sha256']or len(frontend)!=row['bytes']:raise ValueError('frontend changed: '+name)
  ids=[name,*doc['plugin_dependencies'][name]];blob=encode_bundle(frontend,name,[data[n]for n in ids],target);decoded=decode_bundle(blob,module_id=name,target=target)
  if decoded['frontend']!=frontend:raise ValueError('frontend changed during packaging')
  (card/(name+'.wlm')).write_bytes(blob);rows.append(dict(id=name,file=name+'.wlm',bytes=len(blob),sha256=sha(blob),frontend_sha256=sha(frontend),frontend_bytes=len(frontend),components=[dict(id=n,sha256=sha(data[n]),bytes=len(data[n]))for n in ids]))
 # Recompose from decoded installed WLM bytes, not the producer directory.
 selections=[]
 for mask in range(1,32):
  chosen=[n for i,n in enumerate(names)if mask&(1<<i)];native=[row['data']for name in chosen for row in decode_bundle((card/(name+'.wlm')).read_bytes(),module_id=name,target=target)['components']];private,meta=compose_components(native,target=target,requires_id=required)
  expected=next(r for r in doc['selections']if r['mask']==mask)
  if sha(private)!=expected['sha256']:raise ValueError('installed WLM composition differs: '+str(mask))
  selections.append(dict(mask=mask,plugin_ids=chosen,private_plan_sha256=sha(private),private_plan_bytes=len(private),entry_addresses=meta['entries']))
 report=dict(schema='fenestra.z8-wlm-plugins/v1',passed=True,target_main_sha256=target.hex(),requires_id=hex(required),components_manifest_sha256=sha((c/'manifest.json').read_bytes()),frontend_catalog_sha256=sha((f/'artifact-catalog.json').read_bytes()) if (f/'artifact-catalog.json').is_file() else None,plugins=rows,selections=selections,external_boot_files=False,independent_installation=True,cold_restart_required=True,physical_qualified=False)
 (out/'plugins.json').write_text(json.dumps(report,indent=2)+'\n');return report
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--components',required=True);p.add_argument('--frontends',required=True,help='artifact catalog or directory of per-plugin SDK builds');p.add_argument('--output',required=True);a=p.parse_args();r=build(a.components,a.frontends,a.output);print(json.dumps(dict(passed=r['passed'],plugins=[dict(id=x['id'],bytes=x['bytes'])for x in r['plugins']]),indent=2))
if __name__=='__main__':main()
