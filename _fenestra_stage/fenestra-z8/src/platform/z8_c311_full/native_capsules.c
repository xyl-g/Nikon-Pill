/* SPDX-License-Identifier: GPL-3.0-only
 * Generic C311 card capsule loader. No feature implementation lives here.
 * Exact stock boot reserves the otherwise empty native arena. Both executable
 * RAM banks have the verified linear high alias; RODATA is copied to retained
 * heap pages and remapped, never written through a possible flash mapping.
 */
#include "native_capsules.h"
#include "revocation.h"
#include "z8_full_bindings.h"
#include "menu_data.h"
#include "z8_native_arena_profile.h"
#include "fenestra_memory.h"
#include "fenestra_machine.h"
#include "fenestra_z8_file_read.h"
#define PAGE 4096u
#define PAGE_MASK (~(uintptr_t)(PAGE-1u))
#define ARENA_BEGIN UINT64_C(0x408bf8f0)
#define ARENA_END UINT64_C(0x40c00000)
#define CODE_BEGIN UINT64_C(0x40200000)
#define CODE_END UINT64_C(0x41600000)
#define STOCK_END UINT64_C(0x44f6495d)
#define INIT_BEGIN UINT64_C(0xa02808000)
#define INIT_END (INIT_BEGIN+UINT64_C(0x2ad000))
#define ALIAS_DELTA UINT64_C(0x9cf600000)
const uint64_t fx_z8_native_arena_dependency=FX_Z8_NATIVE_ARENA_DEPENDENCY;
const uint8_t fx_z8_native_arena_profile_identity[32]=FX_Z8_NATIVE_ARENA_PROFILE_IDENTITY;
static const FxZ8NativeResourceRoot resource_roots[FX_Z8_NATIVE_RESOURCE_ROOT_COUNT]=FX_Z8_NATIVE_RESOURCE_ROOT_INITIALIZER;
static void *allocate(uint32_t bytes) {return ((void *(*)(uint32_t,uint32_t))(uintptr_t)FX_Z8_ADDR_40d30458)(bytes,0u);}
static void release(void *p) {((void (*)(void *))(uintptr_t)FX_Z8_ADDR_40d30498)(p);}
static int within(uint64_t at,uint32_t n,uint64_t begin,uint64_t end) {return at>=begin && at<end && n<=end-at;}
static int same_name(const char *a,const char *b) {while(*a && *a==*b){++a;++b;}return *a==*b;}
static const FxNamedBinding *binding(const FxPlatform *p,const char *name) {
    for(uint32_t i=0u;i<p->count;++i)if(same_name(p->bindings[i].name,name))return &p->bindings[i];return 0;
}
static uintptr_t function(const FxPlatform *p,const char *name) {
    const FxNamedBinding *b=binding(p,name);return b && b->kind==FX_BIND_FUNCTION && b->abi==FX_CALL_U64_8?(uintptr_t)b->address:0u;
}
static void view(FxZ8Capsules *s,uint32_t root,FxNativeMmuContext *v) {
    *v=*s->mmu;v->root=s->roots[root];v->private_roots=0u;v->pages=fx_native_mmu_view_pages(s->mmu);
}
/* The stock attributes decoder omits NS and cannot represent arbitrary
 * AP/shareability/XN/MAIR/software-bit combinations. Accept only an exact
 * descriptor that stock context.map can recreate, and restore NS explicitly.
 * Semantic query equality alone is not a complete rollback contract. */
