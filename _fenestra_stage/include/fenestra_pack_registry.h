/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FENESTRA_PACK_REGISTRY_H
#define FENESTRA_PACK_REGISTRY_H
#include "fenestra_native_pack.h"
#include "fenestra_platform.h"
#include "fenestra_capsule.h"
#define FX_PACK_ENTRIES 32u
#define FX_PACK_PROVIDERS 32u
#define FX_PACK_SYMBOLS 64u
#define FX_PACK_BINDINGS 256u
#define FX_PACK_LEASES 256u
struct FxPackLease;
typedef struct {
    void *context;
    void *(*allocate)(void *,uint32_t,uint32_t);
    void (*release)(void *,void *);
    void (*wake_gui)(void *);
    int (*data)(void *,uintptr_t,uint32_t);
    int (*code)(void *,uintptr_t);
} FxPackRegistryIO;
typedef struct {
    FxNativePackExports exports;
    uint32_t invoked,validated,aborted,stop_ack,resume_ack;
} FxPackEntry;
typedef struct {
    FxPlatform platform;
    const FxPlatform *base;
    FxNativePackHost host;
    FxNativePackExports exports;
    FxNativePackProvider providers[FX_PACK_PROVIDERS];
    FxNativePackSymbol symbols[FX_PACK_SYMBOLS];
    FxNamedBinding bindings[FX_PACK_BINDINGS];
    FxPackRegistryIO io;
    _Atomic(struct FxPackLease *) leases;
    _Atomic uint32_t ready,stopping,lease_count,queued;
    _Atomic uint32_t event_lock,fault_requested;
    uint32_t staged,provider_count,symbol_count;
    /* Topologically ordered resident entries. STOP closes global admission
     * before visiting every owner; successful per-entry ACKs are not repeated.
     * Native hooks are not claimed to resume atomically across owners. */
    FxPackEntry entries[FX_PACK_ENTRIES];
    uint32_t entry_count,stop_event;
} FxPackRegistry;
void fx_pack_registry_init(FxPackRegistry *,const FxPlatform *,FxPackRegistryIO);
FxResult fx_pack_registry_stage(FxPackRegistry *,FxNativePackEntry);
FxResult fx_pack_registry_stage_many(FxPackRegistry *,const FxNativePackEntry *,uint32_t count);
FxResult fx_pack_registry_abort(FxPackRegistry *);
void fx_pack_registry_publish(FxPackRegistry *);
const FxPlatform *fx_pack_registry_platform(FxPackRegistry *,const FxPlatform *);
FxResult fx_pack_registry_event(FxPackRegistry *,uint32_t);
void fx_pack_registry_poll(FxPackRegistry *);
uint32_t fx_pack_registry_pending(FxPackRegistry *);
/* Platform-owned retained registry; native host supplies this getter. */
FxPackRegistry *fx_native_pack_registry(void);
const FxPlatform *fx_native_pack_platform(const FxPlatform *);
void fx_native_pack_gui_poll(void);
uint32_t fx_native_pack_gui_pending(void);
#endif
