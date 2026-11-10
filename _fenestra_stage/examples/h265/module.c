/* SPDX-License-Identifier: GPL-3.0-only */
#define FX_VIDEO_BINDING "camera.video.h265"
#define FX_VIDEO_TITLE "H.265 码率"
#define FX_VIDEO_LABEL "码率"
#define FX_VIDEO_GRADES 5u
static const char *const video_options[FX_VIDEO_GRADES]={"100%（默认）","60%","45%","30%","20%"};
#include "../video_setting/module.inc"