static FxResult descriptor_attributes(uint64_t descriptor,uint32_t reported,uint32_t *complete) {
    if(!complete || (reported&~0x3fu))return FX_CAPABILITY;
    uint32_t type=reported&0x2cu,index;
    switch(type) {
        case 0u:index=0u;break;
        case 4u:index=2u;break;
        case 8u:index=1u;break;
        case 12u:index=3u;break;
        case 32u:index=4u;break;
        default:return FX_CAPABILITY;
    }
    uint64_t leaf=descriptor&3u;if(leaf!=1u && leaf!=3u)return FX_CAPABILITY;
    uint32_t flags=reported|((descriptor&0x20u)?0x40u:0u);
    uint64_t expected=(descriptor&UINT64_C(0xffffff000))|leaf|UINT64_C(0x400)|((uint64_t)index<<2);
    expected|=(flags&1u)?UINT64_C(0xc0):(flags&2u)?0u:UINT64_C(0x40);
    if(!(flags&2u))expected|=UINT64_C(0x60000000000000);
    if(!(flags&0x10u))expected|=UINT64_C(0x300);
    if(flags&0x40u)expected|=UINT64_C(0x20);
    if(descriptor!=expected)return FX_CAPABILITY;
    *complete=flags;return FX_OK;
}
static FxResult query(FxZ8Capsules *s,uint32_t root,uintptr_t address,uint64_t *pa,uint32_t *attr) {
    FxNativeMmuContext v;view(s,root,&v);
    if(((int32_t (*)(void *,uint64_t,uint64_t *,int32_t))s->translate)(&v,address,pa,-1) ||
       ((int32_t (*)(void *,uint64_t,uint32_t *,int32_t))s->attributes)(&v,address,attr,-1))return FX_IO;
    uint64_t *entry=0;
    if(!((uint32_t (*)(void *,uint64_t,uint64_t **,void *,void *,void *,int32_t))s->walk)(
           &v,address,&entry,0,0,0,-1) || !entry)return FX_IO;
    return descriptor_attributes(*(const volatile uint64_t *)entry,*attr,attr);
}
static FxResult protect(FxZ8Capsules *s,uint32_t root,uintptr_t address,uint32_t attr,uint64_t expected) {
    FxNativeMmuContext v;view(s,root,&v);
    int32_t result=((int32_t (*)(void *,uint64_t,uint64_t,uint32_t,uint32_t,int32_t))s->protect)(&v,address,PAGE,attr,0u,-1);
    memcpy(s->mmu->allocated,v.allocated,sizeof(v.allocated));
    uint64_t pa=0u;uint32_t actual=0u;
    return !result && query(s,root,address,&pa,&actual)==FX_OK && pa==expected && actual==attr?FX_OK:FX_IO;
}
static FxResult mapping(FxZ8Capsules *s,uint32_t root,uintptr_t address,uint64_t pa,uint32_t attr,uint32_t remove) {
    FxNativeMmuContext v;view(s,root,&v);int32_t result;
    if(remove) {
        uint64_t current=0u;int32_t before=((int32_t (*)(void *,uint64_t,uint64_t *,int32_t))s->translate)(&v,address,&current,-1);
        if(before==-26)return FX_OK;
        if(before)return FX_IO;
    }
    if(remove)result=((int32_t (*)(void *,uint64_t,uint64_t,uint32_t,int32_t))s->unmap)(&v,address,PAGE,0u,-1);
    else result=((int32_t (*)(void *,uint64_t,uint64_t,uint64_t,uint32_t,uint32_t,int32_t))s->map)(&v,address,PAGE,pa,attr,0u,-1);
    memcpy(s->mmu->allocated,v.allocated,sizeof(v.allocated));
    if(result)return FX_IO;
    if(remove) {
        uint64_t actual=0u;
        if(((int32_t (*)(void *,uint64_t,uint64_t *,int32_t))s->translate)(&v,address,&actual,-1)!=-26)return FX_IO;
    } else {uint64_t actual=0u;uint32_t a=0u;if(query(s,root,address,&actual,&a)!=FX_OK || actual!=pa || a!=attr)return FX_IO;}
    return FX_OK;
}
static void invalidate(FxZ8Capsules *s) {((void (*)(void))s->invalidate)();}
static uint32_t aliases(const FxZ8CapsulePage *p) {return p->address>=CODE_BEGIN && p->address<CODE_END?2u:1u;}
static uintptr_t address_view(const FxZ8CapsulePage *p,uint32_t alias) {return p->address+(alias?ALIAS_DELTA:0u);}
static FxZ8CapsulePage *page(FxZ8Capsules *s,uintptr_t at) {
    at&=PAGE_MASK;for(uint32_t i=0u;i<s->page_count;++i)if(s->pages[i].address==at)return &s->pages[i];return 0;
}
static FxResult cache(FxZ8Capsules *s,uintptr_t address,uint32_t bytes) {
    if(!s->base->io.cache)return FX_CAPABILITY;
    /* Native consumers can later submit resident tables/images to DMA. Clean
     * data to PoC before code synchronization to PoU and before publication. */
    FxResult result=s->base->io.cache(s->base->io.context,address,bytes,FX_MACHINE_CLEAN);
    return result==FX_OK?s->base->io.cache(s->base->io.context,address,bytes,FX_MACHINE_INSTRUCTIONS):result;
}
static FxResult read_target(void *context,uint64_t address,void *out,uint32_t bytes) {
    FxZ8Capsules *s=context;return s->base->io.read(s->base->io.context,address,out,bytes);
}
static FxResult admit(void *context,const FxCapsuleHeader *h,uint32_t index,const FxCapsuleRecord *r) {
    (void)context;(void)h;(void)index;
    /* This field is a native accepted-ID limit, published with font roots by
     * the core. It must never be replaced by an offset-array length. */
    static const struct {uint64_t address;uint32_t bytes;} core_roots[]={
        {FX_Z8_ADDR_a02822824,4u},{FX_Z8_ADDR_a02f4ab30,8u},{FX_Z8_ADDR_a02f4ab40,8u}
    };
    for(uint32_t i=0u;i<sizeof(core_roots)/sizeof(core_roots[0]);++i)
        if(r->address<core_roots[i].address+core_roots[i].bytes &&
           r->address+r->bytes>core_roots[i].address)return FX_CAPABILITY;
    if(r->address<UINT64_C(0x41184d94) && r->address+r->bytes>UINT64_C(0x41184d8c))return FX_CAPABILITY;
    /* The revocation page is loader-owned. All 31 delivered compositions end
     * below 0x40b488d0, so this rejects no existing package. */
    if(r->address<ARENA_END && r->address+r->bytes>FX_Z8_REVOKED_PAGE)return FX_CAPABILITY;
    if(r->kind==FX_CAPSULE_IMAGE)return within(r->address,r->bytes,ARENA_BEGIN,ARENA_END)?FX_OK:FX_CAPABILITY;
    /* Only exact independently reviewed runtime UI roots are writable. This
     * capability contains no replacement pointer or feature implementation. */
    if(r->kind==FX_CAPSULE_HOOK)for(uint32_t i=0u;i<FX_Z8_NATIVE_RESOURCE_ROOT_COUNT;++i)
        if(r->flags==resource_roots[i].flags && r->address==resource_roots[i].address &&
           r->bytes==resource_roots[i].bytes)return FX_OK;
    /* No kernel, loader, IRQ, page-table, DMA buffer or Linux-shared arena is
     * an admissible target. Stock code and immutable menu tables are RTOS-only;
     * initialized RAM is the reviewed C311 RTOS image interval. */
    if(within(r->address,r->bytes,CODE_BEGIN,ARENA_BEGIN) || within(r->address,r->bytes,ARENA_END,STOCK_END))return FX_OK;
    return r->flags!=FX_CAPSULE_INSTRUCTION && within(r->address,r->bytes,INIT_BEGIN,INIT_END)?FX_OK:FX_CAPABILITY;
}
/* Retained-RAM boot keeps original code backing (both banks) and the exact
 * resource roots outside initialized RAM, so only those HOOKs are revoked at
 * power-off. That boot rebuilds RODATA shadow mappings and initialized RAM
 * itself (E06: 44/44 and 8/8 observed old); stock code may also rewrite
 * initialized-RAM targets at runtime, so they are never switched. */
