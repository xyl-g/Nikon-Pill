/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FENESTRA_SCENE_TEXT_H
#define FENESTRA_SCENE_TEXT_H
#include "fenestra_scene_sdk.h"
/* Selected, pre-rasterized text only. The caller owns the background and uses
 * ordinary RECT commands; no new native API or runtime font parser is needed. */
typedef struct { uint16_t x,y,width,height; } FxSceneTextRect;
typedef struct { uint16_t width,height;uint32_t count;const FxSceneTextRect *rects; } FxSceneText;
/* All geometry/capacity is validated before any output is changed. scale is
 * an integer pixel scale; canvas bounds must be those of the scene submission. */
static inline FxResult fx_scene_text_append(const FxSceneText *text,uint32_t x,uint32_t y,
    uint32_t scale,uint32_t rgb,uint32_t canvas_width,uint32_t canvas_height,
    FxSceneCommand *commands,uint32_t capacity,uint32_t *count) {
    if(!text || !commands || !count || (text->count&&!text->rects) || !text->width || !text->height ||
       !scale || scale>32u || rgb>0xffffffu || !canvas_width || !canvas_height ||
       canvas_width>FX_SCENE_EXTENT || canvas_height>FX_SCENE_EXTENT)return FX_ARGUMENT;
    if(*count>capacity || text->count>capacity-*count || x>=canvas_width || y>=canvas_height ||
       (uint64_t)text->width*scale>canvas_width-x || (uint64_t)text->height*scale>canvas_height-y)return FX_BOUNDS;
    for(uint32_t i=0u;i<text->count;++i) {
        const FxSceneTextRect *r=&text->rects[i];
        if(!r->width || !r->height || r->x>=text->width || r->y>=text->height ||
           r->width>text->width-r->x || r->height>text->height-r->y)return FX_INTEGRITY;
    }
    for(uint32_t i=0u;i<text->count;++i) {
        const FxSceneTextRect *r=&text->rects[i];commands[*count+i]=fx_scene_rectangle(
            (uint16_t)(x+(uint32_t)r->x*scale),(uint16_t)(y+(uint32_t)r->y*scale),
            (uint16_t)((uint32_t)r->width*scale),(uint32_t)r->height*scale,rgb);
    }
    *count+=text->count;return FX_OK;
}
#endif
