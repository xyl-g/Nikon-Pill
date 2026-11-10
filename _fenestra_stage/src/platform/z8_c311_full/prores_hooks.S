/* SPDX-License-Identifier: GPL-3.0-only
 * C3.11 ProRes hooks, derived from z8prores/back_half.py.
 * Helpers are general-register-only; preserve all caller registers and NZCV.
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
.global fx_z8_prores_type_hook
fx_z8_prores_type_hook:
 save_all
 bl fx_z8_prores_selected
 tbnz w0,#31,1f
 str x0,[sp,#64]
 restore_all
 b fx_z8_prores_type_resume
1:
 restore_all
 ldr w8,[x23,#0x68]
 b fx_z8_prores_type_resume
.global fx_z8_prores_check_hook
fx_z8_prores_check_hook:
 save_all
 bl fx_z8_prores_selected
 tbnz w0,#31,1f
 restore_all
 cmp w8,#4
 b fx_z8_prores_check_resume
1:
 restore_all
 cmp w8,#2
 b fx_z8_prores_check_resume
.global fx_z8_prores_rate_hook
fx_z8_prores_rate_hook:
 save_all
 bl fx_z8_prores_rate
 cbz x0,1f
 str x0,[sp,#80]
 restore_all
 mov w8,#0x18
 b fx_z8_prores_rate_resume
1:
 restore_all
 mov x10,#0x3a50
 b fx_z8_prores_rate_original_resume
.global fx_z8_prores_label_hook
fx_z8_prores_label_hook:
 save_all
 bl fx_z8_prores_selected
 str x0,[sp,#72]
 restore_all
 tbnz w9,#31,2f
 cbz w9,2f
 adr x10,fx_z8_prores_fourcc
 ldrb w11,[x10,x9]
 strb w11,[x0,#7]
 adr x10,fx_z8_prores_names
 add x10,x10,x9,lsl #5
 add x12,x0,#0x32
 mov w13,#32
1:
 ldrb w11,[x10],#1
 strb w11,[x12],#1
 subs w13,w13,#1
 b.ne 1b
2:
 b fx_z8_prores_label_next
.global fx_z8_prores_parser_hook
fx_z8_prores_parser_hook:
 mov w8,#0x6368
 movk w8,#0x6170,lsl #16
 sub w9,w0,w8
 cmp w9,#11
 b.hi 2f
 mov w10,#0x8c1
 lsr w10,w10,w9
 tbz w10,#0,2f
 adr x10,fx_z8_prores_grade_by_diff
 ldrb w10,[x10,x9]
 save_all
 mov w0,w10
 bl fx_z8_prores_remember
 restore_all
 b fx_z8_prores_parser_accept
2:
 b fx_z8_prores_parser_reject
.global fx_z8_prores_shoot_hook
fx_z8_prores_shoot_hook:
 ldr x1,[x9,#8]
 ldr w10,[x9]
 cmp w10,#4
 b.ne 2f
 save_all
 bl fx_z8_prores_selected
 str x0,[sp,#80]
 restore_all
 tbnz w10,#31,2f
 cbz w10,2f
 adr x11,fx_z8_prores_display
 add x1,x11,x10,lsl #4
2:
 b fx_z8_prores_shoot_resume
.global fx_z8_prores_play_hook
fx_z8_prores_play_hook:
 save_all
 bl fx_z8_prores_playback
 str x0,[sp,#80]
 restore_all
 adr x11,fx_z8_prores_display
 add x10,x11,x10,lsl #4
 ldr x12,[x10]
 str x12,[sp,#0x118]
 ldr w12,[x10,#8]
 str w12,[sp,#0x120]
 ldrh w12,[x10,#12]
 strh w12,[sp,#0x124]
 b fx_z8_prores_play_resume
.global fx_z8_prores_bank_hook
fx_z8_prores_bank_hook:
 save_all
 bl fx_z8_prores_selected
 tbnz w0,#31,2f
 restore_all
 ldp x10,x8,[sp]
 lsl w9,w20,#16
 mov w11,#0
 movk w11,#0x120b,lsl #16
 cmp w9,w11
 b.eq 1f
 b fx_z8_prores_bank_resume
1:
 ldrh w9,[x8]
 strh w9,[x10]
 ldrb w8,[x8]
 b fx_z8_prores_bank_done
2:
 restore_all
 ldp x10,x8,[sp]
 b fx_z8_prores_bank_resume
.global fx_z8_prores_open_hook
fx_z8_prores_open_hook:
 save_all
 mov x0,x8
 bl fx_z8_prores_movie_open
 restore_all
 str x8,[x19]
 b fx_z8_prores_open_resume
.global fx_z8_prores_close_hook
fx_z8_prores_close_hook:
 stp x19,x30,[sp,#-16]!
 mov x19,x0
 bl fx_z8_prores_close_original
 cbnz w0,1f
 save_all
 mov x0,x19
 bl fx_z8_prores_movie_close
 restore_all
1:
 ldp x19,x30,[sp],#16
 ret
.global fx_z8_prores_close_original
fx_z8_prores_close_original:
 str x20,[sp,#-0x20]!
 b fx_z8_prores_close_resume
.section .rodata.prores,"a"
.balign 8
.global fx_z8_prores_fourcc
fx_z8_prores_fourcc:
 .ascii "hnso"
fx_z8_prores_grade_by_diff:
 .byte 0,0,0,0,0,0,1,3,0,0,0,2
.global fx_z8_prores_names
fx_z8_prores_names:
 .byte 19
 .ascii "Apple ProRes 422 HQ"
 .zero 12
 .byte 16
 .ascii "Apple ProRes 422"
 .zero 15
 .byte 19
 .ascii "Apple ProRes 422 LT"
 .zero 12
 .byte 22
 .ascii "Apple ProRes 422 Proxy"
 .zero 9
.global fx_z8_prores_display
fx_z8_prores_display:
 .ascii "ProRes 422 HQ"
 .zero 3
 .ascii "ProRes 422"
 .zero 6
 .ascii "ProRes 422 LT"
 .zero 3
 .ascii "ProRes Proxy"
 .zero 4