static uint32_t survives(void *context,const FxCapsuleRecord *r) {
    (void)context;
    if(within(r->address,r->bytes,CODE_BEGIN,ARENA_BEGIN) || within(r->address,r->bytes,ARENA_END,CODE_END))return 1u;
    for(uint32_t i=0u;i<FX_Z8_NATIVE_RESOURCE_ROOT_COUNT;++i)
        if(r->address==resource_roots[i].address && r->bytes==resource_roots[i].bytes)return 1u;
    return 0u;
}
static int image_range(FxZ8Capsules *s,uintptr_t a,uint32_t n,uint32_t execute) {
    for(uint32_t i=0u;i<s->control.header.record_count;++i) {
        FxCapsuleRecord r;if(fx_capsule_record(&s->control,i,&r)!=FX_OK)return 0;
        if(r.kind!=FX_CAPSULE_IMAGE)continue;
        if((!execute || (r.flags&FX_CAPSULE_EXECUTE)) && within(a,n,r.address,r.address+r.bytes))return 1;
    }
    return 0;
}
/* Only stock immutable tables or a sealed read-only card IMAGE can supply
 * locale offsets/text. Writable pack state and staging allocations cannot. */
int fx_z8_capsule_locale_span(FxZ8Capsules *s,uintptr_t a,uint32_t n) {
    if(!n)return 0;
    if(within(a,n,CODE_END,STOCK_END))return 1;
    if(!s)return 0;
    for(uint32_t i=0u;i<s->control.header.record_count;++i) {
        FxCapsuleRecord r;if(fx_capsule_record(&s->control,i,&r)!=FX_OK)return 0;
        if(r.kind==FX_CAPSULE_IMAGE && !(r.flags&FX_CAPSULE_WRITE) &&
           within(a,n,r.address,r.address+r.bytes))return 1;
    }
    return 0;
}
static int readonly_image(FxZ8Capsules *s,uintptr_t a,uint32_t n) {
    return within(a,n,ARENA_BEGIN,ARENA_END) && fx_z8_capsule_locale_span(s,a,n);
}
/* IMAGE bytes are already sealed. Directory HOOKs are still private: inspect
 * their exact proposed cells without publishing any native ingress. */
static uintptr_t locale_projected_pointer(FxZ8Capsules *s,uintptr_t at) {
    uint8_t out[sizeof(uintptr_t)];memcpy(out,(const void *)at,sizeof(out));
    for(uint32_t i=s->control.status.image_records;i<s->control.header.record_count;++i) {
        FxCapsuleRecord r;if(fx_capsule_record(&s->control,i,&r)!=FX_OK)return 0u;
        uint64_t end=r.address+r.bytes,first=at>r.address?at:r.address,last=at+sizeof(out)<end?at+sizeof(out):end;
        if(first<last)memcpy(out+(first-at),s->control.package+r.new_offset+(first-r.address),(size_t)(last-first));
    }
    uintptr_t value=0u;memcpy(&value,out,sizeof(value));return value;
}
FxResult fx_z8_capsule_locale_stage(FxZ8Capsules *s) {
    if(!s || s->registry.staged!=2u || atomic_load_explicit(&s->registry.ready,memory_order_acquire))return FX_STATE;
    const FxNativePackSymbol *symbol=0;
    for(uint32_t i=0u;i<s->registry.symbol_count;++i)
        if(same_name(s->registry.symbols[i].name,FX_NATIVE_LOCALE_SYMBOL)) {
            if(symbol)return FX_INTEGRITY;symbol=&s->registry.symbols[i];
        }
    if(!symbol || symbol->version!=1u || symbol->flags!=FX_NATIVE_PACK_SYMBOL_DATA ||
       (symbol->address&7u) || !readonly_image(s,symbol->address,sizeof(FxNativeLocale)))return FX_CAPABILITY;
    FxNativeLocale d;memcpy(&d,(const void *)symbol->address,sizeof(d));
    if(d.abi!=FX_NATIVE_LOCALE_ABI || d.bytes!=sizeof(d) || d.languages!=FX_Z8_UI_LANGUAGES ||
       d.text_count<FX_Z8_UI_TEXT_BASE || d.text_count>FX_NATIVE_LOCALE_CORE_TEXT_BASE ||
       d.offsets_directory!=*(const volatile uintptr_t *)(uintptr_t)FX_Z8_ADDR_a02f4ab38 ||
       d.pools_directory!=*(const volatile uintptr_t *)(uintptr_t)FX_Z8_ADDR_a02f4ab28 ||
       !fx_z8_capsule_locale_span(s,d.offsets_directory,d.languages*(uint32_t)sizeof(uintptr_t)) ||
       !fx_z8_capsule_locale_span(s,d.pools_directory,d.languages*(uint32_t)sizeof(uintptr_t)))return FX_INTEGRITY;
    for(uint32_t language=0u;language<d.languages;++language) {
        uintptr_t offsets=locale_projected_pointer(s,d.offsets_directory+language*sizeof(uintptr_t));
        uintptr_t pool=locale_projected_pointer(s,d.pools_directory+language*sizeof(uintptr_t));
        if((offsets&3u) || !fx_z8_capsule_locale_span(s,offsets,(d.text_count+1u)*4u) ||
           (pool&1u) || !fx_z8_capsule_locale_span(s,pool,2u))return FX_INTEGRITY;
    }
    s->locale=d;return FX_OK;
}
static int data_pointer(void *context,uintptr_t a,uint32_t n) {
    FxZ8Capsules *s=context;return within(a,n,(uintptr_t)s->staging,(uintptr_t)s->staging+FX_Z8_CAPSULE_STAGING) || image_range(s,a,n,0u);
}
static int code_pointer(void *context,uintptr_t a) {return !(a&3u) && image_range(context,a,4u,1u);}
static void *pack_allocate(void *context,uint32_t bytes,uint32_t alignment) {
    FxZ8Capsules *s=context;if(!bytes || !alignment || alignment>PAGE || (alignment&(alignment-1u)))return 0;
    if(!atomic_load_explicit(&s->registry.ready,memory_order_acquire)) {
        uint32_t at=(s->staging_used+alignment-1u)&~(alignment-1u);
        if(at>FX_Z8_CAPSULE_STAGING || bytes>FX_Z8_CAPSULE_STAGING-at)return 0;
        void *p=s->staging+at;s->staging_used=at+bytes;memset(p,0,bytes);return p;
    }
    if(alignment>16u || bytes>UINT32_MAX-16u)return 0;
    /* Original Nikon heap satisfies 16-byte native C alignment. */
    void *p=allocate(bytes);if(p && ((uintptr_t)p&(alignment-1u))){release(p);return 0;}return p;
}
static void pack_release(void *context,void *p) {
    FxZ8Capsules *s=context;if(!p || within((uintptr_t)p,1u,(uintptr_t)s->staging,(uintptr_t)s->staging+FX_Z8_CAPSULE_STAGING))return;
    release(p);
}
static void wake_gui(void *context) {
    (void)context;void *group=((void *(*)(void))(uintptr_t)FX_Z8_ADDR_403d6908)();
    if(group && *(const uintptr_t *)group)((void (*)(void *,uint32_t))(uintptr_t)FX_Z8_ADDR_403d69d4)(group,0x200000u);
}
static int boot_cancelled(const FxZ8Capsules *s) {return s->cancelled && s->cancelled(s->cancel_context);}
static FxResult permissions(FxZ8Capsules *,FxZ8CapsulePage *,uint32_t,uint32_t);
/* Same exclusion, W^X and alias protocol as a HOOK write. Rollback-mode
 * permissions restore the exact attributes first captured from stock. */
