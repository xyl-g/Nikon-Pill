"""Compare the production native C composer with the host reference for 31 WLM sets.
No target memory writes, firmware entry calls or physical transports are used.
"""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
from wlm_native import compose_components
ROOT=Path(__file__).resolve().parents[1]

def verify(components,driver,output):
 directory=Path(components);manifest=json.loads((directory/'manifest.json').read_text());target=bytes.fromhex(manifest['target_main_sha256']);required=int(manifest['requires_id'],0)
 rows={r['id']:r for r in manifest['components']};data={}
 for name,row in rows.items():
  raw=(directory/row['file']).read_bytes()
  if len(raw)!=row['bytes']or hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('component changed: '+name)
  data[name]=raw
 names=['framing','prores','h265','log_colour','photo_tools'];results=[];out=Path(output);out.mkdir(parents=True,exist_ok=False)
 for mask in range(1,32):
  # Repeated shared inputs model independent installed WLM directories.
  selected=[n for i,n in enumerate(names)if mask&(1<<i)];ids=[n for name in selected for n in [name,*manifest['plugin_dependencies'][name]]]
  expected,meta=compose_components([data[n]for n in ids],target=target,requires_id=required);dest=out/(str(mask)+'.fnc')
  command=[str(Path(driver).resolve()),'0',str(dest.resolve()),target.hex(),format(required,'x'),*[str((directory/rows[n]['file']).resolve())for n in ids]]
  actual=subprocess.run(command,capture_output=True,text=True,timeout=30,check=True);receipt=json.loads(actual.stdout)
  if receipt['result']or receipt['live']or dest.read_bytes()!=expected or receipt['entries']!=meta['entries']or receipt['selection']!=mask:raise ValueError('C/reference composition differs: '+str(mask))
  results.append(dict(mask=mask,passed=True,sha256=meta['sha256'],bytes=len(expected),entries=meta['entries'],peak_composer_scratch_bytes=receipt['peak']))
 report=dict(schema='fenestra.wlm-native-c-composition/v1',passed=True,component_manifest_sha256=hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest(),cases=results,physical_camera_used=False,scope='production C executed on host; no MMU/native stock function execution')
 (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');return report

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--components',required=True);p.add_argument('--driver',required=True);p.add_argument('--output',required=True);a=p.parse_args();r=verify(a.components,a.driver,a.output);print(json.dumps(dict(passed=r['passed'],cases=len(r['cases']),peak=max(x['peak_composer_scratch_bytes']for x in r['cases']))))
if __name__=='__main__':main()
