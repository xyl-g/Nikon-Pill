/* SPDX-License-Identifier: GPL-3.0-only
 * Runtime bank addresses from pinned NikonFirmwarePatch a21fc8c04b6d:
 * z8quality/src/hooks.S, z8h265/src/hooks.S and z8log/src/hooks.S.
 * Build only with the verified full native feature-suite overlay.
 */
#include "fenestra_suite_video.h"
#ifdef FX_Z8_FEATURE_SUITE
#include "z8_feature_suite_symbols.h"
#endif
static int same(const char *a,const char *b){while(*a && *a==*b){++a;++b;}return *a==*b;}
void fx_z8_suite_video_init(FxSuiteVideoControl *s) {
    fx_suite_video_init(s,(_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c029),
        (_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c269),
        (_Atomic uint8_t *)(uintptr_t)UINT64_C(0xa03d5c0dd));
}
FxResult fx_z8_suite_video_resource(void *context,uint32_t op,const FxNamedBinding *binding,FxPlatformOwner *o,
    uint64_t token,uint64_t *cookie,const FxServiceRequest *q,FxServiceReply *a) {
    (void)context;if(!binding || !binding->name)return FX_ARGUMENT;
    uint32_t channel=same(binding->name,"camera.video.prores")?FX_SUITE_PRORES:
        same(binding->name,"camera.video.h265")?FX_SUITE_H265:
        same(binding->name,"camera.video.log_colour")?FX_SUITE_LOG_COLOUR:FX_SUITE_VIDEO_CHANNELS;
    FxResult result=fx_suite_video_resource(fx_z8_suite_video_control(),channel,op,o,token,cookie,q,a);
#ifdef FX_Z8_TIER_STAR_REPUBLISH
    /* The pinned native quality OK wrappers use this exact publication path.
     * Never call an arbitrary module pointer or a guessed firmware function. */
    if(result==FX_OK && op==FX_RESOURCE_CALL && q && q->arg[2]==FX_SUITE_VIDEO_SELECT)
        ((void (*)(void))(uintptr_t)FX_Z8_TIER_STAR_REPUBLISH)();
#endif
    return result;
}
void fx_z8_suite_video_movie_open(uintptr_t context){fx_suite_video_movie_open(fx_z8_suite_video_control(),context);}
void fx_z8_suite_video_movie_close(uintptr_t context){fx_suite_video_movie_close(fx_z8_suite_video_control(),context);}
uint32_t fx_z8_suite_capture_busy(void) {
    FxSuiteVideoControl *s=fx_z8_suite_video_control();
    return !s || atomic_load(&s->active) || atomic_load(&s->uncertain) || atomic_load(&s->fault) || atomic_load(&s->gate);
}
const uint32_t fx_z8_suite_video_metadata[]={UINT32_C(0x46585356),FX_SUITE_VIDEO_ABI,sizeof(FxSuiteVideoControl),
    offsetof(FxSuiteVideoControl,channel),offsetof(FxSuiteVideoControl,active),offsetof(FxSuiteVideoControl,fault),
    offsetof(FxSuiteVideoControl,uncertain),offsetof(FxSuiteVideoControl,value),sizeof(FxSuiteVideoChannel),
    offsetof(FxSuiteVideoChannel,owner),FX_SUITE_VIDEO_CHANNELS,FX_SUITE_VIDEO_MOVIES};
