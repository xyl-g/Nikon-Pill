"""Pinned ProRes backend: no original menu, language pool or loader edits.

Semantics originate in NikonFirmwarePatch's z8prores/back_half.py (GPL-3.0-only).
The two MOV lifetime sites also occur in z8orientation/builder.py; their exact
C3.11 preimages are independently required here. No physical claim is implied.
"""
import hashlib
import struct
SITES={
 0x40d1eb88:('fx_z8_prores_type_hook',0xb9406ae8,False),
 0x40d1eb90:('fx_z8_prores_check_hook',0x7100091f,False),
 0x40d1e5f4:('fx_z8_prores_rate_hook',0xd2874a0a,False),
 0x40cee4d0:('fx_z8_prores_label_hook',0x97ffd6f5,True),
 0x40ce9acc:('fx_z8_prores_parser_hook',0x528c6d08,False),
 0x404f8da0:('fx_z8_prores_shoot_hook',0xf9400521,False),
 0x4064d054:('fx_z8_prores_play_hook',0xd299330a,False),
 0x410162ec:('fx_z8_prores_bank_hook',0xa94023ea,False),
 0x40cebe3c:('fx_z8_prores_open_hook',0xf9000268,False),
 0x40ceef10:('fx_z8_prores_close_hook',0xf81e0ff4,False),
}
NATIVE={
 'type_resume':0x40d1eb8c,'check_resume':0x40d1eb94,
 'rate_resume':0x40d1e650,'rate_original_resume':0x40d1e5f8,
 'label_next':0x40ce40a4,'parser_accept':0x40ce9adc,'parser_reject':0x40ce9bbc,
 'shoot_resume':0x404f8da4,'play_resume':0x4064d0d8,
 'bank_resume':0x410162f0,'bank_done':0x41016338,
 'open_resume':0x40cebe40,'close_resume':0x40ceef14,
}
BASE=0x40160000
VALUE=0xa03d5c029

def prepare(original,out):
    for address,(_,word,_) in SITES.items():
        if struct.unpack_from('<I',original,address-BASE)[0]!=word:
            raise ValueError('ProRes C3.11 preimage differs: '+hex(address))
    # Existing video-bank item plus two zero padding bytes. No enlarged DS item
    # or replacement language/menu structure is installed by this backend.
    if original[0x3ed4658:0x3ed465c]!=bytes([1,0,0,0]):
        raise ValueError('ProRes video-bank padding preimage differs')
    stock=original[0x2953a50:0x2953a50+9*24]
    tables=[]
    for numerator in (147,102,45):
        table=bytearray(stock)
        for row in range(9):
            value=struct.unpack_from('<Q',stock,row*24+16)[0]
            if not value or value%(10*(1<<20)):
                raise ValueError('ProRes stock rate preimage differs')
            scaled=(value*numerator+220*(1<<20)//2)//(220*(1<<20))*(1<<20)
            struct.pack_into('<Q',table,row*24+16,scaled)
        tables.append(bytes(table))
    rows=['/* Generated from exact C3.11 native rate records. */',
          'static const uint64_t fx_z8_prores_rates[3][27]={']
    for table in tables:rows.append('{'+','.join(hex(v)+'ull' for v in struct.unpack('<27Q',table))+'},')
    (out/'z8_prores_tables.h').write_text('\n'.join(rows+['};','']))
    return dict(abi=1,grades=['HQ','Standard','LT','Proxy'],value_ram=hex(VALUE),
        original_rate_table=hex(BASE+0x2953a50),rate_rows=9,
        rate_tables_sha256=[hashlib.sha256(t).hexdigest() for t in tables],
        hook_sites=len(SITES),native_movie_lifetime_tracking=True,
        physical_recording_qualified=False,
        limitations=['Native recording setup before successful MOV create is not a proved global capture lock.',
                     'Live-view codec icons and menu reset retain the original patch limitations.'])
