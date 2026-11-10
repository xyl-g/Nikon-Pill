/* SPDX-License-Identifier: GPL-3.0-only */
#include "fenestra_wlm_bundle.h"
#include "fenestra_memory.h"
#include "fenestra_sha256.h"
static uint32_t get32(const uint8_t *p){return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;}
static int span(uint32_t a,uint32_t n,uint32_t limit){return a<=limit && n<=limit-a;}
static int overlaps(uint32_t a,uint32_t n,uint32_t b,uint32_t m){return (uint64_t)a<(uint64_t)b+m && (uint64_t)b<(uint64_t)a+n;}
static int zero(const uint8_t *p,uint32_t n){for(uint32_t i=0u;i<n;++i)if(p[i])return 0;return 1;}
static int component_name(const char id[16]){uint32_t i=0u;for(;i<16u && id[i];++i){uint8_t c=(uint8_t)id[i];if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_'||c=='-'||c=='.'))return 0;}return i && i<16u && zero((const uint8_t *)id+i,16u-i); }
FxResult fx_wlm_bundle_inspect(const uint8_t *p,uint32_t bytes,const char id[16],const uint8_t stock[32],uint64_t caps,FxWlmBundle *out){
    if(!out)return FX_ARGUMENT;memset(out,0,sizeof(*out));
    if(!p || !stock || !id || !fx_service_name(id,16u,0) || bytes<FX_WLM_BUNDLE_HEADER || bytes>FX_WLM_BUNDLE_LIMIT)return FX_ARGUMENT;
    if(memcmp(p,"FENESTRA",8u) || get32(p+8u)!=4u || get32(p+12u)!=FX_WLM_BUNDLE_HEADER || get32(p+88u)!=bytes || get32(p+92u) || !zero(p+168u,24u))return FX_BOUNDS;
    if(get32(p+16u)!=1u || get32(p+20u)!=FX_ISA_AARCH64 || memcmp(p+24u,stock,32u) || get32(p+148u)!=FX_WLM_NATIVE_ABI)return FX_CAPABILITY;
    if(!fx_service_name((const char *)(p+152u),16u,0) || memcmp(p+152u,id,16u))return FX_INTEGRITY;
    FxWlmBundle q;memset(&q,0,sizeof(q));uint8_t hash[32];
    fx_sha256(p+FX_WLM_BUNDLE_HEADER,bytes-FX_WLM_BUNDLE_HEADER,hash);if(memcmp(hash,p+56u,32u))return FX_INTEGRITY;
    memcpy(q.id,id,16u);q.package_bytes=bytes;q.frontend_offset=get32(p+96u);q.frontend_bytes=get32(p+100u);q.component_count=get32(p+140u);q.native_abi=get32(p+148u);
    uint32_t table=get32(p+136u),record=get32(p+144u);
    if(q.component_count>FX_WLM_COMPONENTS || record!=FX_WLM_COMPONENT_BYTES || table!=FX_WLM_BUNDLE_HEADER || !span(table,q.component_count*record,bytes) || q.frontend_offset!=table+q.component_count*record || q.frontend_bytes<=FX_MODULE_HEADER || q.frontend_bytes>FX_NATIVE_MODULE_PACKAGE_LIMIT || !span(q.frontend_offset,q.frontend_bytes,bytes) || overlaps(q.frontend_offset,q.frontend_bytes,table,q.component_count*record))return FX_BOUNDS;
    memcpy(q.frontend_sha256,p+104u,32u);fx_sha256(p+q.frontend_offset,q.frontend_bytes,hash);if(memcmp(hash,q.frontend_sha256,32u))return FX_INTEGRITY;
    /* The ordinary module parser rejects recursive v4 and non-service entries. */
    uint32_t front_version=get32(p+q.frontend_offset+8u);
    if((front_version!=2u && front_version!=3u) || fx_module_header_entry(p+q.frontend_offset,q.frontend_bytes)!=FX_MODULE_ENTRY_SERVICES)return FX_CAPABILITY;
    FxResult r=fx_module_inspect(p+q.frontend_offset,q.frontend_bytes,stock,caps,FX_NATIVE_MODULE_IMAGE_LIMIT,&q.frontend);if(r!=FX_OK)return r;
    uint32_t cursor=q.frontend_offset+q.frontend_bytes;
    for(uint32_t i=0u;i<q.component_count;++i){
        const uint8_t *at=p+table+i*record;FxWlmComponent *v=&q.components[i];
        memcpy(v->id,at,16u);v->version=get32(at+16u);v->flags=get32(at+20u);v->offset=get32(at+24u);v->bytes=get32(at+28u);memcpy(v->sha256,at+32u,32u);
        if(!component_name(v->id) || !v->version || v->flags>1u || v->bytes<FX_WLM_NATIVE_HEADER || v->offset!=cursor || !span(v->offset,v->bytes,bytes) || overlaps(v->offset,v->bytes,table,q.component_count*record) || overlaps(v->offset,v->bytes,q.frontend_offset,q.frontend_bytes))return FX_BOUNDS;
        for(uint32_t j=0u;j<i;++j)if(!memcmp(v->id,q.components[j].id,16u) || overlaps(v->offset,v->bytes,q.components[j].offset,q.components[j].bytes))return FX_INTEGRITY;
        fx_sha256(p+v->offset,v->bytes,hash);if(memcmp(hash,v->sha256,32u))return FX_INTEGRITY;
        const uint8_t *native=p+v->offset;
        if(memcmp(native,"WLMNAT1\0",8u) || get32(native+8u)!=1u || get32(native+12u)!=FX_WLM_NATIVE_HEADER || memcmp(native+16u,v->id,16u) || get32(native+32u)!=v->version || get32(native+56u)!=v->bytes || get32(native+60u)!=v->flags || v->flags!=(uint32_t)!get32(native+36u))return FX_INTEGRITY;
        cursor+=v->bytes;
    }
    if(cursor!=bytes)return FX_BOUNDS;
    fx_sha256(p,bytes,q.package_sha256);*out=q;return FX_OK;
}

/* The manager runs on an existing Nikon GUI task. Never allocate an outer
 * multi-MiB WLM merely to check it; all native bytes are hashed sequentially. */
#define FX_WLM_STREAM_CHUNK 32768u
#define FX_WLM_STREAM_CALLS (FX_READ_CALL_LIMIT+FX_WLM_BUNDLE_LIMIT/4096u+FX_WLM_COMPONENTS*4u)
typedef struct {
    const FxWlmStreamIO *io;
    uint32_t position,calls;
    FxSha256 full,body;
    FxWlmBundle bundle;
    uint8_t header[FX_WLM_BUNDLE_HEADER];
    uint8_t body_sha256[32];
} FxWlmStreamCheck;
static FxResult stream_read(FxWlmStreamCheck *s,uint8_t *out,uint32_t bytes,int body) {
    while(bytes) {
        uint32_t room=bytes>FX_WLM_STREAM_CHUNK?FX_WLM_STREAM_CHUNK:bytes,done=0u;
        if(++s->calls>FX_WLM_STREAM_CALLS)return FX_IO;
        FxResult r=s->io->read(s->io->context,out,room,&done);
        if(r!=FX_OK)return r;
        if(!done || done>room)return FX_INTEGRITY;
        if(fx_sha256_update(&s->full,out,done)!=FX_OK ||
           (body && fx_sha256_update(&s->body,out,done)!=FX_OK))return FX_BOUNDS;
        s->position+=done;out+=done;bytes-=done;
    }
    return FX_OK;
}
static FxResult stream_eof(FxWlmStreamCheck *s) {
    uint8_t extra=0u;uint32_t done=0u;
    if(++s->calls>FX_WLM_STREAM_CALLS)return FX_IO;
    FxResult r=s->io->read(s->io->context,&extra,1u,&done);
    return r!=FX_OK?r:done?FX_INTEGRITY:FX_OK;
}
static FxResult stream_header(FxWlmStreamCheck *s,uint32_t bytes,const char id[16],const uint8_t stock[32]) {
    const uint8_t *p=s->header;FxWlmBundle *q=&s->bundle;
    if(memcmp(p,"FENESTRA",8u) || get32(p+8u)!=4u || get32(p+12u)!=FX_WLM_BUNDLE_HEADER ||
       get32(p+88u)!=bytes || get32(p+92u) || !zero(p+168u,24u))return FX_BOUNDS;
    if(get32(p+16u)!=1u || get32(p+20u)!=FX_ISA_AARCH64 || memcmp(p+24u,stock,32u) ||
       get32(p+148u)!=FX_WLM_NATIVE_ABI)return FX_CAPABILITY;
    if(!fx_service_name((const char *)(p+152u),16u,0) || memcmp(p+152u,id,16u))return FX_INTEGRITY;
    memcpy(q->id,id,16u);q->package_bytes=bytes;q->frontend_offset=get32(p+96u);
    q->frontend_bytes=get32(p+100u);q->component_count=get32(p+140u);q->native_abi=get32(p+148u);
    uint32_t table=get32(p+136u),record=get32(p+144u);
    if(q->component_count>FX_WLM_COMPONENTS || record!=FX_WLM_COMPONENT_BYTES || table!=FX_WLM_BUNDLE_HEADER ||
       !span(table,q->component_count*record,bytes) || q->frontend_offset!=table+q->component_count*record ||
       q->frontend_bytes<=FX_MODULE_HEADER || q->frontend_bytes>FX_NATIVE_MODULE_PACKAGE_LIMIT ||
       !span(q->frontend_offset,q->frontend_bytes,bytes) ||
       overlaps(q->frontend_offset,q->frontend_bytes,table,q->component_count*record))return FX_BOUNDS;
    memcpy(q->frontend_sha256,p+104u,32u);memcpy(s->body_sha256,p+56u,32u);return FX_OK;
}
static FxResult stream_bundle(FxWlmStreamCheck *s,uint32_t bytes,const char id[16],const uint8_t stock[32],
        uint64_t caps,FxIndexMemory memory,uint8_t *scratch,uint32_t capacity,FxAllocationObserver observer) {
    FxResult r=stream_read(s,s->header+FX_MODULE_HEADER,FX_WLM_BUNDLE_HEADER-FX_MODULE_HEADER,0);
    if(r!=FX_OK)return r;
    r=stream_header(s,bytes,id,stock);if(r!=FX_OK)return r;
    FxWlmBundle *q=&s->bundle;
    /* Decode records from a small serialized buffer, never host-endian casts. */
    for(uint32_t i=0u;i<q->component_count;++i) {
        r=stream_read(s,s->header,FX_WLM_COMPONENT_BYTES,1);if(r!=FX_OK)return r;
        const uint8_t *p=s->header;FxWlmComponent *v=&q->components[i];
        memcpy(v->id,p,16u);v->version=get32(p+16u);v->flags=get32(p+20u);
        v->offset=get32(p+24u);v->bytes=get32(p+28u);memcpy(v->sha256,p+32u,32u);
        if(!component_name(v->id) || !v->version || v->flags>1u || v->bytes<FX_WLM_NATIVE_HEADER)return FX_BOUNDS;
        for(uint32_t j=0u;j<i;++j)if(!memcmp(v->id,q->components[j].id,16u) ||
            overlaps(v->offset,v->bytes,q->components[j].offset,q->components[j].bytes))return FX_INTEGRITY;
    }
    uint8_t *front=scratch;int temporary=q->frontend_bytes>capacity;
    if(temporary) {
        front=memory.allocate(memory.context,q->frontend_bytes);if(!front)return FX_BUDGET;
        fx_allocation_observe(observer,q->frontend_bytes,FX_ALLOC_REQUESTED);
    }
    r=stream_read(s,front,q->frontend_bytes,1);
    if(r==FX_OK) {
        uint8_t hash[32];fx_sha256(front,q->frontend_bytes,hash);
        if(memcmp(hash,q->frontend_sha256,32u))r=FX_INTEGRITY;
        else if((get32(front+8u)!=2u && get32(front+8u)!=3u) ||
                fx_module_header_entry(front,q->frontend_bytes)!=FX_MODULE_ENTRY_SERVICES)r=FX_CAPABILITY;
        else r=fx_module_inspect(front,q->frontend_bytes,stock,caps,FX_NATIVE_MODULE_IMAGE_LIMIT,&q->frontend);
    }
    if(temporary) {
        memory.release(memory.context,front);
        fx_allocation_observe(observer,q->frontend_bytes,FX_ALLOC_FREE_PENDING);
    }
    if(r!=FX_OK)return r;
    uint32_t cursor=q->frontend_offset+q->frontend_bytes;
    for(uint32_t i=0u;i<q->component_count;++i) {
        FxWlmComponent *v=&q->components[i];
        if(v->offset!=cursor || !span(v->offset,v->bytes,bytes) ||
           overlaps(v->offset,v->bytes,FX_WLM_BUNDLE_HEADER,q->component_count*FX_WLM_COMPONENT_BYTES) ||
           overlaps(v->offset,v->bytes,q->frontend_offset,q->frontend_bytes))return FX_BOUNDS;
        FxSha256 hash;fx_sha256_init(&hash);
        r=stream_read(s,s->header,FX_WLM_NATIVE_HEADER,1);if(r!=FX_OK)return r;
        if(fx_sha256_update(&hash,s->header,FX_WLM_NATIVE_HEADER)!=FX_OK)return FX_BOUNDS;
        const uint8_t *p=s->header;
        if(memcmp(p,"WLMNAT1\0",8u) || get32(p+8u)!=1u || get32(p+12u)!=FX_WLM_NATIVE_HEADER ||
           memcmp(p+16u,v->id,16u) || get32(p+32u)!=v->version || get32(p+56u)!=v->bytes ||
           get32(p+60u)!=v->flags || v->flags!=(uint32_t)!get32(p+36u))return FX_INTEGRITY;
        uint32_t left=v->bytes-FX_WLM_NATIVE_HEADER;
        while(left) {
            uint32_t chunk=left>capacity?capacity:left;if(chunk>FX_WLM_STREAM_CHUNK)chunk=FX_WLM_STREAM_CHUNK;
            r=stream_read(s,scratch,chunk,1);if(r!=FX_OK)return r;
            if(fx_sha256_update(&hash,scratch,chunk)!=FX_OK)return FX_BOUNDS;
            left-=chunk;
        }
        uint8_t digest[32];fx_sha256_final(&hash,digest);if(memcmp(digest,v->sha256,32u))return FX_INTEGRITY;
        cursor+=v->bytes;
    }
    if(cursor!=bytes || s->position!=bytes)return FX_BOUNDS;
    uint8_t digest[32];fx_sha256_final(&s->body,digest);
    return memcmp(digest,s->body_sha256,32u)?FX_INTEGRITY:stream_eof(s);
}
FxResult fx_wlm_package_inspect_stream(const FxWlmStreamIO *io,uint32_t bytes,const char id[16],
        const uint8_t stock[32],uint64_t caps,FxIndexMemory memory,uint8_t *scratch,uint32_t capacity,
        FxModule *out,uint8_t digest[32],FxAllocationObserver observer) {
    if(!io || !io->read || !id || !fx_service_name(id,16u,0) || !stock || !memory.allocate || !memory.release ||
       !scratch || capacity<4096u || !out || !digest)return FX_ARGUMENT;
    if(bytes<=FX_MODULE_HEADER || bytes>FX_WLM_BUNDLE_LIMIT)return FX_BOUNDS;
    memset(out,0,sizeof(*out));memset(digest,0,32u);
    FxWlmStreamCheck *s=memory.allocate(memory.context,(uint32_t)sizeof(*s));if(!s)return FX_BUDGET;
    fx_allocation_observe(observer,(uint32_t)sizeof(*s),FX_ALLOC_REQUESTED);
    memset(s,0,sizeof(*s));s->io=io;fx_sha256_init(&s->full);fx_sha256_init(&s->body);
    FxResult r=stream_read(s,s->header,FX_MODULE_HEADER,0);
    if(r==FX_OK && get32(s->header+8u)==FX_WLM_BUNDLE_VERSION) {
        if(bytes<FX_WLM_BUNDLE_HEADER)r=FX_BOUNDS;
        else r=stream_bundle(s,bytes,id,stock,caps,memory,scratch,capacity,observer);
    } else if(r==FX_OK) {
        if(bytes>FX_NATIVE_MODULE_PACKAGE_LIMIT)r=FX_BOUNDS;
        else {
            int temporary=bytes>capacity;uint8_t *package=scratch;
            if(temporary) {
                package=memory.allocate(memory.context,bytes);
                if(!package)r=FX_BUDGET;else fx_allocation_observe(observer,bytes,FX_ALLOC_REQUESTED);
            }
            if(r==FX_OK) {
                memcpy(package,s->header,FX_MODULE_HEADER);
                r=stream_read(s,package+FX_MODULE_HEADER,bytes-FX_MODULE_HEADER,0);
                if(r==FX_OK)r=stream_eof(s);
                if(r==FX_OK)r=fx_module_header_entry(package,bytes)!=FX_MODULE_ENTRY_SERVICES?FX_CAPABILITY:
                    fx_module_inspect(package,bytes,stock,caps,FX_NATIVE_MODULE_IMAGE_LIMIT,&s->bundle.frontend);
            }
            if(temporary && package) {
                memory.release(memory.context,package);fx_allocation_observe(observer,bytes,FX_ALLOC_FREE_PENDING);
            }
        }
    }
    if(r==FX_OK){*out=s->bundle.frontend;fx_sha256_final(&s->full,digest);}
    memory.release(memory.context,s);fx_allocation_observe(observer,(uint32_t)sizeof(*s),FX_ALLOC_FREE_PENDING);return r;
}

static int cancelled(const FxFrozenModules *s){return s->cancelled && s->cancelled(s->cancel_context);}
static void *reserve(FxFrozenModules *s,uint32_t bytes){
    uint64_t used=(uint64_t)s->bytes+s->inventory.index.bytes+s->packages.bytes;
    if(!bytes || used+bytes>FX_WLM_FROZEN_BUDGET)return 0;
    void *p=s->memory.allocate(s->memory.context,bytes);if(p)s->bytes+=bytes;return p;
}
FxResult fx_wlm_frozen_scan(FxFrozenModules *s,const FxModuleDirectory *io,const uint8_t stock[32],uint64_t caps,FxIndexMemory memory){
    if(!s || !io || !io->open || !io->next || !io->close || !io->read || !stock || !memory.allocate || !memory.release)return FX_ARGUMENT;
    FxResult r=FX_OK;
    if(!s->attempted){
        s->attempted=1u;s->memory=memory;
        r=fx_module_index_init(&s->inventory.index,memory,(uint32_t)sizeof(FxDiscoveredModule),FX_MODULE_INDEX_BUDGET);
        if(r==FX_OK)r=fx_module_index_init(&s->packages,memory,(uint32_t)sizeof(FxFrozenPackage),FX_MODULE_INDEX_BUDGET);
    } else if(s->error==(uint32_t)FX_INTERRUPTED && !s->prepared && memory.context==s->memory.context &&
              memory.allocate==s->memory.allocate && memory.release==s->memory.release){
        /* Continuation of a cancelled scan: every completed package record is
         * kept; the directory is enumerated again and those ids are skipped. */
        s->error=0u;s->retained=0u;
    } else return FX_STATE;
    FxModuleIndex seen={0};if(r==FX_OK)r=fx_module_index_init(&seen,memory,16u,FX_MODULE_INDEX_BUDGET);
    if(r!=FX_OK){s->error=(uint32_t)r;s->retained=1u;return r;}
    if(cancelled(s)){r=FX_INTERRUPTED;goto done;}
    r=io->open(io->context);if(r==FX_EMPTY){r=FX_OK;goto complete;}if(r!=FX_OK)goto done;
    uint32_t exhausted=0u;
    for(uint32_t n=0u;n<65536u;++n){
        if(cancelled(s)){r=FX_INTERRUPTED;break;}
        FxModuleDirectoryEntry item={0};r=io->next(io->context,&item);if(r==FX_EMPTY){r=FX_OK;exhausted=1u;break;}
        /* Card IO lost to the shutdown that cancelled us is an interruption. */
        if(r==FX_IO && cancelled(s))r=FX_INTERRUPTED;
        if(r!=FX_OK)break;
        if(item.attributes&0x18u || !item.name[0])continue;
        for(uint32_t i=0u;i<32u && item.name[i];++i)if(item.name[i]>='A' && item.name[i]<='Z')item.name[i]=(char)(item.name[i]+('a'-'A'));
        uint32_t len=0u;while(len<32u && item.name[len])++len;
        uint32_t disabled=len>9u && !memcmp(item.name+len-9u,".disabled",9u),end=len-(disabled?9u:0u);
        if(end<5u || memcmp(item.name+end-4u,".wlm",4u))continue;
        if(!fx_service_name(item.name,32u,1) || end-4u>=16u){r=FX_ARGUMENT;break;}
        char id[16]={0};memcpy(id,item.name,end-4u);uint32_t slot=0u;
        if(!fx_service_name(id,16u,0)){r=FX_ARGUMENT;break;}
        r=fx_module_index_add(&seen,id,&slot);if(r!=FX_OK)break;
        if(disabled)continue;
        if(fx_module_index_find(&s->packages,id,&slot)==FX_OK)continue;
        if(item.bytes<=FX_MODULE_HEADER || item.bytes>FX_WLM_BUNDLE_LIMIT){r=FX_BOUNDS;break;}
        uint32_t bytes=(uint32_t)item.bytes;uint8_t *package=reserve(s,bytes+1u);if(!package){r=FX_BUDGET;break;}
        uint32_t actual=0u;r=io->read(io->context,item.name,package,bytes+1u,&actual);
        if(r==FX_OK && actual!=bytes)r=FX_INTEGRITY;
        if((r==FX_OK || r==FX_IO) && cancelled(s))r=FX_INTERRUPTED;
        /* The interrupted read returned: give its outer back so a cancelled
         * snapshot owns nothing outside its records; a continuation reads
         * this file again. */
        if(r==FX_INTERRUPTED){memory.release(memory.context,package);s->bytes-=bytes+1u;}
        if(r!=FX_OK)break;
        FxWlmBundle *bundle=0;FxModule module;uint32_t front_offset=0u,front_bytes=bytes;
        if(get32(package+8u)==FX_WLM_BUNDLE_VERSION){
            bundle=reserve(s,(uint32_t)sizeof(*bundle));if(!bundle){r=FX_BUDGET;break;}
            r=fx_wlm_bundle_inspect(package,bytes,id,stock,caps,bundle);if(r!=FX_OK)break;
            for(uint32_t i=0u;i<bundle->component_count;++i){
                const FxWlmComponent *v=&bundle->components[i];uint32_t j=0u;
                for(;j<s->native_count;++j)if(!memcmp(s->native_views[j].id,v->id,16u))break;
                if(j<s->native_count){if(s->native_views[j].version!=v->version || s->native_views[j].bytes!=v->bytes || memcmp(s->native_sha256[j],v->sha256,32u)){r=FX_INTEGRITY;break;}}
                else {if(j==FX_WLM_COMPONENTS){r=FX_BUDGET;break;}uint8_t *owned=reserve(s,v->bytes);if(!owned){r=FX_BUDGET;break;}memcpy(owned,package+v->offset,v->bytes);FxWlmNativeView *view=&s->native_views[j];view->data=owned;view->bytes=v->bytes;memcpy(view->id,v->id,16u);view->version=v->version;memcpy(s->native_sha256[j],v->sha256,32u);++s->native_count;}
            }
            if(r!=FX_OK)break;
            module=bundle->frontend;front_offset=bundle->frontend_offset;front_bytes=bundle->frontend_bytes;
        }else{
            if(bytes>FX_NATIVE_MODULE_PACKAGE_LIMIT || fx_module_header_entry(package,bytes)!=FX_MODULE_ENTRY_SERVICES){r=FX_CAPABILITY;break;}
            r=fx_module_inspect(package,bytes,stock,caps,FX_NATIVE_MODULE_IMAGE_LIMIT,&module);if(r!=FX_OK)break;
        }
        uint8_t *front=reserve(s,front_bytes);if(!front){r=FX_BUDGET;break;}memcpy(front,package+front_offset,front_bytes);
        uint32_t index=0u;r=fx_module_index_add(&s->packages,id,&index);if(r!=FX_OK)break;
        FxFrozenPackage *f=&((FxFrozenPackage *)s->packages.records)[index];memset(f,0,sizeof(*f));memcpy(f->id,id,16u);f->package_bytes=bytes;f->frontend=front;f->frontend_bytes=front_bytes;f->is_bundle=bundle?1u:0u;fx_sha256(package,bytes,f->package_sha256);
        r=fx_module_index_add(&s->inventory.index,id,&index);if(r!=FX_OK)break;
        s->inventory.entries=s->inventory.index.records;s->inventory.count=s->inventory.index.count;
        FxDiscoveredModule *d=&s->inventory.entries[index];memset(d,0,sizeof(*d));memcpy(d->id,id,16u);memcpy(d->file,item.name,32u);fx_sha256(front,front_bytes,d->digest);
        d->bytes=front_bytes;d->image_bytes=module.bytes;d->required=(uint32_t)module.required_capabilities;d->stacks=module.stacks;d->enabled=1u;d->period_ms=33u;d->budget_ms=100u;d->frozen_frontend=front;
        /* Keep only the separately owned unique components and frontend.
         * Repeated shared libraries must not retain a complete outer per WLM. */
        memory.release(memory.context,package);s->bytes-=bytes+1u;
        if(bundle){memory.release(memory.context,bundle);s->bytes-=(uint32_t)sizeof(*bundle);}
    }
    {FxResult closed=io->close(io->context);if(closed!=FX_OK)r=closed;}
    if(r==FX_OK && !exhausted)r=FX_BUDGET;
 complete:if(r==FX_OK && cancelled(s))r=FX_INTERRUPTED;
 done:fx_module_index_destroy(&seen);s->error=(uint32_t)r;
    if(r==FX_OK)s->prepared=1u;else s->retained=1u;
    return r;
}
void fx_wlm_frozen_release_native(FxFrozenModules *s){
    if(!s || !s->prepared)return;
    for(uint32_t i=0u;i<s->native_count;++i)if(s->native_views[i].data){
        s->memory.release(s->memory.context,(void *)s->native_views[i].data);s->bytes-=s->native_views[i].bytes;s->native_views[i].data=0;}
}
FxResult fx_wlm_frozen_seal(FxFrozenModules *s){
    /* No cancellation check: after a native commit the snapshot must seal.
     * The cold owner rechecks cancellation of an unpublished attempt. */
    if(!s || !s->prepared || s->sealed || s->error)return FX_STATE;
    for(uint32_t i=0u;i<s->inventory.count;++i){const FxDiscoveredModule *d=&s->inventory.entries[i];uint8_t hash[32];
        fx_sha256(d->frozen_frontend,d->bytes,hash);if(memcmp(hash,d->digest,32u)){s->error=FX_INTEGRITY;s->retained=1u;return FX_INTEGRITY;}}
    /* Native composition owns its immutable plan now. No worker can reopen
     * the outer file; only the independently copied frontend remains live. */
    for(uint32_t i=0u;i<s->packages.count;++i){FxFrozenPackage *p=&((FxFrozenPackage *)s->packages.records)[i];
        if(p->package){s->memory.release(s->memory.context,p->package);s->bytes-=p->package_bytes+1u;p->package=0;}
        if(p->bundle){s->memory.release(s->memory.context,p->bundle);s->bytes-=(uint32_t)sizeof(*p->bundle);p->bundle=0;}}
    fx_wlm_frozen_release_native(s);
    s->sealed=1u;return FX_OK;
}
const FxModuleInventory *fx_wlm_frozen_inventory(const FxFrozenModules *s){return s && s->sealed && !s->error?&s->inventory:0;}
