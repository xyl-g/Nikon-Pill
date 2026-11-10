/* SPDX-License-Identifier: GPL-3.0-only
 * Five named control providers, native profile and lifecycle owned by a sealed
 * resident EL1 capsule. The core contains no feature state or feature address.
 */
#include "internal.h"
#include "fenestra_native_locale.h"
#include "fx_control_dependencies.h"
typedef struct FxControlsState FxControlsState;
typedef struct {FxControlsState *state;uint32_t service;} ProviderContext;
struct FxControlsState {
    FxNativePackHost host;FxControlsProfile profile;
    _Atomic uint32_t published,revoked,gate,entered;
    FxSuiteVideoControl video;FxFramingControl framing;FxPhotoToolsControl photo;
    FxControlsPhotoBridge photo_bridge;FxControlsDcfSubscription dcf_subscription;
    FxNativePackProvider providers[5];ProviderContext contexts[5];
    FxNativePackSymbol symbols[8];uint32_t provider_count,native_calls;
};
static _Atomic uintptr_t current;
uintptr_t fx_controls_upstream_close;
static uint64_t ticks(void){
#if defined(__aarch64__)
    uint64_t n;__asm__ volatile("mrs %0,cntvct_el0":"=r"(n));return n;
#else
    return 0u;
#endif
}
static uint64_t tick_rate(void){
#if defined(__aarch64__)
    uint64_t n;__asm__ volatile("mrs %0,cntfrq_el0":"=r"(n));return n&UINT32_MAX;
#else
    return 0u;
#endif
}
static int lock(_Atomic uint32_t *p){uint32_t z=0u;return atomic_compare_exchange_strong_explicit(p,&z,1u,memory_order_acquire,memory_order_relaxed);}
static void unlock(_Atomic uint32_t *p){atomic_store_explicit(p,0u,memory_order_release);}
static void text(char *dst,const char *src,uint32_t n){uint32_t i=0;for(;i+1u<n&&src[i];++i)dst[i]=src[i];dst[i]=0;}
static uint32_t busy(FxControlsState *s){return atomic_load(&s->video.active)||atomic_load(&s->video.uncertain)||atomic_load(&s->video.fault)||atomic_load(&s->video.gate);}
static FxResult framing_read(void *context,FxFramingStatus *v){
    FxControlsState *s=context;
    static const uint8_t grade[]={FX_FRAMING_NORMAL,FX_FRAMING_HIGH,FX_FRAMING_FINE,FX_FRAMING_BASIC};
    uint32_t n=__atomic_load_n((uint32_t *)s->profile.nraw,__ATOMIC_ACQUIRE);
    uint32_t ap=atomic_load_explicit(s->profile.desqueeze,memory_order_acquire),og=atomic_load_explicit(s->profile.open_gate,memory_order_acquire);
    if(n>=4u||ap>=8u||og>1u)return FX_INTEGRITY;
    v->capabilities=FX_FRAMING_NRAW_AVAILABLE|FX_FRAMING_DESQUEEZE_AVAILABLE|FX_FRAMING_NATIVE_OPEN_GATE;
    v->flags=busy(s)?FX_FRAMING_RECORDING:0u;v->nraw=grade[n];v->desqueeze=ap;v->open_gate=og;
    v->rate=s->native_calls?((uint32_t (*)(uint32_t))s->profile.exact_get)(UINT32_C(0x0500125b)):0u;return FX_OK;
}
static FxResult framing_write(void *context,uint32_t control,uint32_t value){
    FxControlsState *s=context;if(busy(s))return FX_BUSY;
    if(control==FX_FRAMING_NRAW&&value<4u){
        static const uint8_t native[]={1u,2u,0u,3u};
        uint32_t r=((uint32_t (*)(uint32_t,uint32_t))s->profile.set)(UINT32_C(0x05001261),native[value]);return r&1u?FX_OK:FX_STATE;
    }
    if(control==FX_FRAMING_DESQUEEZE&&value<8u){atomic_store_explicit(s->profile.desqueeze,(uint8_t)value,memory_order_release);return FX_OK;}
    return FX_ARGUMENT;
}
static uint32_t pending(void *context){
    FxControlsState *s=context;if(!s)return 0u;
    return atomic_load_explicit(&s->entered,memory_order_acquire)||atomic_load(&s->gate)||busy(s)||
        ((FX_CONTROL_FEATURES&FX_CONTROLS_PHOTO)&&(atomic_load(&s->photo.running)||atomic_load(&s->photo.fault)));
}
static FxResult resource(void *context,uint32_t op,uint64_t owner_key,uint64_t token,uint64_t *cookie,
                         const FxServiceRequest *q,FxServiceReply *a){
    ProviderContext *p=context;FxControlsState *s=p?p->state:0;
    if(!s||!owner_key||!token||!cookie)return FX_ARGUMENT;
    atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);
    FxResult r=FX_STATE;
    if(!atomic_load_explicit(&s->published,memory_order_acquire))goto out;
    if(!lock(&s->gate)){r=FX_BUSY;goto out;}
    uint32_t revoked=atomic_load_explicit(&s->revoked,memory_order_acquire);
    if(revoked&&(op==FX_NATIVE_PACK_ATTACH||(op==FX_NATIVE_PACK_CALL&&q&&
        ((p->service<3u&&q->arg[2]==FX_SUITE_VIDEO_SELECT)||
         (p->service==3u&&q->arg[2]==FX_FRAMING_SELECT)||(p->service==4u&&q->arg[2]==FX_PHOTO_TOOLS_START))))){r=FX_STATE;goto release;}
    /* Existing portable controllers use this solely as an opaque identity; no
     * core owner object or module pointer is read through the converted value. */
    FxPlatformOwner *owner=(FxPlatformOwner *)(uintptr_t)owner_key;
    s->native_calls=op==FX_NATIVE_PACK_CALL;
    if(p->service<3u){
        if(FX_CONTROL_FEATURES&(FX_CONTROLS_VIDEO|FX_CONTROLS_LOG)){
        r=fx_suite_video_resource(&s->video,p->service,op,owner,token,cookie,q,a);
        if(r==FX_OK&&op==FX_NATIVE_PACK_CALL&&q&&q->arg[2]==FX_SUITE_VIDEO_SELECT&&s->profile.tier_republish)
            ((void (*)(void))s->profile.tier_republish)();
        }else r=FX_CAPABILITY;
    }else if(p->service==3u){
        if(FX_CONTROL_FEATURES&FX_CONTROLS_FRAMING)r=fx_framing_resource(&s->framing,op,owner,token,cookie,q,a);else r=FX_CAPABILITY;
    }
    else if(p->service==4u){
        if(!(FX_CONTROL_FEATURES&FX_CONTROLS_PHOTO))r=FX_CAPABILITY;
        else if(op==FX_NATIVE_PACK_CALL&&q&&q->arg[2]==FX_PHOTO_TOOLS_START&&busy(s))r=FX_BUSY;
        else r=fx_photo_tools_resource(&s->photo,op,owner,token,cookie,q,a);
    }
    else r=FX_ARGUMENT;
