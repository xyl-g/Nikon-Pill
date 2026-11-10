"""Offline build, verification and packaging for the complete Z8 C3.11 port."""
import argparse
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parent
COMMANDS={
 'status':('host/z8_status.py',),
 'inspect':('host/z8_port.py','inspect'),
 'extract':('host/z8_port.py','extract'),
 'bindings':('host/z8_port.py','bindings'),
 'generate-diag-bindings':('host/generate_z8_diag_bindings.py',),
 'validate-diag':('host/validate_z8_diag.py',),
 'verify-native':('host/verify_z8_native_abi.py',),
 'build-core':('host/build_z8_core.py',),
 'verify-core':('host/verify_z8_core.py',),
 'repack-core':('host/z8_repack.py',),
 'build-diag':('host/build_z8_diag.py',),
 'build-module':('host/build_sdk_module.py',),
}
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('command',choices=tuple(COMMANDS))
 p.add_argument('arguments',nargs=argparse.REMAINDER)
 a=p.parse_args(); extra=a.arguments
 if a.command=='build-module':
  if any(x=='--target' or x.startswith('--target=')for x in extra):p.error('Z8 entry point fixes --target z8-c311')
  extra=[*extra,'--target','z8-c311']
 command=[sys.executable,'-B',str(ROOT/COMMANDS[a.command][0]),*COMMANDS[a.command][1:],*extra]
 return subprocess.run(command,cwd=ROOT).returncode
if __name__=='__main__':raise SystemExit(main())
