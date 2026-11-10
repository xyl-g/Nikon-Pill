/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FENESTRA_NATIVE_LOCALE_H
#define FENESTRA_NATIVE_LOCALE_H
#include <stdint.h>
#define FX_NATIVE_LOCALE_ABI 1u
#define FX_NATIVE_LOCALE_CORE_TEXT_BASE 8192u
#define FX_NATIVE_LOCALE_SYMBOL "camera.locale.text"
/* A resident pack exports this immutable DATA symbol, version1. Its text_count
 * bounds the native signed offset arrays, independently of the host's wider
 * font/attribute bank and its native accepted-ID limit. */
typedef struct {
    uint32_t abi,bytes,languages,text_count;
    uint64_t offsets_directory,pools_directory;
} FxNativeLocale;
_Static_assert(sizeof(FxNativeLocale)==32u,"native locale descriptor ABI");
#endif
