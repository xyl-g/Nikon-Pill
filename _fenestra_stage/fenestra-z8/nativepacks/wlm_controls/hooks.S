/* SPDX-License-Identifier: GPL-3.0-only
 * Exact MOV lifetime contracts from the reviewed suite_video_hooks.S.
 * Helpers are built general-register-only, preserve x0..18/x30 and NZCV.
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
.global fx_wlm_video_open_hook
fx_wlm_video_open_hook:
 save_all
 mov x0,x8
 bl fx_wlm_movie_dispatch_open
 restore_all
 str x8,[x19]
 b fx_wlm_open_resume
.balign 16
.global fx_wlm_video_close_hook
fx_wlm_video_close_hook:
 stp x19,x30,[sp,#-16]!
 mov x19,x0
 bl fx_wlm_video_close_original
 cbnz w0,1f
 save_all
 mov x0,x19
 bl fx_wlm_movie_dispatch_close
 restore_all
1:
 ldp x19,x30,[sp],#16
 ret
.global fx_wlm_video_close_original
fx_wlm_video_close_original:
 adrp x16,fx_wlm_upstream_close
 ldr x16,[x16,:lo12:fx_wlm_upstream_close]
 cbz x16,1f
 br x16
1:
 str x20,[sp,#-32]!
 b fx_wlm_close_resume
