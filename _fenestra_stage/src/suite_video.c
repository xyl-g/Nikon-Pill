/* SPDX-License-Identifier: GPL-3.0-only
 * Feature-suite control plane. Does not write executable memory, change
 * native hook addresses, or execute card-module code in an original task.
 */
#include "fenestra_suite_video.h"
#include "fenestra_memory.h"
static const uint32_t grades[FX_SUITE_VIDEO_CHANNELS]={4u,5u,3u};
static int enter(FxSuiteVideoControl *s) {
    uint32_t zero=0u;
    return atomic_compare_exchange_strong_explicit(&s->gate,&zero,1u,memory_order_acquire,memory_order_relaxed);
}
static void leave(FxSuiteVideoControl *s){atomic_store_explicit(&s->gate,0u,memory_order_release);}
static void uncertain(FxSuiteVideoControl *s,FxResult error) {
    uint32_t zero=0u;
    (void)atomic_compare_exchange_strong(&s->fault,&zero,(uint32_t)error);
    atomic_store_explicit(&s->uncertain,1u,memory_order_release);
}
void fx_suite_video_init(FxSuiteVideoControl *s,_Atomic uint8_t *prores,_Atomic uint8_t *h265,_Atomic uint8_t *log) {
    memset(s,0,sizeof(*s));s->value[0]=prores;s->value[1]=h265;s->value[2]=log;
}
void fx_suite_video_movie_open(FxSuiteVideoControl *s,uintptr_t context) {
    if(!s)return;
    if(!context){uncertain(s,FX_ARGUMENT);return;}
    /* Neither allocate nor spin on an original camera task. Ambiguous MOV
     * lifetime evidence freezes all SDK selections until the next boot. */
    if(!enter(s)){uncertain(s,FX_BUSY);return;}
    uint32_t slot=FX_SUITE_VIDEO_MOVIES;
    for(uint32_t i=0u;i<FX_SUITE_VIDEO_MOVIES;++i) {
        if(s->movies[i]==context){uncertain(s,FX_INTEGRITY);leave(s);return;}
        if(!s->movies[i])slot=i;
    }
    if(slot==FX_SUITE_VIDEO_MOVIES){uncertain(s,FX_BUDGET);leave(s);return;}
    s->movies[slot]=context;atomic_fetch_add_explicit(&s->active,1u,memory_order_release);leave(s);
}
void fx_suite_video_movie_close(FxSuiteVideoControl *s,uintptr_t context) {
    if(!s || !context)return;
    if(!enter(s)){uncertain(s,FX_BUSY);return;}
    for(uint32_t i=0u;i<FX_SUITE_VIDEO_MOVIES;++i)if(s->movies[i]==context) {
        s->movies[i]=0u;atomic_fetch_sub_explicit(&s->active,1u,memory_order_release);break;
    }
    if(!atomic_load(&s->active) && !atomic_load(&s->uncertain))
        for(uint32_t i=0u;i<FX_SUITE_VIDEO_CHANNELS;++i)if(s->channel[i].revoked)s->channel[i].enabled=0u;
    leave(s);
}
static void status(FxSuiteVideoControl *s,uint32_t index,FxServiceReply *a) {
    FxSuiteVideoChannel *c=&s->channel[index];
    uint32_t selected=atomic_load_explicit(s->value[index],memory_order_acquire),active=atomic_load(&s->active);
    /* Pinned ProRes backend masks the bank byte; H.265 and Log clamp it. */
    selected=index==FX_SUITE_PRORES?selected&3u:selected<grades[index]?selected:0u;
    FxSuiteVideoStatus v={FX_SUITE_VIDEO_ABI,sizeof(v),grades[index],selected,
        (c->enabled?FX_PRORES_ENABLED:0u)|(active?FX_PRORES_RECORDING:0u)|
        (c->revoked?FX_PRORES_REVOKED:0u)|(atomic_load(&s->fault)?FX_PRORES_FAULT:0u),
        active,atomic_load(&s->fault),c->revision};
    memcpy(a->data,&v,sizeof(v));a->bytes=sizeof(v);a->value[0]=v.selected;a->value[1]=v.flags;
}
FxResult fx_suite_video_resource(FxSuiteVideoControl *s,uint32_t index,uint32_t op,FxPlatformOwner *o,uint64_t token,
    uint64_t *cookie,const FxServiceRequest *q,FxServiceReply *a) {
    if(!s || index>=FX_SUITE_VIDEO_CHANNELS || !s->value[index])return FX_CAPABILITY;
    if(!o || !token || !cookie)return FX_ARGUMENT;
    if(!enter(s))return FX_BUSY;
    FxSuiteVideoChannel *c=&s->channel[index];FxResult result=FX_OK;
    if(op==FX_RESOURCE_ATTACH) {
        if(atomic_load(&s->fault) || atomic_load(&s->uncertain))result=FX_STATE;
        else if(atomic_load(&c->owner) || atomic_load(&s->active))result=FX_BUSY;
        else {
            /* Native readers interpret the bank byte themselves. Do not
             * silently rewrite it merely because a copied SDK UI attaches. */
            c->token=token;c->revoked=0u;c->enabled=1u;*cookie=token;
            atomic_store_explicit(&c->owner,(uintptr_t)o,memory_order_release);
        }
    } else if(*cookie!=token || c->token!=token || atomic_load(&c->owner)!=(uintptr_t)o)result=FX_STALE;
    else if(op==FX_RESOURCE_PANIC || op==FX_RESOURCE_DETACH) {
        c->revoked=1u;
        if(atomic_load(&s->active) || atomic_load(&s->uncertain))result=op==FX_RESOURCE_PANIC?FX_OK:FX_BUSY;
        else {
            c->enabled=0u;
            if(op==FX_RESOURCE_DETACH){atomic_store(&c->owner,0u);c->token=0u;*cookie=0u;}
        }
        /* Detach releases the copied SDK lease. The suite's native menu and
         * bank values persist, exactly as when closing the native menu. */
    } else if(op!=FX_RESOURCE_CALL || !q || !a || q->arg[3])result=FX_ARGUMENT;
    else if(q->arg[2]==FX_SUITE_VIDEO_QUERY && !q->bytes)status(s,index,a);
    else if(q->arg[2]==FX_SUITE_VIDEO_SELECT && q->bytes==sizeof(FxSuiteVideoSelection)) {
        FxSuiteVideoSelection v;memcpy(&v,q->data,sizeof(v));
        if(v.abi!=FX_SUITE_VIDEO_ABI || v.bytes!=sizeof(v) || v.reserved || v.selected>=grades[index])result=FX_ARGUMENT;
        else if(c->revoked || atomic_load(&s->fault) || atomic_load(&s->uncertain))result=FX_STATE;
        else if(atomic_load(&s->active))result=FX_BUSY;
        else {atomic_store_explicit(s->value[index],(uint8_t)v.selected,memory_order_release);++c->revision;status(s,index,a);}
    } else result=FX_ARGUMENT;
    leave(s);return result;
}
