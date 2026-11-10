/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FENESTRA_NATIVE_PACK_H
#define FENESTRA_NATIVE_PACK_H
#include "fenestra_services.h"
/* Resident EL1 capsule entry ABI. All pointers below are trusted native image
 * or host-owned retained objects, never EL0 pointers. The loader validates every
 * callback against sealed EXEC segments and copies registry descriptors before
 * publication. Host context/table and all published images remain resident.
 * No core private structure layout is exposed to the pack. */
#define FX_NATIVE_PACK_ABI 1u
#define FX_NATIVE_PACK_NAME 64u
/* Resource op values intentionally match the copied binding service. */
enum {FX_NATIVE_PACK_ATTACH=1u,FX_NATIVE_PACK_DETACH,FX_NATIVE_PACK_CALL,FX_NATIVE_PACK_PANIC};
enum {FX_NATIVE_PACK_WORKER=0u,FX_NATIVE_PACK_ORIGINAL_UI=1u};
enum {FX_NATIVE_PACK_GUI_POLL=1u,FX_NATIVE_PACK_STOP,FX_NATIVE_PACK_FAULT,
      FX_NATIVE_PACK_RESUME,FX_NATIVE_PACK_ABORT,FX_NATIVE_PACK_PUBLISH};
enum {FX_NATIVE_PACK_FUNCTION=1u,FX_NATIVE_PACK_TABLE,FX_NATIVE_PACK_HOOK,FX_NATIVE_PACK_DEVICE};
enum {FX_NATIVE_PACK_CALL_U64_8=1u,FX_NATIVE_PACK_CALL_BYTES,FX_NATIVE_PACK_CALL_EVENTS,FX_NATIVE_PACK_CALL_DEVICE};
enum {FX_NATIVE_PACK_SYMBOL_FUNCTION=1u,FX_NATIVE_PACK_SYMBOL_DATA=2u};
typedef struct {
    uint32_t abi,bytes;
    void *context;
    /* Native aligned storage, retained through the image's published lifetime.
     * release may only reclaim an unpublished allocation after ABORT ACK. */
    void *(*allocate)(void *,uint32_t bytes,uint32_t alignment);
    void (*release)(void *,void *allocation);
    /* Exact named/versioned dependency export. No string-based native invocation.
     * Returned functions/data belong to verified resident dependency images. */
    FxResult (*lookup)(void *,const char *name,uint32_t version,uintptr_t *address);
} FxNativePackHost;
/* owner_key is an opaque, nonzero host identity stable for this lease. A pack
 * must not dereference it or infer a core owner layout. token is generation-
 * bound. The host pins image/context for queued/running calls and detach ACK. */
typedef FxResult (*FxNativePackResource)(void *context,uint32_t operation,
    uint64_t owner_key,uint64_t token,uint64_t *cookie,
    const FxServiceRequest *request,FxServiceReply *reply);
typedef struct {
    char name[FX_NATIVE_PACK_NAME];
    uint32_t version,kind,call_abi,capabilities,call_context,flags;
    FxNativePackResource resource;
    void *context;
} FxNativePackProvider;
typedef struct {
    char name[FX_NATIVE_PACK_NAME];
    uint32_t version,flags;
    uintptr_t address;
} FxNativePackSymbol;
typedef struct {
    uint32_t abi,bytes,provider_count,symbol_count;
    const FxNativePackProvider *providers;
    const FxNativePackSymbol *symbols;
    void *context;
    /* Entry is staged while all IMAGE records are sealed and HOOK publication
     * remains quiesced. PUBLISH is no-allocation/no-failure. ABORT must not
     * reclaim escaped native objects; BUSY means retain image/state. STOP/FAULT
     * close admission immediately and BUSY keeps native work in stop accounting.
     * GUI_POLL continues while stopping; RESUME only after completed stop.
     * Host serializes PUBLISH/ABORT/RESUME with service admission and native
     * hook publication. STOP may race GUI polling and cannot wait on that task. */
    FxResult (*event)(void *context,uint32_t event);
    uint32_t (*pending)(void *context);
} FxNativePackExports;
typedef FxResult (*FxNativePackEntry)(const FxNativePackHost *,FxNativePackExports *);
#endif
