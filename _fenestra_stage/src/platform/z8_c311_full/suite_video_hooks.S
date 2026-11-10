/* SPDX-License-Identifier: GPL-3.0-only
 * MOV successful-create / successful-close observation only. Native backend
 * hooks themselves are the independently verified pinned feature overlay.
 */
.text
.macro save_all
 sub sp,sp,#176
 stp x0,x1,[sp]
 stp x2,x3,[sp,#16]
 stp x4,x5,[sp,#32]
 stp x6,x7,[sp,#48]
 stp x8,x9,[sp,#64]
 stp x10,x11,[sp,#80]
 stp x12,x13,[sp,#96]
 stp x14,x15,[sp,#112]
 stp x16,x17,[sp,#128]
 stp x18,x30,[sp,#144]
 mrs x9,nzcv
 str x9,[sp,#160]
.endm
.macro restore_all
 ldr x9,[sp,#160]
 msr nzcv,x9
 ldp x0,x1,[sp]
 ldp x2,x3,[sp,#16]
 ldp x4,x5,[sp,#32]
 ldp x6,x7,[sp,#48]
 ldp x8,x9,[sp,#64]
 ldp x10,x11,[sp,#80]
 ldp x12,x13,[sp,#96]
 ldp x14,x15,[sp,#112]
 ldp x16,x17,[sp,#128]
 ldp x18,x30,[sp,#144]
 add sp,sp,#176
.endm
.balign 16
.global fx_z8_suite_video_open_hook
fx_z8_suite_video_open_hook:
 save_all
 mov x0,x8
 bl fx_z8_suite_video_movie_open
 restore_all
 str x8,[x19]
 b fx_z8_suite_video_open_resume
.balign 16
.global fx_z8_suite_video_close_hook
fx_z8_suite_video_close_hook:
 stp x19,x30,[sp,#-16]!
 mov x19,x0
 bl fx_z8_suite_video_close_original
 cbnz w0,1f
 save_all
 mov x0,x19
 bl fx_z8_suite_video_movie_close
 restore_all
1:
 ldp x19,x30,[sp],#16
 ret
.global fx_z8_suite_video_close_original
fx_z8_suite_video_close_original:
 /* Preserve the complete pinned open-gate writer close / slot release.
  * It owns the original prologue and calls the stock continuation once. */
 b fx_z8_suite_video_upstream_close
