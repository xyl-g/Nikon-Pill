"""Pinned native control ABI for the complete upstream framing backend.

This verifies original C3.11 entry bytes. It does not install the backend: the
feature-suite composer must validate the complete pinned upstream overlay.
"""
import hashlib
MAIN_BYTES=81807709
MAIN_SHA256='2b43a31c9de838f84b12416de4348a7004e65be07f853ec56ec2b10f3a8e3853'
UPSTREAM_COMMIT='a21fc8c04b6dd863439c4192d387b755633287f6'
BASE=0x40160000
PREIMAGES={
    0x403ec39c:'8da73314',
    0x410d5ec8:'f37bbfa9f303002aecffff972001003621148bd2c181a8f2',
    0x410d61d0:'f60f1df8f55301a9f37b02a916b98ad2d68fa0f25601c0f21600e0f2f303002ae00316aae14f01291f59f19715688dd2',
}
def verify_original(original):
    if len(original)!=MAIN_BYTES or hashlib.sha256(original).hexdigest()!=MAIN_SHA256:
        raise ValueError('framing controls require exact original Z8 C3.11 MAIN')
    for address,expected in PREIMAGES.items():
        if original[address-BASE:address-BASE+len(bytes.fromhex(expected))]!=bytes.fromhex(expected):
            raise ValueError('framing native control preimage differs: '+hex(address))
    return dict(abi=1,upstream_commit=UPSTREAM_COMMIT,
        service='camera.video.framing',native_set='0x403ec39c',native_set_body='0x410d61d0',
        native_get='0x410d5ec8',nraw_property='0x05001261',rate_property='0x0500125b',
        nraw_mirror='0xa03d5c290',nraw_native_order=['Normal','High','Fine','Basic'],
        desqueeze_bank_byte='0xa03d5c0ad',open_gate_bank_byte='0xa03d5c02b',
        desqueeze=['Off','1.25x','1.30x','1.33x (4/3)','1.50x','1.60x','1.80x','2.00x'],
        open_gate_selection='native Frame size/rate transaction only',
        depends_on_complete_upstream_overlay=True,physical_qualified=False,
        native_entry_preimages={hex(a):v for a,v in PREIMAGES.items()})
