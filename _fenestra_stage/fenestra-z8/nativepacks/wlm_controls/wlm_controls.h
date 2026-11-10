/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FX_WLM_CONTROLS_H
#define FX_WLM_CONTROLS_H
#include "../feature_controls/internal.h"
#define FX_WLM_FRAMING 1u
#define FX_WLM_PRORES 2u
#define FX_WLM_H265 4u
#define FX_WLM_LOG_COLOUR 8u
#define FX_WLM_PHOTO_TOOLS 16u
#ifndef FX_WLM_FEATURE
#error Define one FX_WLM_FEATURE bit
#endif
#if FX_WLM_FEATURE != 1 && FX_WLM_FEATURE != 2 && FX_WLM_FEATURE != 4 && FX_WLM_FEATURE != 8 && FX_WLM_FEATURE != 16
#error One independent feature is required
#endif
FxResult fx_wlm_pack_entry(const FxNativePackHost *,FxNativePackExports *);
void fx_wlm_movie_open(uintptr_t);
void fx_wlm_movie_close(uintptr_t);
#endif
