/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FENESTRA_WLM_BUNDLE_H
#define FENESTRA_WLM_BUNDLE_H
#include "fenestra_services.h"
#include "fenestra_wlm_native.h"
/* WLM v4 is an explicit compound container. Only its inner v2/v3 service
 * frontend is an EL0 module; native components are never EL0 segments.
 * All integers on disk are little endian. Unknown flags/versions fail closed. */
#define FX_WLM_BUNDLE_VERSION 4u
#define FX_WLM_BUNDLE_HEADER 192u
#define FX_WLM_COMPONENT_BYTES 64u
#define FX_WLM_COMPONENTS 32u
#define FX_WLM_BUNDLE_LIMIT (16u*1024u*1024u)
#define FX_WLM_FROZEN_BUDGET (32u*1024u*1024u)
#define FX_WLM_NATIVE_ABI 1u
typedef struct {
    char id[16];
    uint32_t version,flags,offset,bytes;
    uint8_t sha256[32];
} FxWlmComponent;
_Static_assert(sizeof(FxWlmComponent)==FX_WLM_COMPONENT_BYTES,"WLM component disk ABI");
typedef struct {
    char id[16];
    uint32_t package_bytes,frontend_offset,frontend_bytes,component_count,native_abi;
    uint8_t frontend_sha256[32],package_sha256[32];
    FxModule frontend;
    FxWlmComponent components[FX_WLM_COMPONENTS];
} FxWlmBundle;
/* Read only: checks target, complete body/front/component hashes and every
 * region before returning any usable slice. No allocation/mapping/entry call. */
FxResult fx_wlm_bundle_inspect(const uint8_t *,uint32_t,const char id[16],
    const uint8_t stock_sha256[32],uint64_t capabilities,FxWlmBundle *);
/* Sequential manager preflight. Validates the same complete v4 container,
 * while retaining only metadata and its ordinary frontend. The reader owns
 * one open descriptor; a successful check consumes exactly bytes plus EOF.
 * Ordinary service packages retain their existing full-package validator. */
typedef struct {
    void *context;
    FxResult (*read)(void *,uint8_t *,uint32_t,uint32_t *);
} FxWlmStreamIO;
FxResult fx_wlm_package_inspect_stream(const FxWlmStreamIO *,uint32_t bytes,
    const char id[16],const uint8_t stock_sha256[32],uint64_t capabilities,
    FxIndexMemory,uint8_t *scratch,uint32_t scratch_bytes,
    FxModule *,uint8_t package_sha256[32],FxAllocationObserver);
/* One cold snapshot. Each outer is released after its frontend and unique
 * components are copied. Frontends stay retained through all worker
 * generations; components stay only until the composer owns its plan. Entries are
 * owned native data, not EL0 data.
 * A failed/uncertain close is terminal and cannot be retried on this object.
 * Only an INTERRUPTED (cancelled) scan may be called again with the same
 * memory: it keeps every completed package and skips those ids, so no
 * buffer is freed and allocated again. */
typedef struct {
    char id[16];
    uint8_t *package,*frontend;
    uint32_t package_bytes,frontend_bytes,is_bundle;
    uint8_t package_sha256[32];
    FxWlmBundle *bundle;
} FxFrozenPackage;
typedef struct {
    FxModuleInventory inventory;
    FxModuleIndex packages;
    FxIndexMemory memory;
    FxWlmNativeView native_views[FX_WLM_COMPONENTS];
    uint8_t native_sha256[FX_WLM_COMPONENTS][32];
    uint32_t native_count;
    uint32_t attempted,prepared,sealed,error,retained,bytes;
    int (*cancelled)(void *);void *cancel_context;
} FxFrozenModules;
FxResult fx_wlm_frozen_scan(FxFrozenModules *,const FxModuleDirectory *,
    const uint8_t stock_sha256[32],uint64_t capabilities,FxIndexMemory);
/* After a successful deep-copy composition, before MMU/SMP allocations.
 * Idempotent; preserves source identities and retained frontend bytes. */
void fx_wlm_frozen_release_native(FxFrozenModules *);
/* Seals the successfully committed cold snapshot (or a legacy-only one).
 * Also releases native inputs if composition did not release them earlier. */
FxResult fx_wlm_frozen_seal(FxFrozenModules *);
const FxModuleInventory *fx_wlm_frozen_inventory(const FxFrozenModules *);
#endif
