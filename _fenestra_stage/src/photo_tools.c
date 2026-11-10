/* SPDX-License-Identifier: GPL-3.0-only */
#include "fenestra_photo_tools.h"
#include "fenestra_memory.h"
static int enter(FxPhotoToolsControl *s){uint32_t z=0;return atomic_compare_exchange_strong_explicit(&s->gate,&z,1u,memory_order_acquire,memory_order_relaxed);}
static void leave(FxPhotoToolsControl *s){atomic_store_explicit(&s->gate,0u,memory_order_release);}
void fx_photo_tools_init(FxPhotoToolsControl *s,uint32_t supported,const FxPhotoToolsOps *ops){
    memset(s,0,sizeof(*s));s->supported=supported&(FX_PHOTO_PRECAPTURE|FX_PHOTO_MERGE);s->ops=ops;
}
void fx_photo_tools_finish(FxPhotoToolsControl *s,uint32_t status,uint32_t groups,uint32_t targets,uint32_t merged,uint32_t attempted,uint32_t seconds){
    atomic_store(&s->status,status);atomic_store(&s->groups,groups);atomic_store(&s->targets,targets);
    atomic_store(&s->merged,merged);atomic_store(&s->attempted,attempted);atomic_store(&s->seconds,seconds);
    atomic_store_explicit(&s->running,0u,memory_order_release);
}
void fx_photo_tools_poll(FxPhotoToolsControl *s){if(s&&s->ops&&s->ops->poll)s->ops->poll(s);}
static void snapshot(FxPhotoToolsControl *s,FxServiceReply *a){
    FxPhotoToolsStatus v={.abi=FX_PHOTO_TOOLS_ABI,.bytes=sizeof(v),.flags=s->supported,.serial=s->serial,.mode=s->mode};
    if(atomic_load_explicit(&s->running,memory_order_acquire))v.flags|=FX_PHOTO_RUNNING;
    if(atomic_load(&s->cancel))v.flags|=FX_PHOTO_CANCELLING;
    if(atomic_load(&s->revoked))v.flags|=FX_PHOTO_REVOKED;
    if(atomic_load(&s->fault))v.flags|=FX_PHOTO_FAULT;
    v.status=atomic_load(&s->status);v.phase=atomic_load(&s->phase);v.first=atomic_load(&s->first);v.second=atomic_load(&s->second);
    v.rows=atomic_load(&s->rows);v.total=atomic_load(&s->total);v.groups=atomic_load(&s->groups);v.targets=atomic_load(&s->targets);
    v.merged=atomic_load(&s->merged);v.attempted=atomic_load(&s->attempted);v.seconds=atomic_load(&s->seconds);
    if(s->ops&&s->ops->snapshot)s->ops->snapshot(s,&v);
    memcpy(a->data,&v,sizeof(v));a->bytes=sizeof(v);a->value[0]=v.serial;a->value[1]=v.flags;
}
FxResult fx_photo_tools_resource(FxPhotoToolsControl *s,uint32_t op,FxPlatformOwner *o,uint64_t token,uint64_t *cookie,const FxServiceRequest *q,FxServiceReply *a){
    if(!s||!s->supported||!s->ops)return FX_CAPABILITY;
    if(!o||!token||!cookie)return FX_ARGUMENT;
    if(!enter(s))return FX_BUSY;
    FxResult r=FX_OK;
    if(op==FX_RESOURCE_ATTACH){
        if(atomic_load(&s->owner)||atomic_load(&s->running))r=FX_BUSY;
        else if(atomic_load(&s->fault))r=FX_STATE;
        else {s->token=token;*cookie=token;atomic_store(&s->revoked,0u);atomic_store(&s->owner,(uintptr_t)o);}
    }else if(*cookie!=token||s->token!=token||atomic_load(&s->owner)!=(uintptr_t)o)r=FX_STALE;
    else if(op==FX_RESOURCE_DETACH||op==FX_RESOURCE_PANIC){
        atomic_store(&s->revoked,1u);atomic_store(&s->cancel,1u);
        if(atomic_load(&s->running)||atomic_load(&s->fault))r=op==FX_RESOURCE_PANIC?FX_OK:FX_BUSY;
        else if(op==FX_RESOURCE_DETACH){atomic_store(&s->owner,0u);s->token=0;*cookie=0;}
    }else if(op!=FX_RESOURCE_CALL||!q||!a||q->arg[3])r=FX_ARGUMENT;
    else if(q->arg[2]==FX_PHOTO_TOOLS_QUERY&&!q->bytes)snapshot(s,a);
    else if(q->arg[2]==FX_PHOTO_TOOLS_CANCEL&&!q->bytes){atomic_store(&s->cancel,1u);snapshot(s,a);}
    else if(q->arg[2]==FX_PHOTO_TOOLS_START&&q->bytes==sizeof(FxPhotoToolsStart)){
        FxPhotoToolsStart v;memcpy(&v,q->data,sizeof(v));
        if(v.abi!=FX_PHOTO_TOOLS_ABI||v.bytes!=sizeof(v)||v.mode>FX_PHOTO_ALL||v.confirm!=FX_PHOTO_TOOLS_CONFIRM)r=FX_ARGUMENT;
        else if(!(s->supported&FX_PHOTO_MERGE))r=FX_CAPABILITY;
        else if(atomic_load(&s->revoked)||atomic_load(&s->fault)||s->serial==UINT32_MAX)r=FX_STATE;
        else if(atomic_load(&s->running))r=FX_BUSY;
        else {
            ++s->serial;s->mode=v.mode;atomic_store(&s->cancel,0u);atomic_store(&s->status,0u);
            atomic_store(&s->phase,0u);atomic_store(&s->rows,0u);atomic_store(&s->total,0u);
            atomic_store(&s->groups,0u);atomic_store(&s->targets,0u);atomic_store(&s->merged,0u);atomic_store(&s->attempted,0u);atomic_store(&s->seconds,0u);
            atomic_store_explicit(&s->running,1u,memory_order_release);
            r=s->ops->start?s->ops->start(s):FX_CAPABILITY;
            if(r!=FX_OK)atomic_store_explicit(&s->running,0u,memory_order_release);
            else snapshot(s,a);
        }
    }else r=FX_ARGUMENT;
    leave(s);return r;
}
