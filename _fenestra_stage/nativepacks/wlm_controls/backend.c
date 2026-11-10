/* SPDX-License-Identifier: GPL-3.0-only
 * One WLM owns one provider, its native control code and its movie lifetime
 * tracker. This translation unit is compiled once per independent component.
 * There is no aggregate backend and no function address in the generic core.
 */
#include "wlm_controls.h"
#include "fx_wlm_dependencies.h"
#if FX_WLM_FEATURE == FX_WLM_PRORES
#define FEATURE_NAME "prores"
#define SERVICE_NAME "camera.video.prores"
#define VIDEO_CHANNEL FX_SUITE_PRORES
#elif FX_WLM_FEATURE == FX_WLM_H265
#define FEATURE_NAME "h265"
#define SERVICE_NAME "camera.video.h265"
#define VIDEO_CHANNEL FX_SUITE_H265
#elif FX_WLM_FEATURE == FX_WLM_LOG_COLOUR
#define FEATURE_NAME "log_colour"
#define SERVICE_NAME "camera.video.log_colour"
#define VIDEO_CHANNEL FX_SUITE_LOG_COLOUR
#elif FX_WLM_FEATURE == FX_WLM_FRAMING
#define FEATURE_NAME "framing"
#define SERVICE_NAME "camera.video.framing"
#else
#define FEATURE_NAME "photo_tools"
#define SERVICE_NAME "camera.photo.tools"
#endif

typedef struct {
    FxNativePackHost host;
    FxControlsProfile profile;
    _Atomic uint32_t published,revoked,gate,entered;
    FxSuiteVideoControl video;
#if FX_WLM_FEATURE == FX_WLM_FRAMING
    FxFramingControl framing;
#elif FX_WLM_FEATURE == FX_WLM_PHOTO_TOOLS
    FxPhotoToolsControl photo;
    FxControlsPhotoBridge photo_bridge;
    FxControlsDcfSubscription dcf_subscription;
#endif
    FxNativePackProvider provider;
    FxNativePackSymbol symbols[3];
    uint32_t native_calls;
} FxWlmControlState;
static _Atomic uintptr_t current;
static int lock(_Atomic uint32_t *p){uint32_t z=0u;return atomic_compare_exchange_strong_explicit(p,&z,1u,memory_order_acquire,memory_order_relaxed);}
static void unlock(_Atomic uint32_t *p){atomic_store_explicit(p,0u,memory_order_release);}
static void text(char *dst,const char *src,uint32_t n){uint32_t i=0u;for(;i+1u<n&&src[i];++i)dst[i]=src[i];dst[i]=0;}
static uint32_t busy(FxWlmControlState *s){return atomic_load(&s->video.active)||atomic_load(&s->video.uncertain)||atomic_load(&s->video.fault)||atomic_load(&s->video.gate);}
#if FX_WLM_FEATURE == FX_WLM_PHOTO_TOOLS
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
#endif
#if FX_WLM_FEATURE == FX_WLM_FRAMING
static FxResult framing_read(void *context,FxFramingStatus *v){
    FxWlmControlState *s=context;
    static const uint8_t grade[]={FX_FRAMING_NORMAL,FX_FRAMING_HIGH,FX_FRAMING_FINE,FX_FRAMING_BASIC};
    uint32_t n=__atomic_load_n((uint32_t *)s->profile.nraw,__ATOMIC_ACQUIRE);
    uint32_t ap=atomic_load_explicit(s->profile.desqueeze,memory_order_acquire),og=atomic_load_explicit(s->profile.open_gate,memory_order_acquire);
    if(n>=4u||ap>=8u||og>1u)return FX_INTEGRITY;
    v->capabilities=FX_FRAMING_NRAW_AVAILABLE|FX_FRAMING_DESQUEEZE_AVAILABLE|FX_FRAMING_NATIVE_OPEN_GATE;
    v->flags=busy(s)?FX_FRAMING_RECORDING:0u;v->nraw=grade[n];v->desqueeze=ap;v->open_gate=og;
    v->rate=s->native_calls?((uint32_t (*)(uint32_t))s->profile.exact_get)(UINT32_C(0x0500125b)):0u;return FX_OK;
}
static FxResult framing_write(void *context,uint32_t control,uint32_t value){
    FxWlmControlState *s=context;if(busy(s))return FX_BUSY;
    if(control==FX_FRAMING_NRAW&&value<4u){static const uint8_t native[]={1u,2u,0u,3u};
        uint32_t r=((uint32_t (*)(uint32_t,uint32_t))s->profile.set)(UINT32_C(0x05001261),native[value]);return r&1u?FX_OK:FX_STATE;}
    if(control==FX_FRAMING_DESQUEEZE&&value<8u){atomic_store_explicit(s->profile.desqueeze,(uint8_t)value,memory_order_release);return FX_OK;}
    return FX_ARGUMENT;
}
#endif
static uint32_t pending(void *context){
    FxWlmControlState *s=context;if(!s)return 0u;
    return atomic_load_explicit(&s->entered,memory_order_acquire)||atomic_load(&s->gate)||busy(s)
#if FX_WLM_FEATURE == FX_WLM_PHOTO_TOOLS
        ||atomic_load(&s->photo.running)||atomic_load(&s->photo.fault)
#endif
        ;
}
static FxResult resource(void *context,uint32_t op,uint64_t owner_key,uint64_t token,uint64_t *cookie,
                         const FxServiceRequest *q,FxServiceReply *a){
    FxWlmControlState *s=context;if(!s||!owner_key||!token||!cookie)return FX_ARGUMENT;
    atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);FxResult r=FX_STATE;
    if(!atomic_load_explicit(&s->published,memory_order_acquire))goto out;
    if(!lock(&s->gate)){r=FX_BUSY;goto out;}
    if(atomic_load_explicit(&s->revoked,memory_order_acquire)&&(op==FX_NATIVE_PACK_ATTACH||
       (op==FX_NATIVE_PACK_CALL&&q&&q->arg[2]==1u))){r=FX_STATE;goto release;}
    FxPlatformOwner *owner=(FxPlatformOwner *)(uintptr_t)owner_key;
    s->native_calls=op==FX_NATIVE_PACK_CALL;
