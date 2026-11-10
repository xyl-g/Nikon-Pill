/* SPDX-License-Identifier: GPL-3.0-only
 * Control adapter for NikonFirmwarePatch a21fc8c framing, compiled into the
 * complete feature-suite core. This file installs no static patch blobs.
 */
#include "fenestra_framing.h"
extern uint32_t fx_z8_suite_capture_busy(void);
#define NRAW_VALUE UINT64_C(0xa03d5c290)
#define AP_VALUE UINT64_C(0xa03d5c0ad)
#define OG_VALUE UINT64_C(0xa03d5c02b)
#define RATE_PROPERTY UINT32_C(0x0500125b)
#define NATIVE_GET UINT64_C(0x410d5ec8)
#define NRAW_PROPERTY UINT32_C(0x05001261)
#define NATIVE_SET UINT64_C(0x403ec39c)
static uint32_t read32(uint64_t address){return atomic_load_explicit((_Atomic uint32_t *)(uintptr_t)address,memory_order_acquire);}
static uint32_t read8(uint64_t address){return atomic_load_explicit((_Atomic uint8_t *)(uintptr_t)address,memory_order_acquire);}
static FxResult native_read(void *context,FxFramingStatus *v) {
    (void)context;
    static const uint8_t grade[]={FX_FRAMING_NORMAL,FX_FRAMING_HIGH,FX_FRAMING_FINE,FX_FRAMING_BASIC};
    uint32_t nraw=read32(NRAW_VALUE),ap=read8(AP_VALUE),og=read8(OG_VALUE);
    if(nraw>=4u || ap>=8u || og>1u)return FX_INTEGRITY;
    v->capabilities=FX_FRAMING_NRAW_AVAILABLE|FX_FRAMING_DESQUEEZE_AVAILABLE|FX_FRAMING_NATIVE_OPEN_GATE;
    v->flags=fx_z8_suite_capture_busy()?FX_FRAMING_RECORDING:0u;
    v->nraw=grade[nraw];v->desqueeze=ap;v->open_gate=og;v->rate=((uint32_t (*)(uint32_t))(uintptr_t)NATIVE_GET)(RATE_PROPERTY);
    return FX_OK;
}
static FxResult native_write(void *context,uint32_t control,uint32_t value) {
    (void)context;
    if(fx_z8_suite_capture_busy())return FX_BUSY;
    if(control==FX_FRAMING_NRAW && value<4u) {
        static const uint8_t native_grade[]={1u,2u,0u,3u};
        /* F_SET owns the native datastore mutex and emits normal change
         * notifications. Writing only the DS mirror would skip this path. */
        uint32_t result=((uint32_t (*)(uint32_t,uint32_t))(uintptr_t)NATIVE_SET)(NRAW_PROPERTY,native_grade[value]);
        return result&1u?FX_OK:FX_STATE;
    }
    if(control==FX_FRAMING_DESQUEEZE && value<8u) {
        /* Exact upstream ap_sub_ok_hook contract. Per-frame reconcile in the
         * native backend notices this bank byte without another firmware call. */
        atomic_store_explicit((_Atomic uint8_t *)(uintptr_t)AP_VALUE,(uint8_t)value,memory_order_release);
        return FX_OK;
    }
    return FX_ARGUMENT;
}
void fx_z8_framing_init(FxFramingControl *s){fx_framing_init(s,0,native_read,native_write);}
FxResult fx_z8_framing_resource(void *context,uint32_t op,const FxNamedBinding *binding,FxPlatformOwner *o,
    uint64_t token,uint64_t *cookie,const FxServiceRequest *q,FxServiceReply *a) {
    (void)context;(void)binding;return fx_framing_resource(fx_z8_framing_control(),op,o,token,cookie,q,a);
}
const uint32_t fx_z8_framing_metadata[]={UINT32_C(0x46584652),FX_FRAMING_ABI,sizeof(FxFramingControl),
    offsetof(FxFramingControl,owner),offsetof(FxFramingControl,revoked),sizeof(FxFramingStatus),sizeof(FxFramingSelection)};
