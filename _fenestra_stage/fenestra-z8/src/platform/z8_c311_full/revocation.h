/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FX_Z8_REVOCATION_H
#define FX_Z8_REVOCATION_H
#include "fenestra.h"
#include "fenestra_memory.h"
/* Last page of the stock-reserved native arena; admit rejects every record
 * that touches it. The arena shares the code bank's retained-RAM lifetime and
 * a full bank reload zeroes it. A valid record states only that the previous
 * boot restored every surviving HOOK (code backing, resource roots) of its
 * READY transaction under exclusion before power-off. It grants no authority
 * to patch and is never a ledger. */
#define FX_Z8_REVOKED_PAGE UINT64_C(0x40bff000)
#define FX_Z8_REVOKED_AT UINT64_C(0x40bfffc0)
typedef struct {
    uint8_t magic[8];
    uint32_t abi,bytes;
    uint64_t package_id;
    uint8_t package_sha256[32];
    uint8_t check[8];
} FxZ8Revoked;
_Static_assert(sizeof(FxZ8Revoked)==64u && offsetof(FxZ8Revoked,check)==56u,"revocation record ABI");
static inline void fx_z8_revoked_check(const FxZ8Revoked *r,uint8_t out[8]) {
    uint8_t digest[32];fx_sha256(r,offsetof(FxZ8Revoked,check),digest);memcpy(out,digest,8u);
}
static inline void fx_z8_revoked_seal(FxZ8Revoked *r,uint64_t package_id,const uint8_t package_sha256[32]) {
    memset(r,0,sizeof(*r));memcpy(r->magic,"FXREVOK1",8u);r->abi=1u;r->bytes=(uint32_t)sizeof(*r);
    r->package_id=package_id;memcpy(r->package_sha256,package_sha256,32u);fx_z8_revoked_check(r,r->check);
}
static inline int fx_z8_revoked_valid(const FxZ8Revoked *r) {
    uint8_t check[8];
    if(memcmp(r->magic,"FXREVOK1",8u) || r->abi!=1u || r->bytes!=sizeof(*r))return 0;
    fx_z8_revoked_check(r,check);return !memcmp(check,r->check,8u);
}
#endif
