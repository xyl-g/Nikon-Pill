/* SPDX-License-Identifier: GPL-3.0-only */
#define FX_VIDEO_BINDING "camera.video.log_colour"
#define FX_VIDEO_TITLE "Log 色彩"
#define FX_VIDEO_LABEL "Log / 色域"
#define FX_VIDEO_GRADES 3u
static const char *const video_options[FX_VIDEO_GRADES]={"N-Log / BT.2020","S-Log3 / S-Gamut3.Cine","Log3G10 / REDWideGamutRGB"};
#include "../video_setting/module.inc"
