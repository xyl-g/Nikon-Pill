/* SPDX-License-Identifier: GPL-3.0-only */
#include "fenestra_native_pack.h"
#include "fenestra_native_locale.h"

#define OPEN UINT32_C(0x80000000)
#define COUNT_MASK (OPEN - 1u)
#define WRITERS 16u
#define POSE_ADDRESS UINT64_C(0xa03228244)

static _Atomic uint32_t gate;
/* The stock sample/finalize calls serialize each individual writer. The low
 * two bits store its pose, while distinct writers may run on different CPUs. */
static _Atomic uintptr_t writers[WRITERS];
static const FxNativeLocale stock_locale = {FX_NATIVE_LOCALE_ABI, sizeof(FxNativeLocale), 36u,
    3136u, UINT64_C(0x4327c5a0), UINT64_C(0x439daf58)};
static const FxNativePackSymbol locale_export = {
    FX_NATIVE_LOCALE_SYMBOL, 1u, FX_NATIVE_PACK_SYMBOL_DATA, (uintptr_t)&stock_locale};

static int enter(void) {
    uint32_t state = atomic_load_explicit(&gate, memory_order_relaxed);
    while (state & OPEN) {
        if (atomic_compare_exchange_weak_explicit(&gate, &state, state + 1u,
                memory_order_acquire, memory_order_relaxed)) return 1;
    }
    return 0;
}

static void leave(void) {
    atomic_fetch_sub_explicit(&gate, 1u, memory_order_release);
}

static uint32_t pending(void *context) {
    (void)context;
    return atomic_load_explicit(&gate, memory_order_acquire) & COUNT_MASK;
}

static FxResult event(void *context, uint32_t op) {
    switch (op) {
        case FX_NATIVE_PACK_GUI_POLL: return FX_OK;
        case FX_NATIVE_PACK_PUBLISH:
        case FX_NATIVE_PACK_RESUME:
            if (pending(context)) return FX_BUSY;
            atomic_store_explicit(&gate, OPEN, memory_order_release);
            return FX_OK;
        case FX_NATIVE_PACK_STOP:
        case FX_NATIVE_PACK_FAULT:
        case FX_NATIVE_PACK_ABORT:
            atomic_fetch_and_explicit(&gate, COUNT_MASK, memory_order_acq_rel);
            if (pending(context)) return FX_BUSY;
            for (unsigned i = 0; i < WRITERS; ++i)
                atomic_store_explicit(&writers[i], 0u, memory_order_relaxed);
            return FX_OK;
        default: return FX_ARGUMENT;
    }
}

FxResult fx_native_pack_entry(const FxNativePackHost *host, FxNativePackExports *out) {
    if (!host || !out || host->abi != FX_NATIVE_PACK_ABI || host->bytes < sizeof(*host) || !host->lookup)
        return FX_ARGUMENT;
    *out = (FxNativePackExports){.abi = FX_NATIVE_PACK_ABI, .bytes = sizeof(*out),
        .context = &gate, .event = event, .pending = pending};
    /* The cold loader requires one locale descriptor even for a pack with
     * no new text IDs. Shared locale and Card WAV both stage before us. */
    uintptr_t locale = 0u;
    FxResult found = host->lookup(host->context, FX_NATIVE_LOCALE_SYMBOL, 1u, &locale);
    if (found == FX_CAPABILITY) {
        out->symbols = &locale_export;
        out->symbol_count = 1u;
    } else if (found != FX_OK || !locale) return FX_INTEGRITY;
    return FX_OK;
}

static _Atomic uintptr_t *find_writer(uintptr_t writer) {
    for (unsigned i = 0; i < WRITERS; ++i) {
        uintptr_t tagged = atomic_load_explicit(&writers[i], memory_order_acquire);
        if ((tagged & ~(uintptr_t)3u) == writer) return &writers[i];
    }
    return 0;
}

static void capture(uintptr_t writer) {
    uint32_t pose = *(const volatile uint32_t *)(uintptr_t)POSE_ADDRESS;
    /* Video-mode samples from the C3.11 workbench: grip up = 1, grip down = 2. */
    uintptr_t tagged = (pose == 1u || pose == 2u) ? writer | pose : 0u;
    _Atomic uintptr_t *slot = find_writer(writer);
    if (slot) {
        atomic_store_explicit(slot, tagged, memory_order_release);
    } else if (tagged) {
        for (unsigned i = 0; i < WRITERS; ++i) {
            uintptr_t empty = 0u;
            if (atomic_compare_exchange_strong_explicit(&writers[i], &empty, tagged,
                    memory_order_release, memory_order_relaxed)) break;
        }
    }
}

/* Called before every video sample is counted. The movie recorder takes its
 * writers from a pool without the generic create path, so the first sample
 * of each file (count 0) is the start of recording that every path shares. */
void fx_portrait_sample(uintptr_t writer, uint32_t count) {
    if (count || !writer || (writer & 3u) || !enter()) return;
    capture(writer);
    leave();
}

void fx_portrait_forget(uintptr_t writer) {
    if (!writer || (writer & 3u) || !enter()) return;
    _Atomic uintptr_t *slot = find_writer(writer);
    if (slot) atomic_store_explicit(slot, 0u, memory_order_release);
    leave();
}

void fx_portrait_matrix(uintptr_t writer, uint8_t *matrix, uint32_t width, uint32_t height) {
    if (!writer || (writer & 3u) || !matrix || !width || !height ||
            width > 32767u || height > 32767u || !enter()) return;
    _Atomic uintptr_t *slot = find_writer(writer);
    if (slot) {
        unsigned pose = (unsigned)(atomic_load_explicit(slot, memory_order_relaxed) & 3u);
        /* The caller's 36-byte buffer is the stock v0 video tkhd identity.
         * Check it before changing anything and keep the signed 16.16 and
         * 2.30 fields in big endian. Translation keeps the origin onscreen. */
        for (unsigned i = 0; i < 36u; ++i) {
            unsigned expected = i == 1u || i == 17u ? 1u : i == 32u ? 64u : 0u;
            if (matrix[i] != expected) goto done;
        }
        if (pose == 1u || pose == 2u) {
            uint32_t words[8] = {0u, 0x10000u, 0u, 0xffff0000u, 0u, 0u, height << 16, 0u};
            if (pose == 1u) {
                words[1] = 0xffff0000u;
                words[3] = 0x10000u;
                words[6] = 0u;
                words[7] = width << 16;
            }
            for (unsigned i = 0; i < 8u; ++i) {
                uint32_t word = words[i];
                matrix[4u*i] = (uint8_t)(word >> 24);
                matrix[4u*i + 1u] = (uint8_t)(word >> 16);
                matrix[4u*i + 2u] = (uint8_t)(word >> 8);
                matrix[4u*i + 3u] = (uint8_t)word;
            }
        }
    }
done:
    leave();
}