static FxResult revoked_write(FxZ8Capsules *s,const FxZ8Revoked *value) {
    FxZ8CapsulePage *p=page(s,(uintptr_t)FX_Z8_REVOKED_PAGE);if(!p || !s->entered)return FX_STATE;
    FxResult result=permissions(s,p,1u,0u);
    if(result==FX_OK) {
        memcpy((void *)(uintptr_t)FX_Z8_REVOKED_AT,value,sizeof(*value));
        result=cache(s,(uintptr_t)FX_Z8_REVOKED_PAGE,PAGE);
        if(result==FX_OK)result=cache(s,(uintptr_t)FX_Z8_REVOKED_PAGE+ALIAS_DELTA,PAGE);
    }
    FxResult restored=permissions(s,p,0u,1u);if(result==FX_OK)result=restored;
    FxZ8Revoked now;memcpy(&now,(const void *)(uintptr_t)FX_Z8_REVOKED_AT,sizeof(now));
    s->revoked=(uint32_t)fx_z8_revoked_valid(&now);
    if(result==FX_OK && memcmp(&now,value,sizeof(now)))result=FX_INTEGRITY;
    return result;
}
/* Any operation that may write a HOOK first withdraws the record. */
static FxResult revoked_withdraw(FxZ8Capsules *s) {
    if(!s->revoked)return FX_OK;
    FxZ8Revoked zero;memset(&zero,0,sizeof(zero));FxResult result=revoked_write(s,&zero);
    if(result!=FX_OK)s->revoked_error=(uint32_t)result;
    return s->revoked?FX_INTEGRITY:FX_OK;
}
static void collect_roots(FxZ8Capsules *s,uintptr_t roots[4],uint32_t *count) {
    *count=1u;roots[0]=s->mmu->root;
    if(s->mmu->private_roots)for(uint32_t i=0u;i<3u;++i) {
        uintptr_t a=s->mmu->roots[i];uint32_t duplicate=0u;
        for(uint32_t j=0u;j<*count;++j)if(roots[j]==a)duplicate=1u;
        if(!duplicate)roots[(*count)++]=a;
    }
}
/* Disarm/rearm keep the cold page plan: original PA and attributes remain
 * those captured from stock. Every root and alias must still translate
 * exactly as this transaction left it; any other owner aborts before writes. */
