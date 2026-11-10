/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FENESTRA_WLM_NATIVE_H
#define FENESTRA_WLM_NATIVE_H
#include "fenestra_capsule.h"
#define FX_WLM_NATIVE_COMPONENTS 32u
#define FX_WLM_NATIVE_HEADER 160u
#define FX_WLM_NATIVE_VARIANT 64u
#define FX_WLM_NATIVE_BLOCK 16u
#define FX_WLM_NATIVE_BLOCK_LIMIT 4096u
#define FX_WLM_NATIVE_VARIANTS 64u
#define FX_WLM_NATIVE_BLOCKS 32768u
#define FX_WLM_NATIVE_FILE_LIMIT (16u*1024u*1024u)
/* Component names are canonical zero-padded ASCII identifiers, <=15 chars.
 * selector_bit is zero for libraries or one generic installed-feature bit.
 * Rank determines deterministic entry staging; dependency lookup still fails
 * closed if an entry's required symbols have not already been staged. */
typedef struct {const uint8_t *data;uint32_t bytes;uint8_t id[16];uint32_t version;} FxWlmNativeView;
typedef struct {void *context;void *(*allocate)(void *,uint32_t);void (*release)(void *,void *);} FxWlmNativeMemory;
typedef struct {
    uint8_t *package;uint32_t bytes;uint8_t sha256[32];
    uint64_t entries[FX_WLM_NATIVE_COMPONENTS];uint32_t entry_count,selection,component_count;
} FxWlmNativePlan;
/* No target memory writes, original calls, mappings or executable publication.
 * Parses/hash-checks every component and chosen raw capsule, selects exactly
 * one variant/library, deduplicates exact records, rejects every conflicting
 * or partially overlapping target interval, and produces a private FNC plan.
 * Caller owns the returned immutable plan until reboot; input views may be
 * released after success. On error all temporary allocations are released. */
FxResult fx_wlm_native_compose(const FxWlmNativeView *,uint32_t,const uint8_t target[32],
                             uint64_t requires_id,FxWlmNativeMemory,FxWlmNativePlan *);
#endif
