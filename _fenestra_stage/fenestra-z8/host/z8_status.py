"""Read complete Fenestra Z8 C3.11 identity/status through native USB PTP.

Run explicitly after the full core is installed, Activate selected and USB
Debug enabled locally. Importing this module never opens a camera.
"""
import json,struct
from ptp_client import Client
APPLICATION='2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'

def read_status(client):
 hello=client.hello();identity=hello['identity']
 if(identity['firmware_code']!=311 or identity['model']!='Nikon Z8' or
    identity['application_sha256']!=APPLICATION or identity['module_target_sha256']!=APPLICATION):
  raise ValueError('connected Fenestra identity is not the exact Z8 C3.11 target')
 raw=client.query(1)
 if len(raw)!=32:raise ValueError('Z8 status response length differs')
 firmware,active,capabilities=struct.unpack_from('<3I',raw)
 if firmware!=311 or active not in(0,1) or capabilities!=client.capabilities:
  raise ValueError('Z8 status and identity disagree')
 return dict(model=identity['model'],firmware=identity['firmware'],fenestra_active=bool(active),
             usb_debug_connected=True,capabilities=capabilities,
             application_sha256=APPLICATION,build_identity=identity['build_identity'])

def main():
 from ptp_transport import open_native_transport
 with open_native_transport('z8-c311')as transport:
  print(json.dumps(read_status(Client(transport)),indent=2,ensure_ascii=False))
if __name__=='__main__':main()
