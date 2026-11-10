/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FX_Z8_C311_CAPSULE_RENDEZVOUS_H
#define FX_Z8_C311_CAPSULE_RENDEZVOUS_H
#include "fenestra.h"
#include <stdatomic.h>
/* Exact C311 three-CPU RTOS profile. Zero-initialize once in reboot-lifetime
 * core State. No allocator is used here. run uses the ORIGINAL synchronous
 * SMP callback dispatcher, whose WFE waits have NO timeout. Readiness checks
 * reduce known failures; they cannot turn that native API into a bounded one.
 * This stops the three RTOS CPUs, not DMA, Linux or other bus masters. The
 * caller's admission policy must exclude destinations used by those agents.
 * work executes in the initiating native task, after both remote CPUs park.
 * work must not allocate, wait for another CPU, invoke a native SMP callback,
 * or use any native lock that a parked CPU might hold. All staging resources
 * must already exist. Native context.protect is usable only with a private
 * context view (private_roots=0), flags=0 and its fixed page-table bitmap pool.
 */
typedef struct {
    _Atomic uint32_t gate,parked,exit_requested,sync_epoch;
    _Atomic uint32_t acknowledged[3];
    uint32_t initiator,held,last_outcome,reserved;
    uint64_t serial,lease,saved_daif;
    FxResult (*work)(void *);
    void *argument;
    FxResult result;
} FxZ8CapsuleRendezvous;
/* run must be called from EL1 with IRQ enabled and the verified stock
 * native mapping active. Exact native prologues, all three readiness values,
 * and all original SGI15 callback slots are checked before dispatch. These
 * windows supplement, and do not replace, the core's stock image identity.
 * No READY state is published by this adapter. */
FxResult fx_z8_capsule_rendezvous_run(FxZ8CapsuleRendezvous *,FxResult (*work)(void *),void *);
/* Only the initiating work may enter/leave. enter saves and masks local IRQ
 * and FIQ, and returns a nonzero lease only after all CPUs are parked. leave
 * performs per-CPU TLBI VMALLE1 + IALLU + barriers and waits for all ACKs;
 * every CPU remains parked afterward. Multiple leaves allow rollback to
 * request another epoch using the SAME lease. The lease/DAIF are retained
 * until work returns; run then releases the remote callbacks and restores
 * the original DAIF. outcome uses FX_CAPSULE_LEAVE_* values. */
FxResult fx_z8_capsule_exclusion_enter(FxZ8CapsuleRendezvous *,uint64_t *lease);
FxResult fx_z8_capsule_exclusion_leave(FxZ8CapsuleRendezvous *,uint64_t lease,uint32_t outcome);
#endif