static FxResult reenter(FxZ8Capsules *s) {
    uint32_t zero=0u;if(!atomic_compare_exchange_strong(&s->mappings->lock,&zero,1u))return FX_BUSY;s->lock_held=1u;
    uintptr_t roots[4];uint32_t count=0u;collect_roots(s,roots,&count);
    if(!s->prepared || count!=s->root_count)return FX_INTEGRITY;
    for(uint32_t root=0u;root<count;++root)if(roots[root]!=s->roots[root])return FX_INTEGRITY;
    for(uint32_t i=0u;i<s->page_count;++i) {
        FxZ8CapsulePage *p=&s->pages[i];
        for(uint32_t root=0u;root<s->root_count;++root)for(uint32_t a=0u;a<aliases(p);++a) {
            uint64_t pa=0u,expected=p->shadow_mapped && !a?p->shadow_physical:p->physical[root][a];uint32_t attr=0u;
            if(query(s,root,address_view(p,a),&pa,&attr)!=FX_OK || pa!=expected)return FX_INTEGRITY;
        }
    }
    invalidate(s);s->entered=1u;
    return s->operation==FX_Z8_CAPSULE_REARM?revoked_withdraw(s):FX_OK;
}
static FxResult enter(void *context,const FxCapsuleHeader *h,const uint8_t *package,uint64_t *lease) {
    FxZ8Capsules *s=context;(void)h;(void)package;FxResult result=fx_z8_capsule_exclusion_enter(&s->rendezvous,lease);
    if(result!=FX_OK)return result;s->parked=1u;
    if(s->operation!=FX_Z8_CAPSULE_COMMIT)return reenter(s);
    if(boot_cancelled(s))return FX_INTERRUPTED;
    uint32_t zero=0u;if(!atomic_compare_exchange_strong(&s->mappings->lock,&zero,1u))return FX_BUSY;s->lock_held=1u;
    collect_roots(s,s->roots,&s->root_count);
    for(uint32_t root=0u;root<s->root_count;++root)if(!s->roots[root] || (s->roots[root]&(PAGE-1u)))return FX_INTEGRITY;
    for(uint32_t i=0u;i<s->page_count;++i) {
        FxZ8CapsulePage *p=&s->pages[i];
        for(uint32_t root=0u;root<s->root_count;++root)for(uint32_t a=0u;a<aliases(p);++a) {
            result=query(s,root,address_view(p,a),&p->physical[root][a],&p->attributes[root][a]);
            if(result!=FX_OK || (p->physical[root][a]&(PAGE-1u)) ||
               p->physical[root][a]!=p->physical[0][0])return FX_INTEGRITY;
        }
        if(p->shadow) {
            for(uint32_t root=0u;root<s->root_count;++root) {
                uint64_t physical=0u;uint32_t attr=0u;result=query(s,root,p->shadow,&physical,&attr);
                if(result!=FX_OK || (physical&(PAGE-1u)) || (attr&1u) ||
                   (root && physical!=p->shadow_physical))return FX_INTEGRITY;
                p->shadow_physical=physical;
                /* Entire aligned page is exclusively retained heap backing.
                 * Some stock heap views execute: remove X from every view. */
                if((attr&2u) && protect(s,root,p->shadow,attr&~2u,physical)!=FX_OK)return FX_IO;
            }
        }
    }
    for(uint32_t i=0u;i<s->page_count;++i)if(s->pages[i].shadow) {
        uint64_t pa=s->pages[i].shadow_physical;
        for(uint32_t j=0u;j<s->page_count;++j) {
            if(pa==s->pages[j].physical[0][0] || (i!=j && s->pages[j].shadow && pa==s->pages[j].shadow_physical))return FX_INTEGRITY;
        }
    }
    invalidate(s);s->prepared=1u;s->entered=1u;return revoked_withdraw(s);
}
/* A cross-PA replacement breaks every relevant root before the acknowledged
 * TLBI epoch. The remote cores remain parked throughout the new map and any
 * rollback. RODATA old PA is only read, even on a mapping/write error. */
