/* Diagnostic access for failed cold startup; never a native recovery policy. */
#ifndef FX_Z8_POWER_DIAGNOSTIC_H
#define FX_Z8_POWER_DIAGNOSTIC_H
#include "fenestra.h"
#include "fenestra_ptp.h"
#include "fenestra_startup.h"
#include "cold_bootstrap.h"
enum {FX_Z8_DEBUG_OFF,FX_Z8_DEBUG_FULL,FX_Z8_DEBUG_READ_ONLY};
typedef struct {
    uint32_t activation,shutdown,startup_shutdown;
    uint32_t cold_phase,cold_error,startup_phase;
    uint64_t fault_record;
} FxZ8DiagnosticGate;
static inline uint32_t fx_z8_diagnostic_mode(const FxZ8DiagnosticGate *s) {
    if(!s || s->shutdown || s->startup_shutdown || s->fault_record)return FX_Z8_DEBUG_OFF;
    if(s->activation)return FX_Z8_DEBUG_FULL;
    /* Ordinary fault/Panic and cancelled shutdown never become debug-ready.
     * Only an inactive, non-stopping terminal cold failure or card inspection
     * error after successful cold preparation permits local read-only access. */
    if((s->cold_phase==FX_COLD_FAILED && s->cold_error!=FX_OK) ||
       (s->cold_phase==FX_COLD_READY && s->startup_phase==FX_STARTUP_IO_ERROR))
        return FX_Z8_DEBUG_READ_ONLY;
    return FX_Z8_DEBUG_OFF;
}
static inline int fx_z8_diagnostic_request(uint32_t mode,uint16_t operation,
                                          const uint8_t *data,uint32_t bytes) {
    if(mode==FX_Z8_DEBUG_FULL)return 1;
    if(mode!=FX_Z8_DEBUG_READ_ONLY)return 0;
    if(operation==FX_PTP_STATUS)return bytes==0u;
    if(operation==FX_PTP_ECHO || operation==FX_PTP_IDENTITY)return 1;
    if(operation!=FX_PTP_RTOS_MONITOR || !data || bytes<4u)return 0;
    uint32_t op=(uint32_t)data[0]|(uint32_t)data[1]<<8|
                (uint32_t)data[2]<<16|(uint32_t)data[3]<<24;
    return op==0u || op==1u || op==2u || op==4u;
}
static inline uint32_t fx_z8_diagnostic_result(uint32_t cold_phase,uint32_t cold_error,
                                             uint32_t startup_error,uint32_t core_error) {
    /* Terminal bootstrap error is the originating failure. A zero cold result
     * must not hide a later journal/card or module error. */
    if((cold_phase==FX_COLD_FAILED || cold_phase==FX_COLD_CANCELLED) && cold_error)
        return cold_error;
    return startup_error?startup_error:core_error;
}
/* Fully immutable UTF-16 storage: concurrent native text/GUI readers can keep
 * these pointers without locks, allocations, writable scratch or slot reuse.
 * E is the complete two-digit hex FxResult enum (00..0D); B is cold phase0..7.
 * Unexpected/corrupt values are explicitly unknown, never truncated to a valid
 * but incorrect diagnostic code. */
_Static_assert(FX_INTERRUPTED==13 && FX_COLD_CANCELLED==7,"diagnostic enum coverage");
#define FX_Z8_DIAG_ROW(b) { \
 u"Card error E00 B" b,u"Card error E01 B" b,u"Card error E02 B" b, \
 u"Card error E03 B" b,u"Card error E04 B" b,u"Card error E05 B" b, \
 u"Card error E06 B" b,u"Card error E07 B" b,u"Card error E08 B" b, \
 u"Card error E09 B" b,u"Card error E0A B" b,u"Card error E0B B" b, \
 u"Card error E0C B" b,u"Card error E0D B" b }
static inline const uint16_t *fx_z8_diagnostic_label(uint32_t error,uint32_t cold_phase,
                                                   uint32_t *bytes) {
    static const uint16_t labels[8][14][18]={
        FX_Z8_DIAG_ROW("0"),FX_Z8_DIAG_ROW("1"),FX_Z8_DIAG_ROW("2"),FX_Z8_DIAG_ROW("3"),
        FX_Z8_DIAG_ROW("4"),FX_Z8_DIAG_ROW("5"),FX_Z8_DIAG_ROW("6"),FX_Z8_DIAG_ROW("7")};
    static const uint16_t unknown[]=u"Card error E?? B?";
    if(bytes)*bytes=34u;
    return error<=13u && cold_phase<=7u?labels[cold_phase][error]:unknown;
}
#undef FX_Z8_DIAG_ROW
/* A stage records the last loader boundary, not a progress/liveness promise.
 * Before a potentially blocking call E05 means no result has returned yet.
 * A single atomic word keeps the stage and complete FxResult inseparable. */
enum {
    FX_Z8_RESUME_QUEUED,FX_Z8_RESUME_STOP_DONE,FX_Z8_RESUME_STOP_ACK,
    FX_Z8_RESUME_GATE,FX_Z8_RESUME_TICKET,FX_Z8_RESUME_GUI_MUTEX,
    FX_Z8_RESUME_MANAGER,FX_Z8_RESUME_NATIVE,FX_Z8_RESUME_PHOTO,
    FX_Z8_RESUME_PUBLISH,FX_Z8_RESUME_DONE,FX_Z8_RESUME_BLOCKED
};
static inline uint64_t fx_z8_resume_diagnostic_word(uint32_t stage,uint32_t error) {
    return (uint64_t)stage<<32|error;
}
#define FX_Z8_RESUME_ROW(r) { \
 u"Resuming R" r " E00",u"Resuming R" r " E01",u"Resuming R" r " E02", \
 u"Resuming R" r " E03",u"Resuming R" r " E04",u"Resuming R" r " E05", \
 u"Resuming R" r " E06",u"Resuming R" r " E07",u"Resuming R" r " E08", \
 u"Resuming R" r " E09",u"Resuming R" r " E0A",u"Resuming R" r " E0B", \
 u"Resuming R" r " E0C",u"Resuming R" r " E0D" }
static inline const uint16_t *fx_z8_resume_diagnostic_label(uint64_t record,uint32_t *bytes) {
    static const uint16_t labels[12][14][16]={
        FX_Z8_RESUME_ROW("0"),FX_Z8_RESUME_ROW("1"),FX_Z8_RESUME_ROW("2"),
        FX_Z8_RESUME_ROW("3"),FX_Z8_RESUME_ROW("4"),FX_Z8_RESUME_ROW("5"),
        FX_Z8_RESUME_ROW("6"),FX_Z8_RESUME_ROW("7"),FX_Z8_RESUME_ROW("8"),
        FX_Z8_RESUME_ROW("9"),FX_Z8_RESUME_ROW("A"),FX_Z8_RESUME_ROW("B")};
    static const uint16_t unknown[]=u"Resuming R? E??";
    uint32_t stage=(uint32_t)(record>>32),error=(uint32_t)record;
    if(bytes)*bytes=30u;
    return stage<=FX_Z8_RESUME_BLOCKED && error<=13u?labels[stage][error]:unknown;
}
#undef FX_Z8_RESUME_ROW
#endif
