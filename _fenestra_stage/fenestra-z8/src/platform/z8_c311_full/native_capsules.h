/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FENESTRA_Z8_NATIVE_CAPSULES_H
#define FENESTRA_Z8_NATIVE_CAPSULES_H
#include "fenestra_capsule.h"
#include "fenestra_pack_registry.h"
#include "fenestra_mmu_context.h"
#include "fenestra_native_mapping.h"
#include "fenestra_native_locale.h"
#include "fenestra_wlm_native.h"
#include "capsule_rendezvous.h"
#define FX_Z8_CAPSULE_PAGES 1536u
#define FX_Z8_CAPSULE_STAGING (128u*1024u)
typedef struct {
    uintptr_t address,shadow;
    uint64_t physical[4][2],shadow_physical;
    uint32_t attributes[4][2];
    uint32_t image,flags,shadow_mapped,changed;
} FxZ8CapsulePage;
typedef struct {
    FxCapsuleControl control;
    FxPackRegistry registry;
    FxZ8CapsuleRendezvous rendezvous;
    const FxPlatform *base;
    FxNativeMappingRegistry *mappings;
    FxNativeMmuContext *mmu;
    uintptr_t translate,attributes,walk,protect,map,unmap,invalidate;
    uintptr_t roots[4];uint32_t root_count,page_count;
    FxZ8CapsulePage *pages;
    uint8_t *package,*staging;uint32_t staging_used;
    _Atomic uint32_t boot_attempted;
    uint32_t lock_held,parked,prepared,error,present;
    /* Optional cold-lifecycle cancellation. Read-only/no-allocation, including
     * under the real three-CPU exclusion before any target modification. */
    int (*cancelled)(void *);void *cancel_context;
    /* Compose copied all component records before target mapping begins. */
    void (*release_inputs)(void *);void *release_context;
    /* Copied before HOOK publication, visible only with registry.ready. */
    FxNativeLocale locale;
    /* Private cold-composed transaction; no card BOOT file is opened. */
    FxNativePackEntry native_entries[FX_WLM_NATIVE_COMPONENTS];
    uint32_t native_entry_count,native_selection;
    /* Power-off ingress revocation (revocation.h). revoked: a valid record is
     * in RAM now; unreachable_boot: cold prepare relied on one; ingress_*:
     * last disarm/rearm result and consecutive BUSY retries at shutdown. */
    uint32_t operation,entered,revoked,revoked_error,unreachable_boot,ingress_error,ingress_busy;
    /* The composed private transaction's identity, so a cold attempt
     * continued after a cancelled shutdown reuses package and page plan. */
    uint32_t composed_bytes;uint8_t composed_sha256[32];
} FxZ8Capsules;
enum {FX_Z8_CAPSULE_COMMIT,FX_Z8_CAPSULE_DISARM,FX_Z8_CAPSULE_REARM};
/* Called at most once by the initial cold loader, after the generic stock
 * font bank is published and before any EL0/module activation. No package
 * replacement/unload during warm Resume. Objects live until reset. */
/* Read-only stage validation, including projected post-HOOK directory cells. */
FxResult fx_z8_capsule_locale_stage(FxZ8Capsules *);
int fx_z8_capsule_locale_span(FxZ8Capsules *,uintptr_t,uint32_t);
FxResult fx_z8_wlm_native_boot(FxZ8Capsules *,const FxPlatform *,FxNativeMappingRegistry *,const FxWlmNativeView *,uint32_t);
FxResult fx_z8_capsule_boot(FxZ8Capsules *,const FxPlatform *,FxNativeMappingRegistry *,char drive);
/* Loader task only, after every native owner acknowledged STOP. Restores all
 * HOOK preimages so a retained-RAM boot never enters IMAGE with reset heap or
 * task state, then seals the revocation record. No transaction: OK. */
FxResult fx_z8_capsule_disarm(FxZ8Capsules *);
/* Loader task only, before native RESUME: clears the record, then republishes
 * the same HOOK bytes. IMAGE, entry state and registry were never released. */
FxResult fx_z8_capsule_rearm(FxZ8Capsules *);
/* Nonzero while no target byte, mapping, permission, registry stage or
 * revocation record has been touched: the cold commit never got past the
 * cancellation check in its exclusion enter. Read-only. */
int fx_z8_capsule_pristine(FxZ8Capsules *);
/* Loader task only, for a pristine cancelled cold attempt: re-opens the boot
 * so the next attempt continues. The composed package and page plan are kept
 * (no free/allocate after power-on); the control is re-initialised and the
 * transaction is prepared and committed again from its unchanged preimages. */
FxResult fx_z8_capsule_rewind(FxZ8Capsules *);
#endif
