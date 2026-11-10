/* SPDX-License-Identifier: GPL-3.0-only
 * camera.video.prores/v1: copied control messages, no module code in Nikon tasks.
 */
#ifndef FENESTRA_PRORES_H
#define FENESTRA_PRORES_H
#include "fenestra_platform.h"
#define FX_PRORES_ABI 1u
#define FX_PRORES_MOVIES 32u
enum {FX_PRORES_QUERY,FX_PRORES_SELECT};
enum {FX_PRORES_HQ,FX_PRORES_STANDARD,FX_PRORES_LT,FX_PRORES_PROXY};
enum {FX_PRORES_ENABLED=1u,FX_PRORES_RECORDING=2u,FX_PRORES_REVOKED=4u,FX_PRORES_FAULT=8u};
typedef struct {uint32_t abi,bytes,grade,reserved;} FxProResSelection;
typedef struct {uint32_t abi,bytes,grades,selected,flags,movies,fault,revision;} FxProResStatus;
/* Core-owned state, zero-initialized before native publication. Retained with
 * the menu controller until reboot. value points at C3.11's checked bank byte. */
typedef struct {
    _Atomic uint32_t gate,enabled,fault,uncertain,active,playback;
    _Atomic uintptr_t owner;
    uint64_t token;
    uint32_t revoked,revision;
    _Atomic uint8_t *value;
    uintptr_t movies[FX_PRORES_MOVIES];
} FxProResControl;
void fx_prores_init(FxProResControl *,_Atomic uint8_t *);
uint32_t fx_prores_selected(const FxProResControl *); /* UINT32_MAX = original path */
void fx_prores_movie_open(FxProResControl *,uintptr_t);
void fx_prores_movie_close(FxProResControl *,uintptr_t);
FxResult fx_prores_resource(FxProResControl *,uint32_t,FxPlatformOwner *,uint64_t,uint64_t *,const FxServiceRequest *,FxServiceReply *);
#endif
