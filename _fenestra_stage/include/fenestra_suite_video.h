/* SPDX-License-Identifier: GPL-3.0-only
 * Copied-message controls for the pinned C3.11 feature-suite native backend.
 * camera.video.prores/v1 preserves the already shipped ProRes wire ABI.
 * Native hooks/tables remain core-owned; card modules contain only UI code.
 */
#ifndef FENESTRA_SUITE_VIDEO_H
#define FENESTRA_SUITE_VIDEO_H
#include "fenestra_prores.h"
#define FX_SUITE_VIDEO_ABI 1u
#define FX_SUITE_VIDEO_CHANNELS 3u
#define FX_SUITE_VIDEO_MOVIES 32u
enum {FX_SUITE_PRORES,FX_SUITE_H265,FX_SUITE_LOG_COLOUR};
enum {FX_SUITE_VIDEO_QUERY,FX_SUITE_VIDEO_SELECT};
/* These layouts intentionally equal FxProResSelection/FxProResStatus. */
typedef struct {uint32_t abi,bytes,selected,reserved;} FxSuiteVideoSelection;
typedef struct {uint32_t abi,bytes,grades,selected,flags,movies,fault,revision;} FxSuiteVideoStatus;
typedef struct {
    _Atomic uintptr_t owner;
    uint64_t token;
    uint32_t enabled,revoked,revision;
} FxSuiteVideoChannel;
typedef struct {
    _Atomic uint32_t gate,fault,uncertain,active;
    _Atomic uint8_t *value[FX_SUITE_VIDEO_CHANNELS];
    FxSuiteVideoChannel channel[FX_SUITE_VIDEO_CHANNELS];
    uintptr_t movies[FX_SUITE_VIDEO_MOVIES];
} FxSuiteVideoControl;
/* Initialize only before publication. Value bytes belong to the selected
 * original movie shooting bank, not to a module or to module-owned memory. */
void fx_suite_video_init(FxSuiteVideoControl *,_Atomic uint8_t *,_Atomic uint8_t *,_Atomic uint8_t *);
void fx_suite_video_movie_open(FxSuiteVideoControl *,uintptr_t);
void fx_suite_video_movie_close(FxSuiteVideoControl *,uintptr_t);
FxResult fx_suite_video_resource(FxSuiteVideoControl *,uint32_t channel,uint32_t op,FxPlatformOwner *,uint64_t,
    uint64_t *,const FxServiceRequest *,FxServiceReply *);
void fx_z8_suite_video_init(FxSuiteVideoControl *);
uint32_t fx_z8_suite_capture_busy(void);
FxSuiteVideoControl *fx_z8_suite_video_control(void); /* trusted menu owner */
FxResult fx_z8_suite_video_resource(void *,uint32_t,const FxNamedBinding *,FxPlatformOwner *,uint64_t,
    uint64_t *,const FxServiceRequest *,FxServiceReply *);
#endif
