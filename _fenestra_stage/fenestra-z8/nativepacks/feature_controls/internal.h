/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FX_FEATURE_CONTROLS_INTERNAL_H
#define FX_FEATURE_CONTROLS_INTERNAL_H
#include "fenestra_native_pack.h"
#include "fenestra_suite_video.h"
#include "fenestra_framing.h"
#include "fenestra_photo_tools.h"
#include "fenestra_memory.h"
#define FX_CONTROLS_FRAMING 1u
#define FX_CONTROLS_VIDEO 2u
#define FX_CONTROLS_LOG 4u
#define FX_CONTROLS_PHOTO 8u
#ifndef FX_CONTROL_FEATURES
#define FX_CONTROL_FEATURES 15u
#endif
#if FX_CONTROL_FEATURES < 1 || FX_CONTROL_FEATURES > 15
#error Invalid feature subset
#endif
/* The profile lives in this verified native image, not in the core or EL0.
 * Only tests replace original object/function addresses with synthetic RAM. */
typedef struct {
    _Atomic uint8_t *prores,*h265,*log,*nraw,*desqueeze,*open_gate;
    uint8_t *psm_runtime;const uint8_t *task_registry;
    uintptr_t allocate,release,task_create,task_start,get,set,exact_get,timer_kind,timer_hold,timer_set;
    uintptr_t psm_worker,tier_republish,upstream_close;
    const uint8_t *dcf_constructor,*dcf_registration;uint8_t *dcf_bank;
    uint64_t (*ticks)(void),(*tick_rate)(void);
} FxControlsProfile;
typedef struct {
    _Atomic uint32_t pending,posted,serial;
    uint32_t held,delay,timer;uint64_t posted_at;
    const FxControlsProfile *profile;
} FxControlsPhotoBridge;
typedef struct {
    uintptr_t slot;uint32_t staged,published,already_new,reserved;
} FxControlsDcfSubscription;
FxResult fx_controls_precapture_stage(FxControlsDcfSubscription *,const FxControlsProfile *);
void fx_controls_precapture_publish(FxControlsDcfSubscription *);
void fx_controls_photo_init(FxPhotoToolsControl *,FxControlsPhotoBridge *,const FxControlsProfile *);
FxResult fx_controls_photo_stop(FxPhotoToolsControl *);
FxResult fx_controls_photo_resume(FxPhotoToolsControl *);
FxResult fx_controls_pack_entry(const FxNativePackHost *,FxNativePackExports *);
void fx_controls_movie_open(uintptr_t);
void fx_controls_movie_close(uintptr_t);
void fx_controls_video_open_hook(void);
void fx_controls_video_close_hook(void);
extern uintptr_t fx_controls_upstream_close;
#ifdef FX_CONTROLS_TESTING
extern FxControlsProfile fx_controls_test_profile;
#endif
#endif
