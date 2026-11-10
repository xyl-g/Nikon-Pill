/* SPDX-License-Identifier: GPL-3.0-only
 * Shared MOV hook dispatch only. No feature settings or feature implementation.
 * This entry must stage after all independent control entries. Dependencies
 * are resolved once during staging; PUBLISH cannot allocate or fail.
 */
#include "fenestra_native_pack.h"
#include "fenestra_memory.h"
typedef struct {
    FxNativePackHost host;
    _Atomic uint32_t published,entered;
    uintptr_t open[5],close[5];
    FxNativePackSymbol symbols[2];
} FxWlmMovieBridge;
static _Atomic uintptr_t current;
uintptr_t fx_wlm_upstream_close;
void fx_wlm_video_open_hook(void);
void fx_wlm_video_close_hook(void);
static uint32_t pending(void *context){FxWlmMovieBridge *s=context;return s?atomic_load_explicit(&s->entered,memory_order_acquire):0u;}
static FxResult event(void *context,uint32_t op){
    FxWlmMovieBridge *s=context;if(!s)return FX_ARGUMENT;
    if(op==FX_NATIVE_PACK_PUBLISH){atomic_store_explicit(&s->published,1u,memory_order_release);return FX_OK;}
    if(op==FX_NATIVE_PACK_ABORT){
        if(atomic_load(&s->published)||atomic_load(&s->entered))return FX_BUSY;
        FxNativePackHost host=s->host;atomic_store_explicit(&current,0u,memory_order_release);fx_wlm_upstream_close=0u;
        host.release(host.context,s);return FX_OK;
    }
    if(!atomic_load_explicit(&s->published,memory_order_acquire))return FX_STATE;
    if(op==FX_NATIVE_PACK_GUI_POLL)return FX_OK;
    if(op==FX_NATIVE_PACK_STOP||op==FX_NATIVE_PACK_FAULT||op==FX_NATIVE_PACK_RESUME)return pending(s)?FX_BUSY:FX_OK;
    return FX_ARGUMENT;
}
void fx_wlm_movie_dispatch_open(uintptr_t native_context){
    FxWlmMovieBridge *s=(void *)atomic_load_explicit(&current,memory_order_acquire);
    if(!s||!atomic_load_explicit(&s->published,memory_order_acquire))return;
    atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);
    for(uint32_t i=0u;i<5u;++i)if(s->open[i])((void (*)(uintptr_t))s->open[i])(native_context);
    atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);
}
void fx_wlm_movie_dispatch_close(uintptr_t native_context){
    FxWlmMovieBridge *s=(void *)atomic_load_explicit(&current,memory_order_acquire);
    if(!s||!atomic_load_explicit(&s->published,memory_order_acquire))return;
    atomic_fetch_add_explicit(&s->entered,1u,memory_order_acquire);
    for(uint32_t i=0u;i<5u;++i)if(s->close[i])((void (*)(uintptr_t))s->close[i])(native_context);
    atomic_fetch_sub_explicit(&s->entered,1u,memory_order_release);
}
FxResult fx_wlm_movie_bridge_entry(const FxNativePackHost *host,FxNativePackExports *out){
    static const char *opens[]={"controls.movie_open.prores","controls.movie_open.h265","controls.movie_open.log_colour","controls.movie_open.framing","controls.movie_open.photo_tools"};
    static const char *closes[]={"controls.movie_close.prores","controls.movie_close.h265","controls.movie_close.log_colour","controls.movie_close.framing","controls.movie_close.photo_tools"};
    if(!host||!out||host->abi!=FX_NATIVE_PACK_ABI||host->bytes<sizeof(*host)||!host->allocate||!host->release||!host->lookup)return FX_ARGUMENT;
    if(atomic_load_explicit(&current,memory_order_acquire))return FX_STATE;
    uintptr_t open[5]={0},close[5]={0},upstream=0u;uint32_t count=0u;
    for(uint32_t i=0u;i<5u;++i){
        FxResult a=host->lookup(host->context,opens[i],1u,&open[i]),b=host->lookup(host->context,closes[i],1u,&close[i]);
        if((a!=FX_OK&&a!=FX_CAPABILITY)||(b!=FX_OK&&b!=FX_CAPABILITY)||a!=b||
           (a==FX_OK&&(!open[i]||!close[i]))||(a==FX_CAPABILITY&&(open[i]||close[i])))return FX_INTEGRITY;
        count+=a==FX_OK;
    }
    FxResult original=host->lookup(host->context,"controls.movie_close_original.framing",1u,&upstream);
    if(original!=(open[3]?FX_OK:FX_CAPABILITY)||!!upstream!=!!open[3])return FX_INTEGRITY;
    if(!count)return FX_CAPABILITY;
    FxWlmMovieBridge *s=host->allocate(host->context,sizeof(*s),16u);if(!s)return FX_BUDGET;
    if((uintptr_t)s&15u){host->release(host->context,s);return FX_INTEGRITY;}
    memset(s,0,sizeof(*s));s->host=*host;memcpy(s->open,open,sizeof(open));memcpy(s->close,close,sizeof(close));
    s->symbols[0]=(FxNativePackSymbol){.name="controls.video_open_hook",.version=1u,.flags=FX_NATIVE_PACK_SYMBOL_FUNCTION,.address=(uintptr_t)fx_wlm_video_open_hook};
    s->symbols[1]=(FxNativePackSymbol){.name="controls.video_close_hook",.version=1u,.flags=FX_NATIVE_PACK_SYMBOL_FUNCTION,.address=(uintptr_t)fx_wlm_video_close_hook};
    fx_wlm_upstream_close=upstream;atomic_store_explicit(&current,(uintptr_t)s,memory_order_release);
    *out=(FxNativePackExports){.abi=FX_NATIVE_PACK_ABI,.bytes=sizeof(*out),.symbol_count=2u,.symbols=s->symbols,.context=s,.event=event,.pending=pending};return FX_OK;
}
const uint32_t fx_wlm_component_metadata[]={UINT32_C(0x46585743),FX_NATIVE_PACK_ABI,0u,sizeof(FxNativePackHost),sizeof(FxNativePackExports),sizeof(FxNativePackProvider),sizeof(FxNativePackSymbol),sizeof(FxWlmMovieBridge)};
