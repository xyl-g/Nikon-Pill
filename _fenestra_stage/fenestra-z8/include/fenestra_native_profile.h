/* SPDX-License-Identifier: GPL-3.0-only
 * Optional stock-native FUNCTION profile verifier, using the existing SDK.
 *
 * This wrapper is voluntary: raw FX_BIND_DECLARE still accepts declarations
 * without checking their original[] bytes. Sixteen matching bytes establish
 * a runtime entry window, not a complete function, signature or context proof.
 * INFO checks the core-advertised stock module identity; it does not hash the
 * entire running MAIN image. The generator verifies the original MAIN file.
 *
 * The native image interval and SHA are the module author's profile contract,
 * not an address sandbox. Use the generated stock MAIN interval/identity.
 * Heap payloads and WLM EL0 callbacks are outside this helper's contract; it
 * rejects its own/API/exchange pages as an additional defensive check.
 */
#ifndef FENESTRA_NATIVE_PROFILE_H
#define FENESTRA_NATIVE_PROFILE_H
#include "fenestra_sdk.h"
#include "fenestra_machine.h"
enum {FX_NATIVE_PROFILE_IDLE,FX_NATIVE_PROFILE_IDENTITY,FX_NATIVE_PROFILE_MACHINE,
      FX_NATIVE_PROFILE_READ,FX_NATIVE_PROFILE_DECLARE,FX_NATIVE_PROFILE_OPEN,
      FX_NATIVE_PROFILE_DONE,FX_NATIVE_PROFILE_FAILED};
typedef struct {
    FxSdkClient *client;
    FxBindingInfo entry;
    uint8_t stock_sha256[32];
    uint64_t native_begin,native_end,machine,handle;
    uint32_t stage,checked_bytes,owner,generation;
    FxResult error;
} FxNativeProfile;
static inline FxResult fx_native_profile_tick(FxNativeProfile *,const FxServiceModule *);
static inline int fx_native_profile_same_page(uint64_t a,uintptr_t b) {
    return (a>>12)==((uint64_t)b>>12) || ((a+15u)>>12)==((uint64_t)b>>12);
}
static inline FxResult fx_native_profile_fail(FxNativeProfile *p,FxResult error) {
    p->stage=FX_NATIVE_PROFILE_FAILED;p->error=error;p->handle=0u;return error;
}
/* Zero-initialize p once. The supplied client is shared with the rest of this
 * module, preserving the owner's strictly increasing request sequence. Until
 * tick reaches a terminal result, this helper owns that client's exchange.
 * Start submits nothing. FX_OK means initialized; tick's FX_OK means verified,
 * declared and opened. No operation in this helper CALLs the native function.
 */
static inline FxResult fx_native_profile_start(FxNativeProfile *p,FxSdkClient *client,
    const FxBindingInfo *entry,const uint8_t stock_sha256[32],uint64_t native_begin,uint64_t native_end) {
    if(!p || !client || !entry || !stock_sha256)return FX_ARGUMENT;
    if(client->pending || (p->stage>FX_NATIVE_PROFILE_IDLE && p->stage<FX_NATIVE_PROFILE_DONE))return FX_BUSY;
    uint32_t name=0u,original=0u,identity=0u;uint8_t identity_copy[32];
    while(name<sizeof(entry->name) && entry->name[name])++name;
    for(uint32_t i=0u;i<16u;++i)original|=entry->original[i];
    for(uint32_t i=0u;i<32u;++i){identity_copy[i]=stock_sha256[i];identity|=identity_copy[i];}
    if(!name || name==sizeof(entry->name) || entry->kind!=FX_BIND_FUNCTION ||
       (entry->abi!=FX_CALL_U64_8 && entry->abi!=FX_CALL_AAPCS64) || !entry->version ||
       entry->flags&~(uint32_t)FX_BIND_ESCAPE || entry->bytes || entry->available || entry->reserved ||
       !original || !identity || !native_begin || native_begin>=native_end ||
       entry->address<native_begin || entry->address>=native_end ||
       native_end-entry->address<16u || (entry->address&3u) ||
       fx_native_profile_same_page(entry->address,(uintptr_t)fx_native_profile_tick) ||
       fx_native_profile_same_page(entry->address,(uintptr_t)fx_native_profile_start) ||
       fx_native_profile_same_page(entry->address,(uintptr_t)p) ||
       fx_native_profile_same_page(entry->address,(uintptr_t)client))return FX_ARGUMENT;
    *p=(FxNativeProfile){.client=client,.entry=*entry,.native_begin=native_begin,.native_end=native_end,
        .stage=FX_NATIVE_PROFILE_IDENTITY};
    for(uint32_t i=0u;i<32u;++i)p->stock_sha256[i]=identity_copy[i];
    return FX_OK;
}
/* Returns BUSY while a copied request/reply remains in progress. A terminal
 * failure is sticky until an explicit new start; no DECLARE follows a failed
 * byte/identity check. BUSY replies retry only the same idempotent stage.
 * The returned handle belongs to this owner/generation. Module stop closes
 * leases normally. Calling it later still requires the real native ABI,
 * object lifetimes and appropriate worker/original.ui/intercepted context.
 */