release:s->native_calls=0u;unlock(&s->gate);
out:atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);return r;
}
static FxResult event(void *context,uint32_t op){
    FxControlsState *s=context;if(!s)return FX_ARGUMENT;
    if(op==FX_NATIVE_PACK_PUBLISH){
        /* No allocation, original function, failure, or reopening after STOP.
         * Generic host contract: PUBLISH is still inside the parked epoch. */
        if(FX_CONTROL_FEATURES&FX_CONTROLS_PHOTO)fx_controls_precapture_publish(&s->dcf_subscription);
        atomic_store_explicit(&s->published,1u,memory_order_release);return FX_OK;
    }
    if(op==FX_NATIVE_PACK_ABORT){
        if(atomic_load(&s->published)||atomic_load(&s->entered)||atomic_load(&s->gate))return FX_BUSY;
        FxNativePackHost host=s->host;atomic_store_explicit(&current,0u,memory_order_release);fx_controls_upstream_close=0u;
        host.release(host.context,s);return FX_OK;
    }
    if(!atomic_load_explicit(&s->published,memory_order_acquire))return FX_STATE;
    if(op==FX_NATIVE_PACK_GUI_POLL){
        atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);
        if(FX_CONTROL_FEATURES&FX_CONTROLS_PHOTO)fx_photo_tools_poll(&s->photo);
        atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);return FX_OK;
    }
    if(op==FX_NATIVE_PACK_STOP||op==FX_NATIVE_PACK_FAULT){
        atomic_store_explicit(&s->revoked,1u,memory_order_release);
        /* Cancel must reach native RAM even if original GUI polling has stopped. */
        if(FX_CONTROL_FEATURES&FX_CONTROLS_PHOTO)(void)fx_controls_photo_stop(&s->photo);
        if(!lock(&s->gate))return FX_BUSY;
        if(!lock(&s->video.gate)){unlock(&s->gate);return FX_BUSY;}
        for(uint32_t i=0;i<3u;++i){s->video.channel[i].revoked=1u;if(!atomic_load(&s->video.active))s->video.channel[i].enabled=0u;}
        unlock(&s->video.gate);
        if(lock(&s->framing.gate)){s->framing.revoked=1u;unlock(&s->framing.gate);}else {unlock(&s->gate);return FX_BUSY;}
        unlock(&s->gate);return pending(s)?FX_BUSY:FX_OK;
    }
    if(op==FX_NATIVE_PACK_RESUME){
        if(!atomic_load(&s->revoked)||pending(s)||!lock(&s->gate))return FX_BUSY;
        uint32_t owner=!!s->framing.owner||!!atomic_load(&s->photo.owner);
        for(uint32_t i=0;i<3u;++i)owner|=!!atomic_load(&s->video.channel[i].owner);
        FxResult r=owner?FX_BUSY:FX_OK;
        if(FX_CONTROL_FEATURES&FX_CONTROLS_PHOTO){if(r==FX_OK)r=fx_controls_photo_resume(&s->photo);}
        if(r==FX_OK){s->framing.revoked=0u;atomic_store_explicit(&s->revoked,0u,memory_order_release);}
        unlock(&s->gate);return r;
    }
    return FX_ARGUMENT;
}
void fx_controls_movie_open(uintptr_t native_context){
    FxControlsState *s=(void *)atomic_load_explicit(&current,memory_order_acquire);
    if(!s||!atomic_load_explicit(&s->published,memory_order_acquire))return;
    atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);fx_suite_video_movie_open(&s->video,native_context);
    atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);
}
void fx_controls_movie_close(uintptr_t native_context){
    FxControlsState *s=(void *)atomic_load_explicit(&current,memory_order_acquire);
    if(!s||!atomic_load_explicit(&s->published,memory_order_acquire))return;
    atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);fx_suite_video_movie_close(&s->video,native_context);
    atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);
}
static void provider(FxControlsState *s,const char *name,uint32_t service){
    uint32_t n=s->provider_count++;FxNativePackProvider *p=&s->providers[n];text(p->name,name,sizeof(p->name));
    p->version=1u;p->kind=FX_NATIVE_PACK_DEVICE;p->call_abi=FX_NATIVE_PACK_CALL_DEVICE;
    p->capabilities=FX_SDK_NATIVE;p->call_context=FX_NATIVE_PACK_ORIGINAL_UI;p->resource=resource;
    s->contexts[n]=(ProviderContext){s,service};p->context=&s->contexts[n];
}

