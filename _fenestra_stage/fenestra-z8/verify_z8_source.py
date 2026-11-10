"""Verify the source inventory of this local Z8 adaptation; does not access devices."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
 manifest=json.loads((ROOT/'Z8-PORT-INVENTORY.json').read_text(encoding='utf-8'))
 for name,digest in manifest['files'].items():
  path=(ROOT/name).resolve()
  if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('source identity differs: '+name)
 print('PASS: '+str(len(manifest['files']))+' source files; complete Z8 port inventory')
if __name__=='__main__':main()
