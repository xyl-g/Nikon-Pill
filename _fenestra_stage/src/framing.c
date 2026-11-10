/* SPDX-License-Identifier: GPL-3.0-only */
#include "fenestra_framing.h"
#include "fenestra_memory.h"
static int enter(FxFramingControl *s) {
    uint32_t zero=0u;
    return atomic_compare_exchange_strong_explicit(&s->gate,&zero,1u,memory_order_acquire,memory_order_relaxed);
}
static void leave(FxFramingControl *s){atomic_store_explicit(&s->gate,0u,memory_order_release);}
void fx_framing_init(FxFramingControl *s,void *context,FxFramingRead read,FxFramingWrite write) {
    memset(s,0,sizeof(*s));s->context=context;s->read=read;s->write=write;
}
static FxResult snapshot(FxFramingControl *s,FxFramingStatus *v) {
    memset(v,0,sizeof(*v));v->abi=FX_FRAMING_ABI;v->bytes=sizeof(*v);
    FxResult result=s->read(s->context,v);
    if(result!=FX_OK)return result;
    if(v->abi!=FX_FRAMING_ABI || v->bytes!=sizeof(*v) || v->nraw>=4u ||
       v->desqueeze>=8u || v->open_gate>1u)return FX_INTEGRITY;
    v->revision=s->revision;
    if(s->revoked)v->flags|=FX_FRAMING_REVOKED;
    return FX_OK;
}
static void reply(const FxFramingStatus *v,FxServiceReply *a) {
    memcpy(a->data,v,sizeof(*v));a->bytes=sizeof(*v);a->value[0]=v->nraw;a->value[1]=v->flags;
}
FxResult fx_framing_resource(FxFramingControl *s,uint32_t op,FxPlatformOwner *o,uint64_t token,
    uint64_t *cookie,const FxServiceRequest *q,FxServiceReply *a) {
    if(!s || !s->read || !s->write)return FX_CAPABILITY;
    if(!o || !token || !cookie)return FX_ARGUMENT;
    if(!enter(s))return FX_BUSY;
    FxResult result=FX_OK;FxFramingStatus v;
    if(op==FX_RESOURCE_ATTACH) {
        if(s->owner)result=FX_BUSY;
        else if((result=snapshot(s,&v))==FX_OK) {
            s->owner=(uintptr_t)o;s->token=token;s->revoked=0u;*cookie=token;
        }
    } else if(*cookie!=token || s->token!=token || s->owner!=(uintptr_t)o)result=FX_STALE;
    else if(op==FX_RESOURCE_PANIC || op==FX_RESOURCE_DETACH) {
        /* Preferences belong to Nikon's bank, and all encoder/display hooks
         * remain core-owned. Releasing the UI never changes a live clip. */
        s->revoked=1u;
        if(op==FX_RESOURCE_DETACH){s->owner=0u;s->token=0u;*cookie=0u;}
    } else if(op!=FX_RESOURCE_CALL || !q || !a || q->arg[3])result=FX_ARGUMENT;
    else if(q->arg[2]==FX_FRAMING_QUERY && !q->bytes) {
        result=snapshot(s,&v);if(result==FX_OK)reply(&v,a);
    } else if(q->arg[2]==FX_FRAMING_SELECT && q->bytes==sizeof(FxFramingSelection)) {
        FxFramingSelection selected;memcpy(&selected,q->data,sizeof(selected));
        if(selected.abi!=FX_FRAMING_ABI || selected.bytes!=sizeof(selected) ||
           selected.control>FX_FRAMING_DESQUEEZE ||
           selected.value>=(selected.control==FX_FRAMING_NRAW?4u:8u))result=FX_ARGUMENT;
        else if(s->revoked)result=FX_STATE;
        else if((result=snapshot(s,&v))==FX_OK) {
            uint32_t capability=selected.control==FX_FRAMING_NRAW?FX_FRAMING_NRAW_AVAILABLE:FX_FRAMING_DESQUEEZE_AVAILABLE;
            if(!(v.capabilities&capability))result=FX_CAPABILITY;
            else if(v.flags&FX_FRAMING_FAULT)result=FX_STATE;
            else if(v.flags&FX_FRAMING_RECORDING)result=FX_BUSY;
            else if((result=s->write(s->context,selected.control,selected.value))==FX_OK) {
                ++s->revision;result=snapshot(s,&v);
                if(result==FX_OK) {
                    if((selected.control==FX_FRAMING_NRAW?v.nraw:v.desqueeze)!=selected.value)result=FX_INTEGRITY;
                    else reply(&v,a);
                }
            }
        }
    } else result=FX_ARGUMENT;
    leave(s);return result;
}