#if FX_WLM_FEATURE == FX_WLM_PRORES || FX_WLM_FEATURE == FX_WLM_H265 || FX_WLM_FEATURE == FX_WLM_LOG_COLOUR
    r=fx_suite_video_resource(&s->video,VIDEO_CHANNEL,op,owner,token,cookie,q,a);
#if FX_WLM_FEATURE == FX_WLM_PRORES || FX_WLM_FEATURE == FX_WLM_H265
    if(r==FX_OK&&op==FX_NATIVE_PACK_CALL&&q&&q->arg[2]==FX_SUITE_VIDEO_SELECT)
        ((void (*)(void))s->profile.tier_republish)();
#endif
#elif FX_WLM_FEATURE == FX_WLM_FRAMING
    r=fx_framing_resource(&s->framing,op,owner,token,cookie,q,a);
#else
    if(op==FX_NATIVE_PACK_CALL&&q&&q->arg[2]==FX_PHOTO_TOOLS_START&&busy(s))r=FX_BUSY;
    else r=fx_photo_tools_resource(&s->photo,op,owner,token,cookie,q,a);
#endif
release:s->native_calls=0u;unlock(&s->gate);
out:atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);return r;
}
static FxResult event(void *context,uint32_t op){
    FxWlmControlState *s=context;if(!s)return FX_ARGUMENT;
    if(op==FX_NATIVE_PACK_PUBLISH){
#if FX_WLM_FEATURE == FX_WLM_PHOTO_TOOLS
        fx_controls_precapture_publish(&s->dcf_subscription);
#endif
        atomic_store_explicit(&s->published,1u,memory_order_release);return FX_OK;
    }
    if(op==FX_NATIVE_PACK_ABORT){
        if(atomic_load(&s->published)||atomic_load(&s->entered)||atomic_load(&s->gate))return FX_BUSY;
        FxNativePackHost host=s->host;atomic_store_explicit(&current,0u,memory_order_release);host.release(host.context,s);return FX_OK;
    }
    if(!atomic_load_explicit(&s->published,memory_order_acquire))return FX_STATE;
    if(op==FX_NATIVE_PACK_GUI_POLL){
        atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);
#if FX_WLM_FEATURE == FX_WLM_PHOTO_TOOLS
        fx_photo_tools_poll(&s->photo);
#endif
        atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);return FX_OK;
    }
    if(op==FX_NATIVE_PACK_STOP||op==FX_NATIVE_PACK_FAULT){
        atomic_store_explicit(&s->revoked,1u,memory_order_release);
#if FX_WLM_FEATURE == FX_WLM_PHOTO_TOOLS
        (void)fx_controls_photo_stop(&s->photo);
#endif
        if(!lock(&s->gate))return FX_BUSY;
        if(!lock(&s->video.gate)){unlock(&s->gate);return FX_BUSY;}
        for(uint32_t i=0u;i<FX_SUITE_VIDEO_CHANNELS;++i){s->video.channel[i].revoked=1u;if(!atomic_load(&s->video.active))s->video.channel[i].enabled=0u;}
        unlock(&s->video.gate);
#if FX_WLM_FEATURE == FX_WLM_FRAMING
        if(lock(&s->framing.gate)){s->framing.revoked=1u;unlock(&s->framing.gate);}else {unlock(&s->gate);return FX_BUSY;}
#endif
        unlock(&s->gate);return pending(s)?FX_BUSY:FX_OK;
    }
    if(op==FX_NATIVE_PACK_RESUME){
        /* Multi-entry resume may retry a prefix whose members already ACKed. */
        if(!atomic_load_explicit(&s->revoked,memory_order_acquire))return FX_OK;
        if(pending(s)||!lock(&s->gate))return FX_BUSY;
        uint32_t owner=0u;
        for(uint32_t i=0u;i<FX_SUITE_VIDEO_CHANNELS;++i)owner|=!!atomic_load(&s->video.channel[i].owner);
#if FX_WLM_FEATURE == FX_WLM_FRAMING
        owner|=!!s->framing.owner;
#elif FX_WLM_FEATURE == FX_WLM_PHOTO_TOOLS
        owner|=!!atomic_load(&s->photo.owner);
#endif
        FxResult r=owner?FX_BUSY:FX_OK;
#if FX_WLM_FEATURE == FX_WLM_PHOTO_TOOLS
        if(r==FX_OK)r=fx_controls_photo_resume(&s->photo);
#elif FX_WLM_FEATURE == FX_WLM_FRAMING
        if(r==FX_OK)s->framing.revoked=0u;
#endif
        if(r==FX_OK)atomic_store_explicit(&s->revoked,0u,memory_order_release);
        unlock(&s->gate);return r;
    }
    return FX_ARGUMENT;
}
void fx_wlm_movie_open(uintptr_t native_context){
    FxWlmControlState *s=(void *)atomic_load_explicit(&current,memory_order_acquire);
    if(!s||!atomic_load_explicit(&s->published,memory_order_acquire))return;
    atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);fx_suite_video_movie_open(&s->video,native_context);
    atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);
}
void fx_wlm_movie_close(uintptr_t native_context){
    FxWlmControlState *s=(void *)atomic_load_explicit(&current,memory_order_acquire);
    if(!s||!atomic_load_explicit(&s->published,memory_order_acquire))return;
    atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);fx_suite_video_movie_close(&s->video,native_context);
    atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);
}
FxResult fx_wlm_pack_entry(const FxNativePackHost *host,FxNativePackExports *out){
    if(!host||!out||host->abi!=FX_NATIVE_PACK_ABI||host->bytes<sizeof(*host)||!host->allocate||!host->release)return FX_ARGUMENT;
    if(atomic_load_explicit(&current,memory_order_acquire))return FX_STATE;
    FxControlsProfile p={0};
#if FX_WLM_FEATURE == FX_WLM_PRORES
    p.prores=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c029);p.tier_republish=FX_WLM_TIER_REPUBLISH;