static FxResult replace_shadow(FxZ8Capsules *s,FxZ8CapsulePage *p,uint64_t lease,uint32_t rollback) {
    FxResult result=FX_OK;
    for(uint32_t root=0u;root<s->root_count;++root) {
        FxResult r=mapping(s,root,p->address,0u,0u,1u);if(r!=FX_OK)result=r;
    }
    invalidate(s);
    FxResult synced=fx_z8_capsule_exclusion_leave(&s->rendezvous,lease,FX_CAPSULE_LEAVE_RETAINED);
    if(synced!=FX_OK)return synced;
    /* Even an unmap reporting failure may have partially changed a table.
     * On rollback attempt every old mapping and verify it independently. */
    if(result!=FX_OK)return result;
    result=FX_OK;
    for(uint32_t root=0u;root<s->root_count;++root) {
        uint32_t attr=rollback?p->attributes[root][0]:(p->attributes[root][0]&~3u);
        FxResult r=mapping(s,root,p->address,rollback?p->physical[root][0]:p->shadow_physical,attr,0u);
        if(r!=FX_OK)result=r;
    }
    invalidate(s);if(result==FX_OK)p->shadow_mapped=rollback?0u:1u;return result;
}
static FxResult permissions(FxZ8Capsules *s,FxZ8CapsulePage *p,uint32_t writable,uint32_t rollback) {
    FxResult result=FX_OK;
    for(uint32_t root=0u;root<s->root_count;++root)for(uint32_t a=0u;a<aliases(p);++a) {
        uint32_t old=p->attributes[root][a],attr=old;
        if(writable)attr=old&~3u;
        else if(!rollback && p->image) {
            attr=(old&~3u)|((p->flags&FX_CAPSULE_WRITE)?0u:1u);
            if(a)attr=(old&~3u)|((p->flags&FX_CAPSULE_EXECUTE)?1u:0u);
            else if(p->flags&FX_CAPSULE_EXECUTE)attr|=2u;
        } else if(!rollback && aliases(p)==2u)attr=a?((old|1u)&~2u):(old|3u);
        uint64_t pa=p->shadow_mapped?p->shadow_physical:p->physical[root][a];
        FxResult r=protect(s,root,address_view(p,a),attr,pa);if(r!=FX_OK)result=r;
    }
    invalidate(s);return result;
}
static FxResult apply(void *context,uint64_t lease,const FxCapsuleRecord *r,const uint8_t *data,uint32_t rollback) {
    FxZ8Capsules *s=context;if(!s->prepared || !s->lock_held || !s->parked || lease!=s->rendezvous.lease)return FX_STATE;
    uintptr_t end=(uintptr_t)r->address+r->bytes;
    for(uintptr_t at=(uintptr_t)r->address&PAGE_MASK;at<end;at+=PAGE) {
        FxZ8CapsulePage *p=page(s,at);if(!p)return FX_INTEGRITY;
        if(p->shadow && rollback) {
            if(p->changed){FxResult result=replace_shadow(s,p,lease,1u);if(result!=FX_OK)return result;p->changed=0u;}
            continue;
        }
        if(p->shadow && !p->shadow_mapped) {
            /* Copy the whole page before breaking its readable mapping. */
            memcpy((void *)p->shadow,(const void *)p->address,PAGE);
            FxResult result=cache(s,p->shadow,PAGE);if(result!=FX_OK)return result;p->changed=1u;
            result=replace_shadow(s,p,lease,0u);if(result!=FX_OK)return result;
        }
        p->changed=1u;FxResult result=permissions(s,p,1u,rollback);if(result!=FX_OK)return result;
        uintptr_t first=at<(uintptr_t)r->address?(uintptr_t)r->address:at,last=end<at+PAGE?end:at+PAGE;
        memcpy((void *)first,data+(first-(uintptr_t)r->address),last-first);
        result=cache(s,at,PAGE);if(result!=FX_OK)return result;
        if(aliases(p)==2u){result=cache(s,at+ALIAS_DELTA,PAGE);if(result!=FX_OK)return result;}
        result=permissions(s,p,0u,rollback);if(result!=FX_OK)return result;
    }
    return FX_OK;
}
static FxResult leave(void *context,uint64_t lease,uint32_t outcome) {
    FxZ8Capsules *s=context;
    /* Sealed only after a DISARM proved every surviving HOOK old here. */
    if(s->operation==FX_Z8_CAPSULE_DISARM && outcome==FX_CAPSULE_LEAVE_COMMIT && s->entered) {
        FxZ8Revoked value;fx_z8_revoked_seal(&value,s->control.header.package_id,s->control.package_sha256);
        s->revoked_error=(uint32_t)revoked_write(s,&value);
    }
    s->entered=0u;
    FxResult result=fx_z8_capsule_exclusion_leave(&s->rendezvous,lease,outcome);
    if(s->lock_held){atomic_store_explicit(&s->mappings->lock,0u,memory_order_release);s->lock_held=0u;}
    return result;
}
static FxResult stage_entry(void *context,uint64_t lease,uint64_t entry) {
    FxZ8Capsules *s=context;if(!s->parked || lease!=s->rendezvous.lease)return FX_STATE;
    FxResult result=s->native_entry_count?fx_pack_registry_stage_many(&s->registry,s->native_entries,s->native_entry_count):
        fx_pack_registry_stage(&s->registry,(FxNativePackEntry)(uintptr_t)entry);
    return result==FX_OK?fx_z8_capsule_locale_stage(s):result;
}
static FxResult abort_entry(void *context,uint64_t lease,uint64_t entry) {
    FxZ8Capsules *s=context;(void)lease;(void)entry;return fx_pack_registry_abort(&s->registry);
}
static void publish_entry(void *context,uint64_t entry) {FxZ8Capsules *s=context;(void)entry;fx_pack_registry_publish(&s->registry);}
static FxResult commit(void *context) {FxZ8Capsules *s=context;return fx_capsule_commit(&s->control);}
static FxResult plan(FxZ8Capsules *s) {
    s->pages=allocate(FX_Z8_CAPSULE_PAGES*(uint32_t)sizeof(*s->pages));s->staging=allocate(FX_Z8_CAPSULE_STAGING);
    if(!s->pages || !s->staging)return FX_BUDGET;memset(s->pages,0,FX_Z8_CAPSULE_PAGES*sizeof(*s->pages));
    if((uintptr_t)s->staging&15u)return FX_INTEGRITY;
    for(uint32_t i=0u;i<s->control.header.record_count;++i) {
        FxCapsuleRecord r;FxResult result=fx_capsule_record(&s->control,i,&r);if(result!=FX_OK)return result;
        uintptr_t end=(uintptr_t)r.address+r.bytes;
        for(uintptr_t at=(uintptr_t)r.address&PAGE_MASK;at<end;at+=PAGE) {
            FxZ8CapsulePage *p=page(s,at);
            if(!p){if(s->page_count==FX_Z8_CAPSULE_PAGES)return FX_BUDGET;p=&s->pages[s->page_count++];p->address=at;}
            if(r.kind==FX_CAPSULE_IMAGE){p->image=1u;p->flags|=r.flags;if((p->flags&3u)==3u)return FX_CAPABILITY;}
        }
    }
    /* Captured with every record page so the record can be withdrawn or
     * sealed under the same exclusion. admit keeps records off this page. */
    if(page(s,(uintptr_t)FX_Z8_REVOKED_PAGE))return FX_INTEGRITY;
    if(s->page_count==FX_Z8_CAPSULE_PAGES)return FX_BUDGET;
    s->pages[s->page_count++].address=(uintptr_t)FX_Z8_REVOKED_PAGE;
    for(uint32_t i=0u;i<s->page_count;++i) {
        FxZ8CapsulePage *p=&s->pages[i];
        if(p->address>=CODE_END && p->address<STOCK_END) {
            void *raw=allocate(PAGE*2u-1u);if(!raw)return FX_BUDGET;
            /* Retain raw allocation for the boot lifetime, including failures.
             * No live page can outlast the backing that may have been mapped. */
            p->shadow=((uintptr_t)raw+PAGE-1u)&PAGE_MASK;
        }
    }
    return FX_OK;
}
static FxResult file_exact(FxZ8Capsules *s,char drive,const char *leaf,uint8_t *out,uint32_t n,uint32_t absent_ok) {
    char path[32]="A:\\FENESTRA\\";path[0]=drive;uint32_t at=12u;
    for(uint32_t i=0u;leaf[i] && at<sizeof(path)-1u;++i)path[at++]=leaf[i];path[at]=0;
    if(boot_cancelled(s))return FX_INTERRUPTED;
    int32_t fd=((int32_t (*)(const char *,uint32_t,uint32_t))(uintptr_t)FX_Z8_ADDR_402dc960)(path,0u,0u);
    if(fd<0)return absent_ok?FX_EMPTY:FX_IO;
    uint32_t got=0u;FxResult result=FX_OK;
    while(got<n) {
        if(boot_cancelled(s)){result=FX_INTERRUPTED;break;}
        uint32_t take=n-got;if(take>32768u)take=32768u;uint32_t done=0u;
        result=fx_z8_file_read(fd,out+got,take,&done);if(result!=FX_OK || !done){if(result==FX_OK)result=FX_BOUNDS;break;}got+=done;
    }
    if(result==FX_OK && boot_cancelled(s))result=FX_INTERRUPTED;
    if(result==FX_OK){uint8_t extra;uint32_t done=0u;result=fx_z8_file_read(fd,&extra,1u,&done);if(result==FX_OK && done)result=FX_BOUNDS;}
    uint32_t closed=((uint32_t (*)(int32_t))(uintptr_t)FX_Z8_ADDR_402dc9c0)(fd);if(closed)result=FX_CLOSE;
    else if(result==FX_OK && boot_cancelled(s))result=FX_INTERRUPTED;
    return result;
}
static FxResult boot_package(FxZ8Capsules *s,const FxPlatform *base,uint32_t bytes,const uint8_t hash[32]) {
    FxResult result;
    const FxNamedBinding *mmu=binding(base,"rtos.mmu.context");
    if(!mmu || mmu->kind!=FX_BIND_TABLE || mmu->bytes<sizeof(FxNativeMmuContext)){result=FX_CAPABILITY;goto done;}
    s->mmu=(void *)(uintptr_t)mmu->address;
    s->translate=function(base,"rtos.mmu.context.translate");s->attributes=function(base,"rtos.mmu.context.attributes");
    s->walk=function(base,"rtos.mmu.context.walk");
    s->protect=function(base,"rtos.mmu.context.protect");s->map=function(base,"rtos.mmu.context.map");
    s->unmap=function(base,"rtos.mmu.context.unmap");s->invalidate=function(base,"rtos.mmu.invalidate-all");
    if(!s->translate || !s->attributes || !s->walk || !s->protect || !s->map || !s->unmap || !s->invalidate || !base->io.cache){result=FX_CAPABILITY;goto done;}
    FxCapsuleIO io={s,read_target,admit,enter,apply,leave,stage_entry,abort_entry,publish_entry,survives};
    static const uint8_t target[32]={0x2b,0x43,0xa3,0x1c,0x9d,0xe8,0x38,0xf8,0x4b,0x12,0x41,0x6d,0xe4,0x34,0x8a,0x70,0x04,0xe6,0x5b,0xe0,0x7f,0x85,0x3e,0xc5,0x6e,0xc2,0xb1,0x0f,0x3a,0x8e,0x38,0x53};
    result=fx_capsule_init(&s->control,target,fx_z8_native_arena_dependency,io);if(result!=FX_OK)goto done;
    /* Retained RAM can keep the code bank and arena across a fresh heap/task
     * boot. Only a valid revocation record makes earlier IMAGE bytes
     * unreachable; HOOK preimages are always exact. Without it, retained
     * IMAGE bytes stay a STALE failure and are never overwritten. */
    FxZ8Revoked revoked;
    s->revoked=(uint32_t)(read_target(s,FX_Z8_REVOKED_AT,&revoked,sizeof(revoked))==FX_OK && fx_z8_revoked_valid(&revoked));
    s->unreachable_boot=s->revoked;s->operation=FX_Z8_CAPSULE_COMMIT;
    result=s->revoked?fx_capsule_prepare_unreachable(&s->control,s->package,bytes,hash):
        fx_capsule_prepare(&s->control,s->package,bytes,hash);
    if(result!=FX_OK)goto done;
    /* A continued attempt keeps the plan of this same package: never entered. */
    if(!s->pages){result=plan(s);if(result!=FX_OK)goto done;}
    if(boot_cancelled(s)){result=FX_INTERRUPTED;goto done;}
    fx_pack_registry_init(&s->registry,base,(FxPackRegistryIO){s,pack_allocate,pack_release,wake_gui,data_pointer,code_pointer});
    result=fx_z8_capsule_rendezvous_run(&s->rendezvous,commit,s);s->parked=0u;
    /* A native dispatch failure after commit cannot be relabelled READY. */
    if(result!=FX_OK && s->control.status.phase==FX_CAPSULE_READY){s->control.status.phase=FX_CAPSULE_RETAINED;atomic_store(&s->registry.stopping,1u);}
 done:s->error=(uint32_t)result;return result;
}
FxResult fx_z8_capsule_boot(FxZ8Capsules *s,const FxPlatform *base,FxNativeMappingRegistry *mappings,char drive) {
    if(!s || !base || !mappings || (drive!='A' && drive!='B'))return FX_ARGUMENT;
    uint32_t zero=0u;if(!atomic_compare_exchange_strong(&s->boot_attempted,&zero,1u))return (FxResult)s->error;
    s->base=base;s->mappings=mappings;
    uint8_t index[56];FxResult result=file_exact(s,drive,"BOOT.IDX",index,sizeof(index),1u);
    if(result==FX_EMPTY){s->error=FX_OK;return FX_OK;}
    if(result!=FX_OK)goto done;s->present=1u;
    uint32_t abi=0u,n=0u;uint64_t bytes=0u;memcpy(&abi,index+8u,4u);memcpy(&n,index+12u,4u);memcpy(&bytes,index+16u,8u);
    if(memcmp(index,"FNCIDX01",8u) || abi!=1u || n!=56u || bytes<128u || bytes>FX_CAPSULE_MAX_PACKAGE){result=FX_INTEGRITY;goto done;}
    s->package=allocate((uint32_t)bytes);if(!s->package){result=FX_BUDGET;goto done;}
    result=file_exact(s,drive,"BOOT.FNC",s->package,(uint32_t)bytes,0u);if(result!=FX_OK)goto done;
    return boot_package(s,base,(uint32_t)bytes,index+24u);
 done:s->error=(uint32_t)result;return result;
}
static FxResult disarm_work(void *context){FxZ8Capsules *s=context;return fx_capsule_disarm(&s->control);}
static FxResult rearm_work(void *context){FxZ8Capsules *s=context;return fx_capsule_rearm(&s->control);}
static FxResult ingress(FxZ8Capsules *s,uint32_t operation) {
    s->operation=operation;
    FxResult result=fx_z8_capsule_rendezvous_run(&s->rendezvous,operation==FX_Z8_CAPSULE_DISARM?disarm_work:rearm_work,s);
    s->parked=0u;s->operation=FX_Z8_CAPSULE_COMMIT;s->ingress_error=(uint32_t)result;return result;
}
FxResult fx_z8_capsule_disarm(FxZ8Capsules *s) {
    if(!s)return FX_ARGUMENT;
    if(!s->present || !s->control.magic)return FX_OK;
    uint32_t phase=s->control.status.phase;
    /* Already revoked and sealed: nothing to prove again. */
    if(phase==FX_CAPSULE_DISARMED && s->revoked)return FX_OK;
    if(phase!=FX_CAPSULE_READY && phase!=FX_CAPSULE_DISARMED)return FX_OK;
    FxResult result=ingress(s,FX_Z8_CAPSULE_DISARM);
    s->ingress_busy=result==FX_BUSY?s->ingress_busy+1u:0u;return result;
}
FxResult fx_z8_capsule_rearm(FxZ8Capsules *s) {
    if(!s)return FX_ARGUMENT;
    if(!s->present || !s->control.magic)return FX_OK;
    uint32_t phase=s->control.status.phase;
    if(phase==FX_CAPSULE_READY)return FX_OK;
    if(phase!=FX_CAPSULE_DISARMED)return FX_STATE;
    return ingress(s,FX_Z8_CAPSULE_REARM);
}
int fx_z8_capsule_pristine(FxZ8Capsules *s) {
    if(!s)return 0;
    if(!atomic_load(&s->boot_attempted))return 1;
    const FxCapsuleControl *c=&s->control;uint32_t phase=c->status.phase;
    return !s->parked && !s->lock_held && !s->entered && !s->prepared && !c->lease && !c->attempted &&
        !c->entry_started && !c->status.applied_records && !c->status.retained_records &&
        !s->registry.staged && !atomic_load(&s->registry.ready) &&
        (phase==FX_CAPSULE_EMPTY || phase==FX_CAPSULE_PREPARED || phase==FX_CAPSULE_FAILED);
}
FxResult fx_z8_capsule_rewind(FxZ8Capsules *s) {
    if(!s)return FX_ARGUMENT;
    if(!fx_z8_capsule_pristine(s))return FX_STATE;
    /* fx_capsule_init accepts only a cleared control; prepare re-validates. */
    memset(&s->control,0,sizeof(s->control));
    s->error=FX_OK;s->operation=FX_Z8_CAPSULE_COMMIT;
    atomic_store(&s->boot_attempted,0u);return FX_OK;
}
static void *compose_allocate(void *context,uint32_t bytes){(void)context;return allocate(bytes);}
static void compose_release(void *context,void *p){(void)context;release(p);}
FxResult fx_z8_wlm_native_boot(FxZ8Capsules *s,const FxPlatform *base,FxNativeMappingRegistry *mappings,const FxWlmNativeView *views,uint32_t count) {
    if(!s || !base || !mappings || (count && !views))return FX_ARGUMENT;
    uint32_t zero=0u;if(!atomic_compare_exchange_strong(&s->boot_attempted,&zero,1u))return (FxResult)s->error;
    s->base=base;s->mappings=mappings;if(!count){s->error=FX_OK;return FX_OK;}
    if(boot_cancelled(s)){s->error=FX_INTERRUPTED;return FX_INTERRUPTED;}
    /* A continued attempt already owns this composition; inputs were released. */
    if(!s->package) {
        static const uint8_t target[32]={0x2b,0x43,0xa3,0x1c,0x9d,0xe8,0x38,0xf8,0x4b,0x12,0x41,0x6d,0xe4,0x34,0x8a,0x70,0x04,0xe6,0x5b,0xe0,0x7f,0x85,0x3e,0xc5,0x6e,0xc2,0xb1,0x0f,0x3a,0x8e,0x38,0x53};
        FxWlmNativePlan composed;FxResult result=fx_wlm_native_compose(views,count,target,fx_z8_native_arena_dependency,
            (FxWlmNativeMemory){0,compose_allocate,compose_release},&composed);
        if(result!=FX_OK){s->error=(uint32_t)result;return result;}
        /* Retain the immutable private transaction: image/code/locale bounds are
         * checked from its record table throughout the cold boot lifetime. */
        s->package=composed.package;s->present=1u;s->native_selection=composed.selection;s->native_entry_count=composed.entry_count;
        s->composed_bytes=composed.bytes;memcpy(s->composed_sha256,composed.sha256,32u);
        for(uint32_t i=0u;i<composed.entry_count;++i)s->native_entries[i]=(FxNativePackEntry)(uintptr_t)composed.entries[i];
        if(s->release_inputs){void (*drop)(void *)=s->release_inputs;s->release_inputs=0;drop(s->release_context);}
        if(boot_cancelled(s)){s->error=FX_INTERRUPTED;return FX_INTERRUPTED;}
    }
    return boot_package(s,base,s->composed_bytes,s->composed_sha256);
}
