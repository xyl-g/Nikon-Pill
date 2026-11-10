"""Five independently owned WLM native recipes from pinned C311 sources.

No BOOT file, precomposed feature MAIN, camera or QEMU is used.  Shared
resources are explicit owners and conditional variants, never last-writer wins.
"""
from dataclasses import dataclass
import binascii
import hashlib
import json
import struct
from z8_native_profile import BASE, check_main
from z8_feature_suite import checked_upstream, VENDOR, COMPILED_SHA256
from z8_native_capsule import _runtime_address, COPY_END_SITE

COMPONENTS=('framing','prores','h265','log_colour','photo_tools')
BITS={name:1<<i for i,name in enumerate(COMPONENTS)}
SHARED_LOCALE='shared.locale'
SHARED_VIDEO='shared.menu'
SHARED_QUALITY='shared.quality'
QUALITY_CODE=0x40a41000
QUALITY_REPUBLISH=0x40a41250
VIDEO_CODE=0x40980000
VIDEO_CONFIG=0x40980f00
VIDEO_ROWS=0x40a30000
VIDEO_EVENTS=0x40a31000
INIT_OFFSET=0x4b13000
INIT_RAM=0xa02808000
MOV_CLOSE=0x40ceef10


def sha(data):return hashlib.sha256(data).hexdigest()
def selected(mask):
    if type(mask)is not int or not 1<=mask<=31:raise ValueError('nonempty exact five-component mask required')
    return [name for name in COMPONENTS if mask&BITS[name]]

@dataclass(frozen=True)
class OwnedWindow:
    offset:int
    old:bytes
    new:bytes
    owner:str
    purpose:str
    @property
    def address(self):return _runtime_address(self.offset,len(self.new))[0]
    def metadata(self):
        return dict(offset=hex(self.offset),address=hex(self.address),bytes=len(self.new),owner=self.owner,purpose=self.purpose,old_sha256=sha(self.old),new_sha256=sha(self.new))

@dataclass(frozen=True)
class NativeRecipe:
    mask:int
    image:bytes
    windows:tuple
    detail:dict
    def metadata(self):
        return dict(schema='fenestra.wlm-native-five-owner-recipe/v1',mask=self.mask,components=selected(self.mask),image_sha256=sha(self.image),windows=[w.metadata()for w in self.windows],detail=self.detail)


def _unit(name):
    from z8patch.compiled import load
    raw,metadata=load(name,'prebuilt','clang')
    return raw,{k:int(v['va'],16)for k,v in metadata['symbols'].items()}


def shared_video_code():
    raw,entries=_unit('shared');code=bytearray(raw)
    # Exact pinned instructions; preserve all branch destinations and registers.
    for offset,old,new in ((0x5c,'0c011036','0c011836'),(0x70,'6c000836','6c001036')):
        if bytes(code[offset:offset+4])!=bytes.fromhex(old):raise ValueError('shared bank instruction preimage differs')
        code[offset:offset+4]=bytes.fromhex(new)
    return bytes(code),entries


def quality_part(eg,prores,h265,prores_ok,h265_ok):
    from z8quality.patcher import build_main,tables
    from z8patch.image import branch
    code,entries=_unit('quality')
    part,detail=build_main(eg,code,entries,h265_ok if h265 else 0,prores_ok if prores else 0)
    raw=bytearray(next(e['new']for e in part.edits if e['offset']==QUALITY_CODE-BASE))
    patches=[]
    # The shared icon router must be stock-equivalent for an absent codec,
    # even if unrelated/persisted spare DS bytes happen to be nonzero.
    gates=[]
    if not h265:gates.extend((at,'29090051',end)for at,end in ((0x1c,0x80),(0xa4,0x108),(0x164,0x1c8)))
    if not prores:gates.extend((at,old,end)for at,old,end in ((0x48,'491b0058',0x80),(0xd0,'09170058',0x108),(0x190,'09110058',0x1c8)))
    for at,old,end in gates:
        if raw[at:at+4]!=bytes.fromhex(old):raise ValueError('quality router gate preimage differs')
        replacement=branch(QUALITY_CODE+at,QUALITY_CODE+end);raw[at:at+4]=replacement
        patches.append(dict(address=hex(QUALITY_CODE+at),old=old,new=replacement.hex()))
    pairs,new,hq,pngs=tables(eg)
    for name,data,enabled in (('tier_star_pairs',pairs,h265),('tier_star_new',new,h265),('tier_hq_pairs',hq,prores)):
        if not enabled:
            at=entries[name]-QUALITY_CODE;raw[at:at+len(data)]=bytes(len(data))
    kept=[]
    for e in part.edits:
        address=BASE+e['offset']
        if address==0x40a20000 and not h265:continue
        if address in (0x440c7b58,0x440c7b60)and not h265:continue
        if address==0x440c7b68 and not prores:continue
        e=dict(e)
        if address==QUALITY_CODE:e['new']=bytes(raw)
        kept.append(e)
    detail.update(prores_enabled=prores,h265_enabled=h265,router_gates=patches,republish=hex(QUALITY_REPUBLISH),selected_icon_data_only=True)
    return kept,detail


