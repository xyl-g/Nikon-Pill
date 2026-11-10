/* SPDX-License-Identifier: GPL-3.0-only */
.text
.macro save_callers
    sub sp, sp, #176
    stp x0, x1, [sp]
    stp x2, x3, [sp, #16]
    stp x4, x5, [sp, #32]
    stp x6, x7, [sp, #48]
    stp x8, x9, [sp, #64]
    stp x10, x11, [sp, #80]
    stp x12, x13, [sp, #96]
    stp x14, x15, [sp, #112]
    stp x16, x17, [sp, #128]
    stp x18, x30, [sp, #144]
    mrs x9, nzcv
    str x9, [sp, #160]
.endm
.macro restore_callers
    ldr x9, [sp, #160]
    msr nzcv, x9
    ldp x0, x1, [sp]
    ldp x2, x3, [sp, #16]
    ldp x4, x5, [sp, #32]
    ldp x6, x7, [sp, #48]
    ldp x8, x9, [sp, #64]
    ldp x10, x11, [sp, #80]
    ldp x12, x13, [sp, #96]
    ldp x14, x15, [sp, #112]
    ldp x16, x17, [sp, #128]
    ldp x18, x30, [sp, #144]
    add sp, sp, #176
.endm

.global portrait_sample_hook
portrait_sample_hook:
    save_callers
    mov x0, x20
    ldr w1, [x20, #0x55c]
    bl fx_portrait_sample
    restore_callers
    ldr w8, [x20, #0x55c]
    b portrait_sample_resume

.global portrait_tkhd_hook
portrait_tkhd_hook:
    save_callers
    mov x0, x19
    ldr w2, [x19, #0x518]
    ldr w3, [x19, #0x51c]
    bl fx_portrait_matrix
    restore_callers
    b portrait_stock_copy

.global portrait_close_hook
portrait_close_hook:
    save_callers
    bl fx_portrait_forget
    restore_callers
    b portrait_stock_reset
