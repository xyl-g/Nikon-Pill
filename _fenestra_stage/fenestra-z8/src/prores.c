/* SPDX-License-Identifier: GPL-3.0-only */
#include "fenestra_prores.h"
#include "fenestra_memory.h"
static int enter(FxProResControl *s) {
    uint32_t zero=0u;
    return atomic_compare_exchange_strong_explicit(&s->gate,&zero,1u,memory_order_acquire,memory_order_relaxed);
}
static void leave(FxProResControl *s){atomic_store_explicit(&s->gate,0u,memory_order_release);}
static void uncertain(FxProResControl *s,FxResult error) {
    uint32_t zero=0u;
    (void)atomic_compare_exchange_strong(&s->fault,&zero,(uint32_t)error);
    atomic_store_explicit(&s->uncertain,1u,memory_order_release);
}
void fx_prores_init(FxProResControl *s,_Atomic uint8_t *value) {
    memset(s,0,sizeof(*s));s->value=value;
}
uint32_t fx_prores_selected(const FxProResControl *s) {
    if(!s || !s->value || !atomic_load_explicit(&s->enabled,memory_order_acquire))return UINT32_MAX;
    uint32_t grade=atomic_load_explicit(s->value,memory_order_acquire);
    return grade<4u?grade:0u;
}
void fx_prores_movie_open(FxProResControl *s,uintptr_t context) {
    if(!s)return;
    if(!context){uncertain(s,FX_ARGUMENT);return;}
    /* Native tasks never spin or allocate. Lost/ambiguous lifetime evidence
     * freezes selection and retains ownership until reboot. */
    if(!enter(s)){uncertain(s,FX_BUSY);return;}
    uint32_t free_slot=FX_PRORES_MOVIES;
    for(uint32_t i=0u;i<FX_PRORES_MOVIES;++i) {
        if(s->movies[i]==context){uncertain(s,FX_INTEGRITY);leave(s);return;}
        if(!s->movies[i])free_slot=i;
    }
    if(free_slot==FX_PRORES_MOVIES){uncertain(s,FX_BUDGET);leave(s);return;}
    s->movies[free_slot]=context;atomic_fetch_add_explicit(&s->active,1u,memory_order_release);leave(s);
}
void fx_prores_movie_close(FxProResControl *s,uintptr_t context) {
    if(!s || !context)return;
    if(!enter(s)){uncertain(s,FX_BUSY);return;}
    for(uint32_t i=0u;i<FX_PRORES_MOVIES;++i)if(s->movies[i]==context) {
        s->movies[i]=0u;atomic_fetch_sub_explicit(&s->active,1u,memory_order_release);break;
    }
    if(s->revoked && !atomic_load(&s->active) && !atomic_load(&s->uncertain))
        atomic_store_explicit(&s->enabled,0u,memory_order_release);
    leave(s);
}
static void status(FxProResControl *s,FxServiceReply *a) {
    uint32_t grade=atomic_load_explicit(s->value,memory_order_acquire),active=atomic_load(&s->active);
    FxProResStatus v={FX_PRORES_ABI,sizeof(v),4u,grade<4u?grade:0u,
        (atomic_load(&s->enabled)?FX_PRORES_ENABLED:0u)|(active?FX_PRORES_RECORDING:0u)|
        (s->revoked?FX_PRORES_REVOKED:0u)|(atomic_load(&s->fault)?FX_PRORES_FAULT:0u),
        active,atomic_load(&s->fault),s->revision};
    memcpy(a->data,&v,sizeof(v));a->bytes=sizeof(v);a->value[0]=v.selected;a->value[1]=v.flags;
}
FxResult fx_prores_resource(FxProResControl *s,uint32_t op,FxPlatformOwner *o,uint64_t token,
    uint64_t *cookie,const FxServiceRequest *q,FxServiceReply *a) {
    if(!s || !s->value)return FX_CAPABILITY;
    if(!o || !token || !cookie)return FX_ARGUMENT;
    if(!enter(s))return FX_BUSY;
    FxResult result=FX_OK;
    if(op==FX_RESOURCE_ATTACH) {
        if(atomic_load(&s->fault) || atomic_load(&s->uncertain))result=FX_STATE;
        else if(atomic_load(&s->owner) || atomic_load(&s->active))result=FX_BUSY;
        else {
            uint32_t grade=atomic_load_explicit(s->value,memory_order_acquire);
            if(grade>=4u)atomic_store_explicit(s->value,0u,memory_order_release);
            s->token=token;s->revoked=0u;*cookie=token;
            atomic_store(&s->owner,(uintptr_t)o);atomic_store_explicit(&s->enabled,1u,memory_order_release);
        }
    } else if(*cookie!=token || s->token!=token || atomic_load(&s->owner)!=(uintptr_t)o)result=FX_STALE;
    else if(op==FX_RESOURCE_PANIC || op==FX_RESOURCE_DETACH) {
        s->revoked=1u;
        if(atomic_load(&s->active) || atomic_load(&s->uncertain))result=op==FX_RESOURCE_PANIC?FX_OK:FX_BUSY;
        else {
            atomic_store_explicit(&s->enabled,0u,memory_order_release);
            if(op==FX_RESOURCE_DETACH){atomic_store(&s->owner,0u);s->token=0u;*cookie=0u;}
        }
    } else if(op!=FX_RESOURCE_CALL || !q || !a || q->arg[3])result=FX_ARGUMENT;
    else if(q->arg[2]==FX_PRORES_QUERY && !q->bytes)status(s,a);
    else if(q->arg[2]==FX_PRORES_SELECT && q->bytes==sizeof(FxProResSelection)) {
        FxProResSelection v;memcpy(&v,q->data,sizeof(v));
        if(v.abi!=FX_PRORES_ABI || v.bytes!=sizeof(v) || v.reserved || v.grade>=4u)result=FX_ARGUMENT;
        else if(s->revoked || atomic_load(&s->fault) || atomic_load(&s->uncertain))result=FX_STATE;
        else if(atomic_load(&s->active))result=FX_BUSY;
        else {atomic_store_explicit(s->value,(uint8_t)v.grade,memory_order_release);++s->revision;status(s,a);}
    } else result=FX_ARGUMENT;
    leave(s);return result;
}
