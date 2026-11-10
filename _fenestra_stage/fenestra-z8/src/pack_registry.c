/* SPDX-License-Identifier: GPL-3.0-only */
#include "fenestra_pack_registry.h"
#include "fenestra_memory.h"
enum {IDLE,QUEUED,RUNNING,DONE,CLOSED};
typedef struct FxPackLease {
    struct FxPackLease *next;
    FxNativePackProvider *provider;
    FxPlatformOwner *owner;
    uint64_t token,owner_key,cookie;
    _Atomic uint32_t lock,phase;
    FxResult result;
    FxServiceRequest request;
    FxServiceReply reply;
} FxPackLease;
static int name_valid(const char *s) {
    uint32_t i=0u;
    for(;i<64u && s[i];++i)if((uint8_t)s[i]<33u || (uint8_t)s[i]>126u)return 0;
    return i && i<64u;
}
static int equal(const char *a,const char *b){for(uint32_t i=0u;i<64u;++i){if(a[i]!=b[i])return 0;if(!a[i])return 1;}return 0;}
static int lock(FxPackLease *l){uint32_t z=0u;return atomic_compare_exchange_strong_explicit(&l->lock,&z,1u,memory_order_acquire,memory_order_relaxed);}
static void unlock(FxPackLease *l){atomic_store_explicit(&l->lock,0u,memory_order_release);}
static void *host_allocate(void *context,uint32_t bytes,uint32_t alignment) {
    FxPackRegistry *r=context;
    return r->io.allocate?r->io.allocate(r->io.context,bytes,alignment):0;
}
static void host_release(void *context,void *p) {
    FxPackRegistry *r=context;
    if(!atomic_load(&r->ready) && r->io.release)r->io.release(r->io.context,p);
}
static FxResult host_lookup(void *context,const char *name,uint32_t version,uintptr_t *address) {
    FxPackRegistry *r=context;if(!name || !address || !name_valid(name))return FX_ARGUMENT;
    for(uint32_t i=0u;i<r->symbol_count;++i)if(equal(name,r->symbols[i].name) && version==r->symbols[i].version){*address=r->symbols[i].address;return FX_OK;}
    for(uint32_t i=0u;r->base && i<r->base->count;++i) {
        const FxNamedBinding *b=&r->base->bindings[i];
        if(equal(name,b->name) && b->version==version && b->kind==FX_BIND_FUNCTION){*address=(uintptr_t)b->address;return FX_OK;}
    }
    *address=0u;return FX_CAPABILITY;
}
void fx_pack_registry_init(FxPackRegistry *r,const FxPlatform *base,FxPackRegistryIO io) {
    memset(r,0,sizeof(*r));r->base=base;r->io=io;
    r->host=(FxNativePackHost){FX_NATIVE_PACK_ABI,sizeof(FxNativePackHost),r,host_allocate,host_release,host_lookup};
}
static FxResult resource(void *,uint32_t,const FxNamedBinding *,FxPlatformOwner *,uint64_t,uint64_t *,const FxServiceRequest *,FxServiceReply *);
static FxResult stage_exports(FxPackRegistry *r,FxNativePackExports *e){
    if(e->abi!=FX_NATIVE_PACK_ABI || e->bytes!=sizeof(*e) || e->provider_count>FX_PACK_PROVIDERS-r->provider_count ||
       e->symbol_count>FX_PACK_SYMBOLS-r->symbol_count || r->base->count+r->provider_count>FX_PACK_BINDINGS-e->provider_count ||
       (e->provider_count && !r->io.data(r->io.context,(uintptr_t)e->providers,e->provider_count*(uint32_t)sizeof(*e->providers))) ||
       (e->symbol_count && !r->io.data(r->io.context,(uintptr_t)e->symbols,e->symbol_count*(uint32_t)sizeof(*e->symbols))) ||
       !e->event || !e->pending || !r->io.code(r->io.context,(uintptr_t)e->event) || !r->io.code(r->io.context,(uintptr_t)e->pending) ||
       !r->io.data(r->io.context,(uintptr_t)e->context,1u))return FX_INTEGRITY;
    uint32_t pn=r->provider_count,sn=r->symbol_count;
    memcpy(r->providers+pn,e->providers,e->provider_count*(uint32_t)sizeof(*e->providers));
    memcpy(r->symbols+sn,e->symbols,e->symbol_count*(uint32_t)sizeof(*e->symbols));
    for(uint32_t i=pn;i<pn+e->provider_count;++i){
        FxNativePackProvider *p=&r->providers[i];
        if(!name_valid(p->name) || !p->version || p->kind!=FX_NATIVE_PACK_DEVICE || p->call_abi!=FX_NATIVE_PACK_CALL_DEVICE ||
           p->capabilities&~FX_SDK_CAPS || p->call_context>FX_NATIVE_PACK_ORIGINAL_UI || p->flags || !p->resource ||
           !r->io.code(r->io.context,(uintptr_t)p->resource) || !r->io.data(r->io.context,(uintptr_t)p->context,1u))return FX_INTEGRITY;
        for(uint32_t j=0u;j<i;++j)if(equal(p->name,r->providers[j].name))return FX_INTEGRITY;
        for(uint32_t j=0u;j<r->base->count;++j)if(equal(p->name,r->base->bindings[j].name))return FX_STATE;
    }
    for(uint32_t i=sn;i<sn+e->symbol_count;++i){
        FxNativePackSymbol *p=&r->symbols[i];
        if(!name_valid(p->name) || !p->version || (p->flags!=FX_NATIVE_PACK_SYMBOL_FUNCTION && p->flags!=FX_NATIVE_PACK_SYMBOL_DATA) ||
           (p->flags==FX_NATIVE_PACK_SYMBOL_FUNCTION?!r->io.code(r->io.context,p->address):!r->io.data(r->io.context,p->address,1u)))return FX_INTEGRITY;
        for(uint32_t j=0u;j<i;++j)if(equal(p->name,r->symbols[j].name))return FX_INTEGRITY;
        for(uint32_t j=0u;j<r->base->count;++j)if(equal(p->name,r->base->bindings[j].name))return FX_STATE;
    }
    r->provider_count=pn+e->provider_count;r->symbol_count=sn+e->symbol_count;return FX_OK;
}
FxResult fx_pack_registry_stage_many(FxPackRegistry *r,const FxNativePackEntry *entries,uint32_t count){
    if(!r || !r->base || !r->io.code || !r->io.data || !entries || !count || count>FX_PACK_ENTRIES || r->staged || atomic_load(&r->ready))return FX_STATE;
    for(uint32_t i=0u;i<count;++i)if(!entries[i] || !r->io.code(r->io.context,(uintptr_t)entries[i]))return FX_INTEGRITY;
    r->staged=1u;r->entry_count=count;
    for(uint32_t i=0u;i<count;++i){
        FxPackEntry *slot=&r->entries[i];slot->invoked=1u;FxResult result=entries[i](&r->host,&slot->exports);
        if(!i)r->exports=slot->exports; /* Retain the v1 first-entry diagnostic. */
        if(result!=FX_OK)return result;
        result=stage_exports(r,&slot->exports);if(result!=FX_OK)return result;slot->validated=1u;
    }
    r->platform=*r->base;memcpy(r->bindings,r->base->bindings,r->base->count*(uint32_t)sizeof(*r->bindings));
    for(uint32_t i=0u;i<r->provider_count;++i){FxNativePackProvider *p=&r->providers[i];
        r->bindings[r->base->count+i]=(FxNamedBinding){.name=p->name,.kind=FX_BIND_DEVICE,.abi=FX_CALL_DEVICE,
            .version=p->version,.flags=FX_BIND_PROVIDER,.capabilities=p->capabilities,.address=(uintptr_t)resource};}
    r->platform.bindings=r->bindings;r->platform.count=r->base->count+r->provider_count;r->staged=2u;return FX_OK;
}
FxResult fx_pack_registry_stage(FxPackRegistry *r,FxNativePackEntry entry){return fx_pack_registry_stage_many(r,&entry,1u);}
static FxResult combine(FxResult current,FxResult next){return next==FX_OK?current:current==FX_OK || (current==FX_BUSY && next!=FX_BUSY)?next:current;}
FxResult fx_pack_registry_abort(FxPackRegistry *r){
    if(!r || atomic_load(&r->ready))return FX_STATE;if(!r->staged)return FX_OK;FxResult result=FX_OK;
    for(uint32_t i=r->entry_count;i>0u;--i){FxPackEntry *slot=&r->entries[i-1u];if(!slot->invoked || slot->aborted)continue;
        FxNativePackExports *e=&slot->exports;FxResult stopped=FX_OK;
        if(e->event){if(!r->io.code(r->io.context,(uintptr_t)e->event) || !r->io.data(r->io.context,(uintptr_t)e->context,1u))stopped=FX_INTEGRITY;
            else stopped=e->event(e->context,FX_NATIVE_PACK_ABORT);}
        if(stopped==FX_OK)slot->aborted=1u;result=combine(result,stopped);
    }
    if(result==FX_OK){r->staged=0u;r->provider_count=r->symbol_count=r->entry_count=0u;memset(&r->exports,0,sizeof(r->exports));memset(r->entries,0,sizeof(r->entries));}
    return result;
}
void fx_pack_registry_publish(FxPackRegistry *r){
    /* All entries are validated before any no-allocation/no-failure PUBLISH.
     * The native exclusion is still held until the enclosing run returns. */
    if(!r || r->staged!=2u || atomic_load(&r->ready))return;
    for(uint32_t i=0u;i<r->entry_count;++i){FxNativePackExports *e=&r->entries[i].exports;(void)e->event(e->context,FX_NATIVE_PACK_PUBLISH);}
    atomic_store_explicit(&r->ready,1u,memory_order_release);
}
const FxPlatform *fx_pack_registry_platform(FxPackRegistry *r,const FxPlatform *fallback) {
    return r && atomic_load_explicit(&r->ready,memory_order_acquire)?&r->platform:fallback;
}
static int same_request(const FxServiceRequest *a,const FxServiceRequest *b) {
    return a->generation==b->generation && a->operation==b->operation && a->bytes==b->bytes &&
        !memcmp(a->arg,b->arg,sizeof(a->arg)) && !memcmp(a->data,b->data,a->bytes);
}
static FxResult resource(void *context,uint32_t op,const FxNamedBinding *binding,FxPlatformOwner *owner,
    uint64_t token,uint64_t *cookie,const FxServiceRequest *request,FxServiceReply *reply) {
    (void)context;if(reply)reply->reserved=0u;FxPackRegistry *r=fx_native_pack_registry();
    if(!r || !owner || !cookie || !atomic_load_explicit(&r->ready,memory_order_acquire))return FX_STATE;
    if(op==FX_RESOURCE_ATTACH) {
        if(atomic_load(&r->stopping) || !r->io.allocate)return FX_STATE;
        FxNativePackProvider *provider=0;
        for(uint32_t i=0u;i<r->provider_count;++i)if(equal(binding->name,r->providers[i].name)){provider=&r->providers[i];break;}
        if(!provider)return FX_CAPABILITY;
        uint32_t n=atomic_fetch_add(&r->lease_count,1u);if(n>=FX_PACK_LEASES){atomic_fetch_sub(&r->lease_count,1u);return FX_BUDGET;}
        FxPackLease *l=r->io.allocate(r->io.context,(uint32_t)sizeof(*l),16u);
        if(!l){atomic_fetch_sub(&r->lease_count,1u);return FX_BUDGET;}
        memset(l,0,sizeof(*l));l->provider=provider;l->owner=owner;l->token=token;
        l->owner_key=((uint64_t)owner->owner<<32)|owner->generation;
        FxResult result=provider->resource(provider->context,op,l->owner_key,token,&l->cookie,request,reply);
        if(result!=FX_OK && !l->cookie){if(r->io.release)r->io.release(r->io.context,l);atomic_fetch_sub(&r->lease_count,1u);return result;}
        FxPackLease *head=atomic_load(&r->leases);do{l->next=head;}while(!atomic_compare_exchange_weak_explicit(&r->leases,&head,l,memory_order_release,memory_order_relaxed));
        *cookie=(uintptr_t)l;return result;
    }
    FxPackLease *l=(void *)(uintptr_t)*cookie;
    /* A lease pointer comes only from this core-owned cookie, never q.data. */
    if(!l || l->owner!=owner || l->token!=token || l->phase==CLOSED)return FX_STALE;
    if(!lock(l)){if(reply&&atomic_load(&l->phase)!=IDLE)reply->reserved=FX_SERVICE_DEFERRED;return FX_BUSY;}
    FxResult result;
    if(op==FX_RESOURCE_DETACH || op==FX_RESOURCE_PANIC) {
        uint32_t phase=atomic_load(&l->phase);
        if(phase==RUNNING){unlock(l);return FX_BUSY;}
        if(phase==QUEUED)atomic_fetch_sub(&r->queued,1u);
        atomic_store(&l->phase,IDLE);
        result=l->provider->resource(l->provider->context,op,l->owner_key,token,&l->cookie,request,reply);
        if(result==FX_OK&&op==FX_RESOURCE_DETACH){atomic_store(&l->phase,CLOSED);*cookie=0u;}
    } else if(op!=FX_RESOURCE_CALL || !request || !reply || request->bytes>FX_SERVICE_BYTES)result=FX_ARGUMENT;
    else if(atomic_load(&r->stopping) || owner->phase!=FX_OWNER_RUNNING)result=FX_STATE;
    else if(l->provider->call_context==FX_NATIVE_PACK_WORKER)
        result=l->provider->resource(l->provider->context,op,l->owner_key,token,&l->cookie,request,reply);
    else {
        uint32_t phase=atomic_load(&l->phase);
        if(phase==DONE && same_request(request,&l->request)) {
            uint32_t sequence=reply->sequence;*reply=l->reply;reply->sequence=sequence;
            result=l->result;atomic_store(&l->phase,IDLE);
        } else if(phase!=IDLE){reply->reserved=FX_SERVICE_DEFERRED;result=FX_BUSY;}
        else {
            l->request=*request;memset(&l->reply,0,sizeof(l->reply));l->result=FX_BUSY;
            atomic_fetch_add(&r->queued,1u);atomic_store_explicit(&l->phase,QUEUED,memory_order_release);
            if(r->io.wake_gui)r->io.wake_gui(r->io.context);reply->reserved=FX_SERVICE_DEFERRED;result=FX_BUSY;
        }
    }
    unlock(l);return result;
}
static FxResult event_locked(FxPackRegistry *r,uint32_t event,uint32_t previous){
    FxResult result=FX_OK;
    if(event==FX_NATIVE_PACK_STOP || event==FX_NATIVE_PACK_FAULT){
        if(atomic_load(&r->fault_requested))event=FX_NATIVE_PACK_FAULT;
        if(!previous || previous==2u || !r->stop_event || (event==FX_NATIVE_PACK_FAULT && r->stop_event!=FX_NATIVE_PACK_FAULT)){
            r->stop_event=event;for(uint32_t i=0u;i<r->entry_count;++i)r->entries[i].stop_ack=r->entries[i].resume_ack=0u;}
        for(uint32_t i=0u;i<r->entry_count;++i){FxPackEntry *slot=&r->entries[i];if(slot->stop_ack)continue;FxNativePackExports *x=&slot->exports;
            FxResult ack=x->event(x->context,r->stop_event);if(ack==FX_OK)slot->stop_ack=1u;result=combine(result,ack);}
        if(fx_pack_registry_pending(r))result=combine(result,FX_BUSY);return result;
    }
    if(event==FX_NATIVE_PACK_RESUME){
        if(fx_pack_registry_pending(r) || atomic_load(&r->fault_requested))return FX_BUSY;if(!atomic_load(&r->stopping))return FX_OK;
        /* 2 keeps all service admission closed while owners ACK. A racing STOP
         * changes it back to 1, so the final CAS cannot reopen admission. */
        uint32_t expected=1u;if(!atomic_compare_exchange_strong(&r->stopping,&expected,2u))return FX_BUSY;
        for(uint32_t i=0u;i<r->entry_count;++i){FxPackEntry *slot=&r->entries[i];if(slot->resume_ack)continue;FxNativePackExports *x=&slot->exports;
            FxResult ack=x->event(x->context,event);if(ack==FX_OK)slot->resume_ack=1u;result=combine(result,ack);}
        expected=2u;uint32_t next=result==FX_OK?0u:1u;
        if(!atomic_compare_exchange_strong(&r->stopping,&expected,next)){
            r->stop_event=0u;for(uint32_t i=0u;i<r->entry_count;++i)r->entries[i].stop_ack=r->entries[i].resume_ack=0u;
            result=combine(result,FX_BUSY);
        }else if(result==FX_OK)r->stop_event=0u;
        return result;
    }
    if(event!=FX_NATIVE_PACK_GUI_POLL)return FX_ARGUMENT;
    for(uint32_t i=0u;i<r->entry_count;++i){FxNativePackExports *x=&r->entries[i].exports;result=combine(result,x->event(x->context,event));}return result;
}
FxResult fx_pack_registry_event(FxPackRegistry *r,uint32_t event){
    if(!r || !atomic_load_explicit(&r->ready,memory_order_acquire))return FX_OK;
    uint32_t previous=atomic_load(&r->stopping);
    if(event==FX_NATIVE_PACK_FAULT)atomic_store(&r->fault_requested,1u);
    if(event==FX_NATIVE_PACK_STOP || event==FX_NATIVE_PACK_FAULT)previous=atomic_exchange(&r->stopping,1u);
    uint32_t zero=0u;if(!atomic_compare_exchange_strong_explicit(&r->event_lock,&zero,1u,memory_order_acquire,memory_order_relaxed))return FX_BUSY;
    FxResult result=event_locked(r,event,previous);atomic_store_explicit(&r->event_lock,0u,memory_order_release);return result;
}
void fx_pack_registry_poll(FxPackRegistry *r){
    if(!r || !atomic_load_explicit(&r->ready,memory_order_acquire))return;
    for(FxPackLease *l=atomic_load_explicit(&r->leases,memory_order_acquire);l;l=l->next){
        if(atomic_load_explicit(&l->phase,memory_order_acquire)!=QUEUED || !lock(l))continue;
        if(atomic_load(&l->phase)!=QUEUED){unlock(l);continue;}atomic_store(&l->phase,RUNNING);
        if(atomic_load(&r->stopping) || l->owner->phase!=FX_OWNER_RUNNING)l->result=FX_STATE;
        else l->result=l->provider->resource(l->provider->context,FX_NATIVE_PACK_CALL,l->owner_key,l->token,&l->cookie,&l->request,&l->reply);
        atomic_fetch_sub(&r->queued,1u);atomic_store_explicit(&l->phase,DONE,memory_order_release);unlock(l);
    }
    if(atomic_load(&r->fault_requested))(void)fx_pack_registry_event(r,FX_NATIVE_PACK_FAULT);
    (void)fx_pack_registry_event(r,FX_NATIVE_PACK_GUI_POLL);
}
uint32_t fx_pack_registry_pending(FxPackRegistry *r){
    if(!r || !atomic_load_explicit(&r->ready,memory_order_acquire))return 0u;
    uint32_t pending=atomic_load(&r->queued);for(uint32_t i=0u;i<r->entry_count;++i){FxNativePackExports *e=&r->entries[i].exports;pending|=e->pending(e->context);}return pending;
}
const FxPlatform *fx_native_pack_platform(const FxPlatform *base){return fx_pack_registry_platform(fx_native_pack_registry(),base);}
void fx_native_pack_gui_poll(void){fx_pack_registry_poll(fx_native_pack_registry());}
uint32_t fx_native_pack_gui_pending(void){return fx_pack_registry_pending(fx_native_pack_registry());}
