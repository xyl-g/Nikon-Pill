/* Minimal diagnostic profile: status, echo and source/build identity only.
 * No memory, native-call, RTOS-monitor, Linux or module-loading capability. */
#include "diag.h"
#include "fenestra_memory.h"
#include "z8_diag_bindings.h"
#include "z8_diag_identity.h"
#define FN(a,t) ((t)(uintptr_t)(a))
static const FxPtpIdentity identity={311u,FX_Z8_DIAG_CAPS,2u|FX_PTP_FLAG_CONTROL,
    "Nikon Z8","C 3.11",fx_z8_debug_application,fx_z8_debug_application,fx_z8_debug_build};
const FxNativePtpProfile fx_native_ptp_profile={
    fx_z8_diag_ptp_state,fx_z8_diag_active,
    FN(FX_Z8_NATIVE_PTP_PAYLOAD,uint8_t *(*)(void)),
    FN(FX_Z8_NATIVE_PTP_CAPACITY,uint32_t (*)(void)),
    FN(FX_Z8_NATIVE_PTP_INCOMING,const uint8_t *(*)(void)),
    FN(FX_Z8_NATIVE_PTP_RESPONSE,void (*)(const uint8_t *,uint32_t,uint32_t,const void *,uint32_t)),
    FN(FX_Z8_NATIVE_PTP_SEND,uint32_t (*)(const uint8_t *,const uint8_t *,uint32_t,uint32_t)),
    fx_z8_ptp_original_lookup,fx_z8_ptp_original_session,fx_z8_ptp_original_operations,0,
    (const volatile uint32_t *)(uintptr_t)FX_Z8_DATA_PTP_SESSION,
    (const uint8_t *)(uintptr_t)FX_Z8_DATA_PTP_OPERATIONS,FX_Z8_DATA_PTP_OPERATIONS_CAPACITY};
void fx_z8_diag_ptp_init(FxPtp *p){fx_ptp_init_target(p,&identity);}
const FxNativePtpDescriptor *fx_z8_ptp_lookup(uint32_t opcode){return fx_native_ptp_lookup(opcode);}
void fx_z8_ptp_session(uint32_t session){fx_native_ptp_session(session);}
uint32_t fx_z8_ptp_operations(uint8_t *out,uint32_t capacity,const uint8_t *operations){
    return fx_native_ptp_operations(out,capacity,operations);
}
uint32_t fx_z8_diag_backend(void *context,uint16_t operation,const uint8_t *input,
                           uint32_t bytes,uint8_t *out,uint32_t *count){
    FxZ8DiagState *s=context;
    if(!s || atomic_load_explicit(&s->shutdown,memory_order_acquire)){*count=0u;return FX_PTP_CLOSED;}
    if(operation==FX_PTP_ECHO){memcpy(out,input,bytes);*count=bytes;return FX_PTP_OK;}
    if(operation==FX_PTP_STATUS && !bytes){
        memset(out,0,32u);fx_ptp_put32(out,311u);
        fx_ptp_put32(out+4,(uint32_t)fx_z8_diag_active());fx_ptp_put32(out+8,FX_Z8_DIAG_CAPS);
        fx_ptp_put32(out+12,atomic_load_explicit(&s->error,memory_order_acquire));
        fx_ptp_put32(out+16,atomic_load_explicit(&s->phase,memory_order_acquire));
        fx_ptp_put32(out+20,atomic_load_explicit(&s->worker_running,memory_order_acquire));
        *count=32u;return FX_PTP_OK;
    }
    *count=0u;return FX_PTP_UNSUPPORTED;
}