#elif FX_WLM_FEATURE == FX_WLM_H265
    p.h265=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c269);p.tier_republish=FX_WLM_TIER_REPUBLISH;
#elif FX_WLM_FEATURE == FX_WLM_LOG_COLOUR
    p.log=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c0dd);
#elif FX_WLM_FEATURE == FX_WLM_FRAMING
    p.nraw=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c290);p.desqueeze=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c0ad);
    p.open_gate=(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c02b);p.exact_get=UINT64_C(0x410d5ec8);p.set=UINT64_C(0x403ec39c);
#else
    p.psm_runtime=(uint8_t *)(uintptr_t)UINT64_C(0xa0ffed000);p.task_registry=(const uint8_t *)(uintptr_t)UINT64_C(0xa03203e08);
    p.allocate=UINT64_C(0x40d2c408);p.release=UINT64_C(0x40d2c448);p.task_create=UINT64_C(0x40d2d498);p.task_start=UINT64_C(0x40d2d5cc);
    p.get=UINT64_C(0x403ec398);p.set=UINT64_C(0x403ec39c);p.timer_kind=UINT64_C(0x403884a0);p.timer_hold=UINT64_C(0x403884b8);p.timer_set=UINT64_C(0x40388300);
    p.dcf_constructor=(const uint8_t *)(uintptr_t)UINT64_C(0x4086e900);p.dcf_registration=(const uint8_t *)(uintptr_t)UINT64_C(0x41013ca0);
    p.dcf_bank=(uint8_t *)(uintptr_t)UINT64_C(0xa03bddd20);p.psm_worker=FX_WLM_PSM_WORKER;p.ticks=ticks;p.tick_rate=tick_rate;