def _emit_video(img,menu,mask,callbacks,put):
    from z8patch.image import branch
    if not mask&(BITS['framing']|BITS['prores']|BITS['h265']|BITS['log_colour']):return None
    records={item:(row,pointer)for item,row,pointer in zip(menu.items,menu.rows,menu.pointers)}
    records.update(menu.replacements);order=list(menu.items)
    if mask&BITS['log_colour']:
        order.remove(0x2e38);order.insert(order.index(0x2fc1)+1,0x2e38)
    if mask&(BITS['prores']|BITS['h265']):
        if mask&BITS['prores']:order.remove(0x311b)
        insert=[]
        if mask&BITS['prores']:insert.append(0x311b)
        if mask&BITS['h265']:insert.append(0x315a)
        at=order.index(0x3118)+1;order[at:at]=insert
    if set(order)!=set(records)or len(order)!=len(set(order)):raise ValueError('video menu contribution loses/duplicates a row')
    rows=b''.join(records[i][0]for i in order);pointers=struct.pack('<'+'Q'*len(order),*(records[i][1]for i in order))
    if VIDEO_ROWS+len(rows)>VIDEO_EVENTS or VIDEO_EVENTS+len(pointers)>0x40a40000:raise ValueError('video menu capacity')
    put(VIDEO_ROWS-BASE,rows,'shared final video menu rows',SHARED_VIDEO,expect_zero=True)
    put(VIDEO_EVENTS-BASE,pointers,'shared final video menu transition pointers',SHARED_VIDEO,expect_zero=True)
    put(0x4bcc2b0+0x10,struct.pack('<QQH',VIDEO_ROWS,VIDEO_EVENTS,len(order)),'shared video menu page: final rows and count',SHARED_VIDEO,expect=struct.pack('<QQH',BASE+0x3aa4758,BASE+0x3aa4ee8,44))
    for enabled,site,item in ((mask&BITS['log_colour'],0x40572940,0x2e38),(mask&BITS['prores'],0x4057295c,0x311b),(mask&BITS['framing'],0x40572978,0x310f)):
        if enabled:put(site-BASE,struct.pack('<I',0x52800002),'show selected menu item '+hex(item),SHARED_VIDEO,expect=bytes.fromhex('e2030032'))
    code,entries=shared_video_code()
    put(VIDEO_CODE-BASE,code,'shared five-component bank and refresh code',SHARED_VIDEO,expect_zero=True)
    callback_values=[callbacks.get(k,0)for k in ('prores','h265','anamorphic','log')]
    put(VIDEO_CONFIG-BASE,struct.pack('<II4Q',mask&15,0,*callback_values),'shared five-component mask and row callbacks',SHARED_VIDEO,expect_zero=True)
    put(0x4bf3a80,struct.pack('<Q',entries['shared_refresh']),'shared video refresh dispatcher',SHARED_VIDEO,expect=struct.pack('<Q',0x40572b2c))
    put(0x410162ec-BASE,branch(0x410162ec,entries['shared_bank']),'shared menu-bank copy dispatcher',SHARED_VIDEO,expect=bytes.fromhex('ea2340a9'))
    return dict(items=[hex(i)for i in order],count=len(order),mask=mask&15,callbacks={k:hex(v)for k,v in callbacks.items()},entries={k:hex(v)for k,v in entries.items()})