FxResult fx_controls_pack_entry(const FxNativePackHost *host,FxNativePackExports *out){
    if(!host||!out||host->abi!=FX_NATIVE_PACK_ABI||host->bytes<sizeof(*host)||!host->allocate||!host->release)return FX_ARGUMENT;
    if(atomic_load_explicit(&current,memory_order_acquire))return FX_STATE;
    FxControlsProfile p={
        .prores=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c029),.h265=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c269),
        .log=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c0dd),.nraw=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c290),
        .desqueeze=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c0ad),.open_gate=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c02b),
        .psm_runtime=(uint8_t *)(uintptr_t)UINT64_C(0xa0ffed000),.task_registry=(const uint8_t *)(uintptr_t)UINT64_C(0xa03203e08),
        .allocate=UINT64_C(0x40d2c408),.release=UINT64_C(0x40d2c448),.task_create=UINT64_C(0x40d2d498),.task_start=UINT64_C(0x40d2d5cc),
        .get=UINT64_C(0x403ec398),.exact_get=UINT64_C(0x410d5ec8),.set=UINT64_C(0x403ec39c),
        .dcf_constructor=(const uint8_t *)(uintptr_t)UINT64_C(0x4086e900),.dcf_registration=(const uint8_t *)(uintptr_t)UINT64_C(0x41013ca0),.dcf_bank=(uint8_t *)(uintptr_t)UINT64_C(0xa03bddd20),
        .psm_worker=FX_CONTROL_PSM_WORKER,.tier_republish=FX_CONTROL_TIER_REPUBLISH,.upstream_close=FX_CONTROL_UPSTREAM_CLOSE,
        .timer_kind=UINT64_C(0x403884a0),.timer_hold=UINT64_C(0x403884b8),.timer_set=UINT64_C(0x40388300),.ticks=ticks,.tick_rate=tick_rate};
