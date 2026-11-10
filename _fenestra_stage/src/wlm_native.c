/* SPDX-License-Identifier: GPL-3.0-only */
#include "fenestra_wlm_native.h"
#include "fenestra_memory.h"
#include <stddef.h>
typedef struct {const FxWlmNativeView *view;uint32_t bit,rank,variants,blocks,boff,selected;uint8_t hash[32];} Component;
typedef struct {FxCapsuleRecord r;const uint8_t *old,*new_data;} Record;
static uint32_t get32(const uint8_t *p){return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static uint64_t get64(const uint8_t *p){return get32(p)|(uint64_t)get32(p+4)<<32;}
static void put32(uint8_t *p,uint32_t n){for(uint32_t i=0u;i<4u;++i)p[i]=(uint8_t)(n>>(8u*i));}
static void put64(uint8_t *p,uint64_t n){for(uint32_t i=0u;i<8u;++i)p[i]=(uint8_t)(n>>(8u*i));}
static int zero(const uint8_t *p,uint32_t n){for(uint32_t i=0u;i<n;++i)if(p[i])return 0;return 1;}
static int span(uint32_t at,uint32_t n,uint32_t limit){return at<=limit && n<=limit-at;}
static int id_valid(const uint8_t p[16]){
 uint32_t i=0u;for(;i<16u&&p[i];++i){uint8_t c=p[i];if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='-'||c=='.'))return 0;}
 return i && i<16u && zero(p+i,16u-i);
}
static int hash_equal(const uint8_t *data,uint32_t bytes,const uint8_t expected[32]){uint8_t h[32];fx_sha256(data,bytes,h);return !memcmp(h,expected,32u);}
static FxResult component_check(Component *out,const FxWlmNativeView *view,const uint8_t target[32],uint64_t required){
 const uint8_t *p=view->data;uint32_t size=view->bytes;
 if(!p||size<FX_WLM_NATIVE_HEADER||size>FX_WLM_NATIVE_FILE_LIMIT||memcmp(p,"WLMNAT1\0",8u)||get32(p+8)!=1u||get32(p+12)!=FX_WLM_NATIVE_HEADER)return FX_INTEGRITY;
 uint32_t bit=get32(p+36),n=get32(p+44),blocks=get32(p+136),boff=get32(p+140);
 if(!id_valid(p+16)||memcmp(p+16,view->id,16u)||!view->version||get32(p+32)!=view->version||
  (bit&&(bit&(bit-1u)))||get32(p+60)!=(uint32_t)!bit||get32(p+56)!=size||!n||n>FX_WLM_NATIVE_VARIANTS||
  get32(p+48)!=FX_WLM_NATIVE_HEADER||get32(p+52)!=FX_WLM_NATIVE_VARIANT||
  boff!=FX_WLM_NATIVE_HEADER+n*FX_WLM_NATIVE_VARIANT||get32(p+144)!=FX_WLM_NATIVE_BLOCK||!blocks||blocks>FX_WLM_NATIVE_BLOCKS||
  !span(boff,blocks*FX_WLM_NATIVE_BLOCK,size)||!zero(p+148,12u)||get64(p+64)!=required||memcmp(p+72,target,32u)||!hash_equal(p+FX_WLM_NATIVE_HEADER,size-FX_WLM_NATIVE_HEADER,p+104))return FX_INTEGRITY;
 uint32_t cursor=boff+blocks*FX_WLM_NATIVE_BLOCK;
 for(uint32_t i=0u;i<n;++i){const uint8_t *v=p+FX_WLM_NATIVE_HEADER+i*FX_WLM_NATIVE_VARIANT;uint32_t mask=get32(v),value=get32(v+4),at=get32(v+12),bytes=get32(v+16),raw=get32(v+20);
  if((value&~mask)||get32(v+8)!=2u||at!=cursor||!span(at,bytes,size)||bytes<4u||raw<FX_CAPSULE_HEADER_BYTES||raw>FX_CAPSULE_MAX_PACKAGE||!zero(v+56,8u))return FX_INTEGRITY;
  uint32_t refs=get32(p+at);if(!refs||refs>65536u||bytes!=4u+refs*4u)return FX_BOUNDS;
  uint64_t total=0u;for(uint32_t j=0u;j<refs;++j){uint32_t index=get32(p+at+4u+j*4u);if(index>=blocks)return FX_BOUNDS;total+=get32(p+boff+index*FX_WLM_NATIVE_BLOCK);}
  if(total!=raw)return FX_BOUNDS;
  for(uint32_t j=0u;j<i;++j){const uint8_t *other=p+FX_WLM_NATIVE_HEADER+j*FX_WLM_NATIVE_VARIANT;if(!((value^get32(other+4))&mask&get32(other)))return FX_STATE;}
  cursor+=bytes;
 }
 for(uint32_t i=0u;i<blocks;++i){const uint8_t *b=p+boff+i*FX_WLM_NATIVE_BLOCK;uint32_t raw=get32(b),at=get32(b+4),bytes=get32(b+8),codec=get32(b+12);
  if(!raw||raw>FX_WLM_NATIVE_BLOCK_LIMIT||!bytes||at!=cursor||!span(at,bytes,size)||codec>1u||(!codec&&bytes!=raw))return FX_INTEGRITY;
  cursor+=bytes;
 }
 if(cursor!=size)return FX_INTEGRITY;
 memset(out,0,sizeof(*out));out->view=view;out->bit=bit;out->rank=get32(p+40);out->variants=n;out->blocks=blocks;out->boff=boff;fx_sha256(p,size,out->hash);return FX_OK;
}
static FxResult unpack(uint8_t *out,uint32_t bytes,const uint8_t *in,uint32_t size){
 uint32_t at=0u,done=0u;
 while(done<bytes){if(at==size)return FX_BOUNDS;uint32_t flags=in[at++];
  for(uint32_t bit=0u;bit<8u;++bit){if(done==bytes){if(flags>>bit)return FX_INTEGRITY;break;}
   if(flags&(1u<<bit)){if(at==size)return FX_BOUNDS;out[done++]=in[at++];}
   else{if(size-at<2u)return FX_BOUNDS;uint32_t r=(uint32_t)in[at]|(uint32_t)in[at+1u]<<8;at+=2u;uint32_t distance=(r&4095u)+1u,n=(r>>12)+3u;
    if(distance>done||n>bytes-done)return FX_BOUNDS;for(uint32_t i=0u;i<n;++i){out[done]=out[done-distance];++done;}}
  }
 }
 return at==size?FX_OK:FX_INTEGRITY;
}
static FxResult decode_selected(const Component *c,uint8_t *out,uint32_t bytes){
 const uint8_t *p=c->view->data,*v=p+FX_WLM_NATIVE_HEADER+c->selected*FX_WLM_NATIVE_VARIANT;uint32_t at=get32(v+12),refs=get32(p+at),done=0u;
 for(uint32_t i=0u;i<refs;++i){uint32_t index=get32(p+at+4u+i*4u);const uint8_t *b=p+c->boff+index*FX_WLM_NATIVE_BLOCK;uint32_t raw=get32(b),offset=get32(b+4),packed=get32(b+8);
  if(raw>bytes-done)return FX_BOUNDS;FxResult r=FX_OK;if(get32(b+12))r=unpack(out+done,raw,p+offset,packed);else memcpy(out+done,p+offset,raw);if(r!=FX_OK)return r;done+=raw;
 }
 return done==bytes&&hash_equal(out,bytes,v+24)?FX_OK:FX_INTEGRITY;
}
static int overlaps(uint64_t a,uint32_t an,uint64_t b,uint32_t bn){return a<b+bn&&b<a+an;}
static FxResult append_capsule(const uint8_t *p,uint32_t size,const uint8_t target[32],uint64_t required,Record *records,uint32_t *count,uint64_t *entry){
 if(size<128u||memcmp(p,"FXCAPS1\0",8u)||get32(p+8)!=1u||get32(p+12)!=128u||get32(p+16)!=size||get32(p+24)!=128u||get32(p+28)||memcmp(p+32,target,32u)||get64(p+104)!=required||!get64(p+96)||!hash_equal(p+128,size-128u,p+64))return FX_INTEGRITY;
 uint32_t n=get32(p+20);if(!n||n>FX_CAPSULE_MAX_RECORDS||!span(128u,n*112u,size))return FX_BOUNDS;
 uint32_t cursor=128u+n*112u;uint64_t total=0u,entered=get64(p+120);uint32_t entry_valid=!entered,seen_hook=0u;
 for(uint32_t i=0u;i<n;++i){const uint8_t *r=p+128u+i*112u;Record item;memset(&item,0,sizeof(item));
  item.r.kind=get32(r);item.r.flags=get32(r+4);item.r.address=get64(r+8);item.r.bytes=get32(r+16);item.r.new_offset=get32(r+20);item.r.old_offset=get32(r+24);item.r.depends_on=get32(r+28);
  uint32_t bytes=item.r.bytes;
  if(!bytes||bytes>FX_CAPSULE_MAX_RECORD||item.r.address>UINT64_MAX-bytes||!zero(r+96,16u)||item.r.new_offset!=cursor||!span(cursor,bytes,size))return FX_BOUNDS;
  cursor+=bytes;if(item.r.old_offset!=cursor||!span(cursor,bytes,size))return FX_BOUNDS;cursor+=bytes;
  if(item.r.kind==FX_CAPSULE_IMAGE){if(seen_hook||item.r.flags&~3u||(item.r.flags&3u)==3u||item.r.depends_on!=FX_CAPSULE_NO_DEPENDENCY)return FX_CAPABILITY;
   if((item.r.flags&FX_CAPSULE_EXECUTE)&&entered>=item.r.address&&entered-item.r.address<=bytes && bytes-(entered-item.r.address)>=4u)entry_valid=1u;
  }else if(item.r.kind==FX_CAPSULE_HOOK){seen_hook=1u;if(item.r.flags!=FX_CAPSULE_INSTRUCTION&&item.r.flags!=FX_CAPSULE_POINTER&&item.r.flags!=FX_CAPSULE_DATA)return FX_CAPABILITY;
   if(item.r.depends_on!=FX_CAPSULE_NO_DEPENDENCY&&(item.r.depends_on>=i||get32(p+128u+item.r.depends_on*112u)!=FX_CAPSULE_IMAGE))return FX_BOUNDS;
  }else return FX_CAPABILITY;
  if(item.r.kind==FX_CAPSULE_HOOK && ((item.r.flags==FX_CAPSULE_INSTRUCTION && ((item.r.address&3u)||(bytes&3u))) || (item.r.flags==FX_CAPSULE_POINTER && ((item.r.address&7u)||(bytes&7u)))))return FX_INTEGRITY;
  for(uint32_t j=0u;j<i;++j){const uint8_t *other=p+128u+j*112u;if(overlaps(item.r.address,bytes,get64(other+8),get32(other+16)))return FX_STATE;}
  item.old=p+item.r.old_offset;item.new_data=p+item.r.new_offset;if(!hash_equal(item.old,bytes,r+32)||!hash_equal(item.new_data,bytes,r+64))return FX_INTEGRITY;
  total+=bytes;if(total>FX_CAPSULE_MAX_WRITE)return FX_BUDGET;
  uint32_t duplicate=0u;
  for(uint32_t j=0u;j<*count;++j){Record *q=&records[j];if(!overlaps(item.r.address,bytes,q->r.address,q->r.bytes))continue;
   if(item.r.kind==q->r.kind&&item.r.flags==q->r.flags&&item.r.address==q->r.address&&bytes==q->r.bytes&&!memcmp(item.old,q->old,bytes)&&!memcmp(item.new_data,q->new_data,bytes)){duplicate=1u;break;}
   return FX_STATE;
  }
  if(!duplicate){if(*count==FX_CAPSULE_MAX_RECORDS)return FX_BUDGET;item.r.depends_on=FX_CAPSULE_NO_DEPENDENCY;records[(*count)++]=item;}
 }
 if(cursor!=size||total!=get64(p+112)||!entry_valid||(entered&3u))return FX_INTEGRITY;*entry=entered;return FX_OK;
}
static int before(const Record *a,const Record *b){return a->r.kind<b->r.kind||(a->r.kind==b->r.kind&&a->r.address<b->r.address);}
FxResult fx_wlm_native_compose(const FxWlmNativeView *views,uint32_t n,const uint8_t target[32],uint64_t required,FxWlmNativeMemory memory,FxWlmNativePlan *out){
 if(!out)return FX_ARGUMENT;memset(out,0,sizeof(*out));if(!target||!memory.allocate||!memory.release||n>FX_WLM_NATIVE_COMPONENTS||(n&&!views))return FX_ARGUMENT;
 if(!n)return FX_OK;
 Component components[FX_WLM_NATIVE_COMPONENTS];uint8_t *raw[FX_WLM_NATIVE_COMPONENTS]={0};uint32_t used=0u,selection=0u;FxResult result=FX_OK;Record *records=0;uint8_t *package=0;
 for(uint32_t i=0u;i<n;++i){Component item;result=component_check(&item,&views[i],target,required);if(result!=FX_OK)goto done;uint32_t duplicate=0u;
  for(uint32_t j=0u;j<used;++j)if(!memcmp(components[j].view->id,views[i].id,16u)){if(components[j].view->version!=views[i].version||memcmp(components[j].hash,item.hash,32u)){result=FX_STATE;goto done;}duplicate=1u;break;}
  if(duplicate)continue;if(selection&item.bit){result=FX_STATE;goto done;}selection|=item.bit;components[used++]=item;
 }
 for(uint32_t i=1u;i<used;++i){Component item=components[i];uint32_t j=i;while(j&&(components[j-1u].rank>item.rank||(components[j-1u].rank==item.rank&&memcmp(components[j-1u].view->id,item.view->id,16u)>0))){components[j]=components[j-1u];--j;}components[j]=item;}
 uint64_t raw_total=0u;
 for(uint32_t i=0u;i<used;++i){Component *c=&components[i];uint32_t matches=0u;for(uint32_t j=0u;j<c->variants;++j){const uint8_t *v=c->view->data+160u+j*64u;if((selection&get32(v))==get32(v+4)){c->selected=j;++matches;}}
  if(matches!=1u){result=FX_CAPABILITY;goto done;}raw_total+=get32(c->view->data+160u+c->selected*64u+20u);if(raw_total>32u*1024u*1024u){result=FX_BUDGET;goto done;}}
 records=memory.allocate(memory.context,FX_CAPSULE_MAX_RECORDS*(uint32_t)sizeof(*records));if(!records){result=FX_BUDGET;goto done;}uint32_t count=0u;
 for(uint32_t i=0u;i<used;++i){const Component *c=&components[i];uint32_t bytes=get32(c->view->data+160u+c->selected*64u+20u);raw[i]=memory.allocate(memory.context,bytes);if(!raw[i]){result=FX_BUDGET;goto done;}
  result=decode_selected(c,raw[i],bytes);if(result!=FX_OK)goto done;uint64_t entry=0u;result=append_capsule(raw[i],bytes,target,required,records,&count,&entry);if(result!=FX_OK)goto done;
  if(entry){for(uint32_t j=0u;j<out->entry_count;++j)if(out->entries[j]==entry){result=FX_STATE;goto done;}out->entries[out->entry_count++]=entry;}
 }
 if(!count){result=FX_EMPTY;goto done;}
 for(uint32_t i=1u;i<count;++i){Record item=records[i];uint32_t j=i;while(j&&before(&item,&records[j-1u])){records[j]=records[j-1u];--j;}records[j]=item;}
 uint64_t write_bytes=0u;
 for(uint32_t i=0u;i<count;++i){Record *r=&records[i];write_bytes+=r->r.bytes;uint64_t address=0u;uint32_t have=0u;
  if(r->r.kind==FX_CAPSULE_HOOK&&r->r.flags==FX_CAPSULE_INSTRUCTION&&r->r.bytes==4u){uint32_t word=get32(r->new_data);if((word&0x7c000000u)==0x14000000u){int64_t relative=(int64_t)(word&0x03ffffffu);if(relative&0x02000000)relative-=0x04000000;if(relative<0){uint64_t delta=(uint64_t)(-relative)*4u;if(r->r.address<delta){result=FX_BOUNDS;goto done;}address=r->r.address-delta;}else{uint64_t delta=(uint64_t)relative*4u;if(r->r.address>UINT64_MAX-delta){result=FX_BOUNDS;goto done;}address=r->r.address+delta;}have=1u;}}
  else if(r->r.kind==FX_CAPSULE_HOOK&&r->r.flags==FX_CAPSULE_POINTER&&r->r.bytes==8u){address=get64(r->new_data);have=1u;}
  if(have)for(uint32_t j=0u;j<i;++j)if(records[j].r.kind==FX_CAPSULE_IMAGE&&address>=records[j].r.address&&address-records[j].r.address<records[j].r.bytes){r->r.depends_on=j;break;}
 }
 uint64_t total=128u+(uint64_t)count*112u+write_bytes*2u;if(write_bytes>FX_CAPSULE_MAX_WRITE||total>FX_CAPSULE_MAX_PACKAGE){result=FX_BUDGET;goto done;}
 package=memory.allocate(memory.context,(uint32_t)total);if(!package){result=FX_BUDGET;goto done;}memset(package,0,(size_t)total);memcpy(package,"FXCAPS1\0",8u);put32(package+8,1u);put32(package+12,128u);put32(package+16,(uint32_t)total);put32(package+20,count);put32(package+24,128u);memcpy(package+32,target,32u);put64(package+104,required);put64(package+112,write_bytes);put64(package+120,out->entry_count?out->entries[0]:0u);
 uint32_t cursor=128u+count*112u;
 for(uint32_t i=0u;i<count;++i){Record *r=&records[i];uint8_t *q=package+128u+i*112u;put32(q,r->r.kind);put32(q+4,r->r.flags);put64(q+8,r->r.address);put32(q+16,r->r.bytes);put32(q+20,cursor);memcpy(package+cursor,r->new_data,r->r.bytes);cursor+=r->r.bytes;put32(q+24,cursor);memcpy(package+cursor,r->old,r->r.bytes);cursor+=r->r.bytes;put32(q+28,r->r.depends_on);fx_sha256(r->old,r->r.bytes,q+32);fx_sha256(r->new_data,r->r.bytes,q+64);}
 fx_sha256(package+128,(uint32_t)total-128u,package+64);
 uint32_t sorted[FX_WLM_NATIVE_COMPONENTS];for(uint32_t i=0u;i<used;++i){sorted[i]=i;uint32_t j=i;while(j&&memcmp(components[sorted[j-1u]].view->id,components[sorted[j]].view->id,16u)>0){uint32_t swap=sorted[j-1u];sorted[j-1u]=sorted[j];sorted[j]=swap;--j;}}
 uint8_t fingerprints[FX_WLM_NATIVE_COMPONENTS*32u],identity[32];for(uint32_t i=0u;i<used;++i)memcpy(fingerprints+i*32u,components[sorted[i]].hash,32u);fx_sha256(fingerprints,used*32u,identity);uint64_t package_id=get64(identity);put64(package+96,package_id?package_id:1u);
 out->package=package;out->bytes=(uint32_t)total;out->selection=selection;out->component_count=used;fx_sha256(package,(uint32_t)total,out->sha256);package=0;
 done:
 if(package)memory.release(memory.context,package);if(records)memory.release(memory.context,records);for(uint32_t i=0u;i<FX_WLM_NATIVE_COMPONENTS;++i)if(raw[i])memory.release(memory.context,raw[i]);
 if(result!=FX_OK)memset(out,0,sizeof(*out));return result;
}