def compose_recipe(stock,mask):
    """Return every changed byte with one explicit component/shared owner."""
    selected(mask);check_main(stock);checked_upstream()
    from z8prores.menu import Image
    from z8patch.shared import Strings,VideoMenu,loader
    img=Image(stock+binascii.crc_hqx(stock,0).to_bytes(2,'big'));strings=Strings();menu=VideoMenu(img.old)
    owners={};detail={};callbacks={}
    def put(offset,data,purpose,owner,**options):
        img.put(offset,data,purpose,**options);owners[offset]=owner
    def merge(owner,edits,refinements=()):
        for e in edits:
            if menu.consumed(e):continue
            overlaps=[x for x in img.edits if e['offset']<x['end']and x['offset']<e['end']]
            if overlaps:
                if BASE+e['offset']not in refinements or len(overlaps)!=1:raise ValueError('unregistered native component overlap')
                previous=overlaps[0]
                if not previous['offset']<=e['offset']<=e['end']<=previous['end']or bytes(img.new[e['offset']:e['end']])!=e['old']:raise ValueError('native refinement base differs')
                at=e['offset']-previous['offset'];data=bytearray(previous['new']);data[at:at+len(e['new'])]=e['new'];previous['new']=bytes(data);previous['purpose']+='; '+e['purpose'];img.new[e['offset']:e['end']]=e['new']
            else:put(e['offset'],e['new'],e['purpose'],owner,expect=e['old'])
    if mask&BITS['framing']:
        from z8opengate.patcher import build_main
        code,entries=_unit('opengate');part,detail['opengate']=build_main(img.old,code,entries,strings);merge('framing',part.edits)
        from z8anamorphic.patcher import build_main
        code,entries=_unit('anamorphic');part,detail['anamorphic']=build_main(bytes(part.new),code,entries,strings);menu.collect(part,0x310f);merge('framing',part.edits,(0x40960c98,0x40962b58,0x409616a4,0x407138d0));callbacks['anamorphic']=entries['ap_menu_refresh_hook']
    star,star_entries=_unit('quality');prores_ok=h265_ok=0
    if mask&BITS['prores']:
        from z8prores.menu import build_main
        part,detail['prores']=build_main(img.old,strings,star_entries['tier_hq_ok']);menu.collect(part,0x311b);merge('prores',part.edits);callbacks['prores']=int(detail['prores']['entries']['refresh'],16);prores_ok=int(detail['prores']['entries']['ok'],16)
    if mask&BITS['h265']:
        from z8h265.patcher import build_main
        code,entries=_unit('h265');part,detail['h265']=build_main(img.old,code,entries,strings,star_entries['tier_ok_star']);m=detail['h265']['menu'];menu.add(0x315a,bytes.fromhex(m['row_hex']),int(m['row_table'],16));merge('h265',part.edits);callbacks['h265']=entries['tier_refresh_hook'];h265_ok=entries['tier_ok_hook']
    if mask&(BITS['prores']|BITS['h265']):
        edits,detail['quality']=quality_part(img.old,bool(mask&BITS['prores']),bool(mask&BITS['h265']),prores_ok,h265_ok)
        for e in edits:
            address=BASE+e['offset'];owner='h265'if address in (0x440c7b58,0x440c7b60,0x40a20000)else'prores'if address==0x440c7b68 else SHARED_QUALITY
            merge(owner,[e])
    if mask&BITS['log_colour']:
        from z8log.patcher import build_main
        code,entries=_unit('log');part,detail['log_colour']=build_main(img.old,code,entries,strings);menu.collect(part,0x2e38);merge('log_colour',part.edits);callbacks['log']=entries['lg_menu_refresh_hook']
    if mask&BITS['photo_tools']:
        from z8precapture.patcher import edits
        from z8patch.compiled import load
        code,metadata=load('precapture','prebuilt','clang')
        for e in edits(code,metadata['symbols']):put(e['offset'],e['new'],e['purpose'],'photo_tools',expect=img.old[e['offset']:e['offset']+len(e['new'])])
        from z8pixelshift.patcher import build_main
        code,metadata=load('pixelshift','prebuilt','clang');af,_=load('pixelshift-af','prebuilt','clang');part,detail['pixelshift']=build_main(img.old,code,metadata['symbols'],af,strings);merge('photo_tools',part.edits)
    first=len(img.edits);detail['strings']=strings.emit(img)
    for e in img.edits[first:]:owners[e['offset']]=SHARED_LOCALE
    detail['menu']=_emit_video(img,menu,mask,callbacks,put)
    legacy=[]
    if mask&BITS['framing']:legacy.append('framing')
    if mask&(BITS['prores']|BITS['h265']):legacy.append('video-quality')
    if mask&BITS['log_colour']:legacy.append('log-colour')
    if mask&BITS['photo_tools']:legacy.append('photo-tools')
    first=len(img.edits);detail['loader']=loader(img,legacy)
    for e in img.edits[first:]:owners[e['offset']]='foundation.reserve-only'
    windows=[];end=0;rebuilt=bytearray(stock)
    for e in sorted(img.edits,key=lambda e:e['offset']):
        at=e['offset'];data=bytes(e['new']);old=bytes(e['old'])
        if at<end or stock[at:at+len(data)]!=old:raise ValueError('overlap/stock preimage differs')
        owner=owners.get(at)
        if owner is None:raise ValueError('native changed window has no owner')
        windows.append(OwnedWindow(at,old,data,owner,e['purpose']));rebuilt[at:at+len(data)]=data;end=at+len(data)
    image=bytes(img.new[:-2])
    if bytes(rebuilt)!=image:raise ValueError('recipe does not cover every changed MAIN byte')
    detail.update(compiled_metadata_sha256=COMPILED_SHA256,codec_independence=True,shared_bank_bits=BITS,unselected_backend_images_absent=True)
    return NativeRecipe(mask,image,tuple(windows),detail)


def component_windows(recipe,owner):
    """Filter selected recipes for a component/shared-library package.

    The MOV close site is delegated to the one shared bridge; loader reserve
    is a foundation prerequisite and is never a runtime package record.
    """
    if owner not in (*COMPONENTS,SHARED_LOCALE,SHARED_VIDEO,SHARED_QUALITY):raise ValueError('unknown native owner')
    return tuple(w for w in recipe.windows if w.owner==owner and w.address not in (COPY_END_SITE,MOV_CLOSE))
