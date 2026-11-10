/* Project-owned Z8 adaptation of Fenestra; upstream LICENSE and EXCEPTIONS apply.
 * No Nikon instruction bytes, addresses or tables are distributed here. */
#ifndef FENESTRA_Z8_C311_DIAG_H
#define FENESTRA_Z8_C311_DIAG_H
#include "fenestra_native_menu.h"
#include "fenestra_native_ptp.h"
#include <stdatomic.h>
#include <stddef.h>
#define FX_Z8_DIAG_MAGIC UINT32_C(0x465a3844)
#define FX_Z8_DIAG_VERSION 1u
#define FX_Z8_DIAG_CAPS ((1u<<(FX_PTP_STATUS-1u))|(1u<<(FX_PTP_ECHO-1u))|(1u<<(FX_PTP_IDENTITY-1u)))
enum { FX_Z8_DIAG_PREPARED=1u,FX_Z8_DIAG_STARTING,FX_Z8_DIAG_ACTIVE,FX_Z8_DIAG_CLOSED,FX_Z8_DIAG_FAILED };
typedef struct {
    FxNativeDescriptor original_descriptor;
    uint32_t magic,version;
    _Atomic uint32_t phase,ready,shutdown,error,worker_running;
    int32_t task_id;uint32_t reserved;
    uintptr_t task_stack;uint32_t task_stack_bytes,reserved2;
    FxPtp ptp;
} FxZ8DiagState;
/* The first member is a byte-identical clone of the stock descriptor. */
_Static_assert(sizeof(FxNativePage)==96 && offsetof(FxNativePage,descriptor)==80,"native page ABI must be independently checked for Z8");
_Static_assert(sizeof(FxNativeDescriptor)==64 && offsetof(FxZ8DiagState,original_descriptor)==0,"native descriptor anchor ABI");
FxZ8DiagState *fx_z8_diag_owner(void);
FxPtp *fx_z8_diag_ptp_state(void);
int fx_z8_diag_active(void);
void fx_z8_diag_ptp_init(FxPtp *);
uint32_t fx_z8_diag_backend(void *,uint16_t,const uint8_t *,uint32_t,uint8_t *,uint32_t *);
uint64_t fx_z8_system_dispatch(void *,uint32_t);
extern uint64_t fx_z8_system_original(void *,uint32_t);
extern const FxNativePtpDescriptor *fx_z8_ptp_original_lookup(uint32_t);
extern void fx_z8_ptp_original_session(uint32_t);
extern uint32_t fx_z8_ptp_original_operations(uint8_t *,uint32_t,const uint8_t *);
#endif