#endif
#ifdef FX_CONTROLS_TESTING
    p=fx_controls_test_profile;
#endif
    FxWlmControlState *s=host->allocate(host->context,sizeof(*s),16u);if(!s)return FX_BUDGET;
    if((uintptr_t)s&15u){host->release(host->context,s);return FX_INTEGRITY;}memset(s,0,sizeof(*s));s->host=*host;s->profile=p;
#if FX_WLM_FEATURE == FX_WLM_PHOTO_TOOLS
    FxResult dcf=fx_controls_precapture_stage(&s->dcf_subscription,&s->profile);if(dcf!=FX_OK){host->release(host->context,s);return dcf;}
    fx_controls_photo_init(&s->photo,&s->photo_bridge,&s->profile);
#elif FX_WLM_FEATURE == FX_WLM_FRAMING
    fx_framing_init(&s->framing,s,framing_read,framing_write);
#endif
    fx_suite_video_init(&s->video,p.prores,p.h265,p.log);
    text(s->provider.name,SERVICE_NAME,sizeof(s->provider.name));s->provider.version=1u;s->provider.kind=FX_NATIVE_PACK_DEVICE;
    s->provider.call_abi=FX_NATIVE_PACK_CALL_DEVICE;s->provider.capabilities=FX_SDK_NATIVE;
    s->provider.call_context=FX_NATIVE_PACK_ORIGINAL_UI;s->provider.resource=resource;s->provider.context=s;
    text(s->symbols[0].name,"controls.movie_open." FEATURE_NAME,FX_NATIVE_PACK_NAME);
    text(s->symbols[1].name,"controls.movie_close." FEATURE_NAME,FX_NATIVE_PACK_NAME);
    s->symbols[0].version=s->symbols[1].version=1u;
    s->symbols[0].flags=s->symbols[1].flags=FX_NATIVE_PACK_SYMBOL_FUNCTION;
    s->symbols[0].address=(uintptr_t)fx_wlm_movie_open;s->symbols[1].address=(uintptr_t)fx_wlm_movie_close;
    if(FX_WLM_DEPENDENCY_COUNT)s->symbols[2]=fx_wlm_dependency;
    atomic_store_explicit(&current,(uintptr_t)s,memory_order_release);
    *out=(FxNativePackExports){.abi=FX_NATIVE_PACK_ABI,.bytes=sizeof(*out),.provider_count=1u,.symbol_count=2u+FX_WLM_DEPENDENCY_COUNT,
        .providers=&s->provider,.symbols=s->symbols,.context=s,.event=event,.pending=pending};return FX_OK;
}
const uint32_t fx_wlm_component_metadata[]={UINT32_C(0x46585743),FX_NATIVE_PACK_ABI,FX_WLM_FEATURE,sizeof(FxNativePackHost),sizeof(FxNativePackExports),
    sizeof(FxNativePackProvider),sizeof(FxNativePackSymbol),sizeof(FxWlmControlState)};