#ifdef FX_CONTROLS_TESTING
    p=fx_controls_test_profile;
#endif

    FxControlsState *s=host->allocate(host->context,sizeof(*s),16u);if(!s)return FX_BUDGET;
    if((uintptr_t)s&15u){host->release(host->context,s);return FX_INTEGRITY;}memset(s,0,sizeof(*s));s->host=*host;s->profile=p;
    if(FX_CONTROL_FEATURES&FX_CONTROLS_PHOTO){FxResult dcf=fx_controls_precapture_stage(&s->dcf_subscription,&s->profile);if(dcf!=FX_OK){host->release(host->context,s);return dcf;}}
    fx_suite_video_init(&s->video,p.prores,p.h265,p.log);
    if(FX_CONTROL_FEATURES&FX_CONTROLS_FRAMING)fx_framing_init(&s->framing,s,framing_read,framing_write);
    if(FX_CONTROL_FEATURES&FX_CONTROLS_PHOTO)fx_controls_photo_init(&s->photo,&s->photo_bridge,&s->profile);
    if(FX_CONTROL_FEATURES&FX_CONTROLS_VIDEO){provider(s,"camera.video.prores",0u);provider(s,"camera.video.h265",1u);}
    if(FX_CONTROL_FEATURES&FX_CONTROLS_LOG)provider(s,"camera.video.log_colour",2u);
    if(FX_CONTROL_FEATURES&FX_CONTROLS_FRAMING)provider(s,"camera.video.framing",3u);
    if(FX_CONTROL_FEATURES&FX_CONTROLS_PHOTO)provider(s,"camera.photo.tools",4u);
    const char *names[]={"controls.movie_open","controls.movie_close","controls.video_open_hook","controls.video_close_hook"};
    uintptr_t addresses[]={(uintptr_t)fx_controls_movie_open,(uintptr_t)fx_controls_movie_close,(uintptr_t)fx_controls_video_open_hook,(uintptr_t)fx_controls_video_close_hook};
    for(uint32_t i=0;i<4u;++i){text(s->symbols[i].name,names[i],sizeof(s->symbols[i].name));s->symbols[i].version=1u;s->symbols[i].flags=FX_NATIVE_PACK_SYMBOL_FUNCTION;s->symbols[i].address=addresses[i];}
    for(uint32_t i=0;i<FX_CONTROL_DEPENDENCY_COUNT;++i)s->symbols[4u+i]=fx_controls_dependency_table[i];
    s->symbols[4u+FX_CONTROL_DEPENDENCY_COUNT]=fx_controls_locale_export;
    fx_controls_upstream_close=p.upstream_close;atomic_store_explicit(&current,(uintptr_t)s,memory_order_release);
    *out=(FxNativePackExports){.abi=FX_NATIVE_PACK_ABI,.bytes=sizeof(*out),.provider_count=s->provider_count,.symbol_count=5u+FX_CONTROL_DEPENDENCY_COUNT,
        .providers=s->providers,.symbols=s->symbols,.context=s,.event=event,.pending=pending};return FX_OK;
}
const uint32_t fx_controls_metadata[]={UINT32_C(0x46584e50),FX_NATIVE_PACK_ABI,FX_CONTROL_FEATURES,sizeof(FxNativePackHost),sizeof(FxNativePackExports),
    sizeof(FxNativePackProvider),sizeof(FxNativePackSymbol),sizeof(FxControlsState),sizeof(FxSuiteVideoControl),sizeof(FxFramingControl),sizeof(FxPhotoToolsControl)};