static inline FxResult fx_native_profile_tick(FxNativeProfile *p,const FxServiceModule *api) {
    if(!p || !p->client || !api || api->base.bytes!=sizeof(*api) ||
       api->service_abi!=FX_SERVICE_ABI || !api->exchange)return FX_ARGUMENT;
    if(p->stage==FX_NATIVE_PROFILE_FAILED)return p->error;
    if(p->stage==FX_NATIVE_PROFILE_DONE) {
        if(p->owner!=api->owner || p->generation!=api->generation)return fx_native_profile_fail(p,FX_STALE);
        return FX_OK;
    }
    if(p->stage<FX_NATIVE_PROFILE_IDENTITY || p->stage>FX_NATIVE_PROFILE_OPEN)return FX_STATE;
    if((api->capabilities&(FX_SDK_BINDING|FX_SDK_NATIVE))!=(FX_SDK_BINDING|FX_SDK_NATIVE))
        return fx_native_profile_fail(p,FX_CAPABILITY);
    if(fx_native_profile_same_page(p->entry.address,(uintptr_t)api) ||
       fx_native_profile_same_page(p->entry.address,(uintptr_t)api->exchange))
        return fx_native_profile_fail(p,FX_ARGUMENT);
    if(!p->owner){p->owner=api->owner;p->generation=api->generation;}
    if(!p->owner || p->owner!=api->owner || p->generation!=api->generation)
        return fx_native_profile_fail(p,FX_STALE);
    if(p->client->pending) {
        FxServiceReply reply;FxResult taken=fx_sdk_take(p->client,api,&reply);
        if(taken==FX_BUSY)return FX_BUSY;
        if(taken!=FX_OK)return fx_native_profile_fail(p,taken);
        if(reply.result==FX_BUSY) { /* No acknowledged operation to advance. */ }
        else if(reply.result!=FX_OK)return fx_native_profile_fail(p,(FxResult)reply.result);
        else if(p->stage==FX_NATIVE_PROFILE_IDENTITY) {
            if(reply.bytes!=sizeof(FxPlatformInfo))return fx_native_profile_fail(p,FX_INTEGRITY);
            FxPlatformInfo info;const uint8_t *data=reply.data;uint8_t *out=(uint8_t *)(void *)&info;
            for(uint32_t i=0u;i<sizeof(info);++i)out[i]=data[i];
            if(info.abi!=FX_PLATFORM_ABI || info.bytes!=sizeof(info) || info.owner!=p->owner ||
               info.generation!=p->generation)return fx_native_profile_fail(p,FX_INTEGRITY);
            /* module_identity is returned in the four value words, while
             * info.identity is the platform/application identity convention. */
            const uint8_t *actual=(const uint8_t *)(const void *)reply.value;
            for(uint32_t i=0u;i<32u;++i)if(actual[i]!=p->stock_sha256[i])return fx_native_profile_fail(p,FX_INTEGRITY);
            p->stage=FX_NATIVE_PROFILE_MACHINE;
        } else if(p->stage==FX_NATIVE_PROFILE_MACHINE) {
            if(!reply.value[0] || reply.value[1]!=FX_BIND_DEVICE || reply.value[2]!=FX_CALL_DEVICE)
                return fx_native_profile_fail(p,FX_INTEGRITY);
            p->machine=reply.value[0];p->stage=FX_NATIVE_PROFILE_READ;
        } else if(p->stage==FX_NATIVE_PROFILE_READ) {
            if(reply.bytes || reply.value[0]!=(uint64_t)p->entry.original[p->checked_bytes])
                return fx_native_profile_fail(p,FX_INTEGRITY);
            if(++p->checked_bytes==16u)p->stage=FX_NATIVE_PROFILE_DECLARE;
        } else if(p->stage==FX_NATIVE_PROFILE_DECLARE)p->stage=FX_NATIVE_PROFILE_OPEN;
        else if(p->stage==FX_NATIVE_PROFILE_OPEN) {
            if(!reply.value[0] || reply.value[1]!=FX_BIND_FUNCTION || reply.value[2]!=p->entry.abi)
                return fx_native_profile_fail(p,FX_INTEGRITY);
            p->handle=reply.value[0];p->stage=FX_NATIVE_PROFILE_DONE;return FX_OK;
        } else return fx_native_profile_fail(p,FX_STATE);
    }
    FxResult submitted;
    if(p->stage==FX_NATIVE_PROFILE_IDENTITY)
        submitted=fx_sdk_platform(p->client,api,FX_BIND_INFO,0u,0u,0u,0,0u);
    else if(p->stage==FX_NATIVE_PROFILE_MACHINE)
        submitted=fx_sdk_open(p->client,api,"core.machine",1u);
    else if(p->stage==FX_NATIVE_PROFILE_READ) {
        FxMachineRequest request={.address=p->entry.address+p->checked_bytes,.bytes=1u};
        submitted=fx_sdk_platform(p->client,api,FX_BIND_CALL,p->machine,FX_MACHINE_READ,0u,&request,sizeof(request));
    } else if(p->stage==FX_NATIVE_PROFILE_DECLARE)submitted=fx_sdk_declare(p->client,api,&p->entry);
    else submitted=fx_sdk_open(p->client,api,p->entry.name,p->entry.version);
    return submitted==FX_OK || submitted==FX_BUSY?FX_BUSY:fx_native_profile_fail(p,submitted);
}
#endif
