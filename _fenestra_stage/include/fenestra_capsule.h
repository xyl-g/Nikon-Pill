/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FENESTRA_CAPSULE_H
#define FENESTRA_CAPSULE_H
#include "fenestra.h"
#include <stdatomic.h>
/* FNC v1 is little-endian. A capsule is one complete composition, not an
 * instruction to stack independently overlapping patches. Identifiers are
 * opaque; this manager has no camera feature names or native addresses. */
#define FX_CAPSULE_ABI 1u
#define FX_CAPSULE_HEADER_BYTES 128u
#define FX_CAPSULE_RECORD_BYTES 112u
#define FX_CAPSULE_MAX_PACKAGE (16u*1024u*1024u)
#define FX_CAPSULE_MAX_RECORDS 1024u
#define FX_CAPSULE_MAX_RECORD (1024u*1024u)
#define FX_CAPSULE_MAX_WRITE (8u*1024u*1024u)
#define FX_CAPSULE_NO_DEPENDENCY UINT32_MAX
enum {FX_CAPSULE_IMAGE=1u,FX_CAPSULE_HOOK};
enum {FX_CAPSULE_EXECUTE=1u,FX_CAPSULE_WRITE=2u,FX_CAPSULE_INSTRUCTION=4u,
      FX_CAPSULE_POINTER=8u,FX_CAPSULE_DATA=16u};
enum {FX_CAPSULE_EMPTY,FX_CAPSULE_VALIDATING,FX_CAPSULE_PREPARED,FX_CAPSULE_COMMITTING,
      FX_CAPSULE_ROLLING_BACK,FX_CAPSULE_READY,FX_CAPSULE_FAILED,FX_CAPSULE_RETAINED,
      FX_CAPSULE_DISARMED};
enum {FX_CAPSULE_LEAVE_ROLLBACK,FX_CAPSULE_LEAVE_COMMIT,FX_CAPSULE_LEAVE_RETAINED};
typedef struct {
    uint8_t magic[8];
    uint32_t abi,header_bytes,package_bytes,record_count,records_offset,flags;
    uint8_t target_sha256[32],body_sha256[32];
    uint64_t package_id,requires_id,total_write_bytes,entry;
} FxCapsuleHeader;
typedef struct {
    uint32_t kind,flags;
    uint64_t address;
    uint32_t bytes,new_offset,old_offset,depends_on;
    uint8_t old_sha256[32],new_sha256[32];
    uint32_t reserved[4];
} FxCapsuleRecord;
_Static_assert(sizeof(FxCapsuleHeader)==FX_CAPSULE_HEADER_BYTES,"capsule header ABI");
_Static_assert(sizeof(FxCapsuleRecord)==FX_CAPSULE_RECORD_BYTES,"capsule record ABI");
_Static_assert(offsetof(FxCapsuleHeader,entry)==120u && offsetof(FxCapsuleRecord,old_sha256)==32u,"capsule field ABI");
/* read/admit never mutate target memory. admit establishes the platform's
 * allowed native intervals, image permissions and exact hook/context policy.
 * enter must acquire REAL writer/CPU/DMA exclusion for all affected users,
 * returning a nonzero retained lease. Local IRQ masking alone is insufficient.
 * A failed enter may return a lease requiring leave(ROLLBACK).
 *
 * apply owns ALL target writes: IMAGE uploads must use all-root W^X, exact
 * translation/permission verification and cache synchronization; HOOK writes
 * need the platform's qualified instruction/pointer publication protocol.
 * rollback=1 restores the record's old bytes AND original permission contract.
 * An error may mean partial effects; the manager includes that record in undo.
 * The callbacks must never claim memcpy alone satisfies these contracts.
 *
 * stage_entry runs only after every IMAGE is sealed and read back. It may
 * initialize a staged registry but must publish no consumer references.
 * abort_entry revokes that stage, including a partially failed entry.
 * publish_entry is no-allocation/no-failure and release-publishes that registry
 * ONLY after leave(COMMIT) acknowledges success. A failed leave never reaches
 * it. All code, host objects and published references have reboot lifetime.
 */
typedef struct {
    void *context;
    FxResult (*read)(void *,uint64_t,void *,uint32_t);
    FxResult (*admit)(void *,const FxCapsuleHeader *,uint32_t,const FxCapsuleRecord *);
    FxResult (*enter)(void *,const FxCapsuleHeader *,const uint8_t *,uint64_t *);
    FxResult (*apply)(void *,uint64_t,const FxCapsuleRecord *,const uint8_t *,uint32_t rollback);
    FxResult (*leave)(void *,uint64_t,uint32_t outcome);
    FxResult (*stage_entry)(void *,uint64_t,uint64_t);
    FxResult (*abort_entry)(void *,uint64_t,uint64_t);
    void (*publish_entry)(void *,uint64_t);
    /* Optional, read-only: nonzero when a HOOK target keeps its bytes across
     * a retained-RAM boot. DISARM/REARM verify and switch only those; the
     * stock boot itself rebuilds the others, which stock code may also own
     * at runtime. NULL means every HOOK. */
    uint32_t (*survives)(void *,const FxCapsuleRecord *);
} FxCapsuleIO;
typedef struct {
    uint32_t abi,bytes,phase,error,rollback_error,failed_record;
    uint32_t validated_records,applied_records,retained_records,image_records;
    uint64_t package_id,total_write_bytes;
    uint8_t package_sha256[32];
} FxCapsuleStatus;
typedef struct {
    uint32_t magic;
    _Atomic uint32_t gate;
    FxCapsuleIO io;
    uint8_t target_sha256[32],package_sha256[32];
    uint64_t available_dependency,lease;
    const uint8_t *package;
    uint32_t package_bytes,attempted,entry_started;
    FxCapsuleHeader header;
    FxCapsuleStatus status;
    uint32_t images_unreachable;
} FxCapsuleControl;
/* Zero-initialize control once. target is the stock target identity supplied
 * by the verified platform. expected_package is the full-package SHA supplied
 * by the trusted boot selection. The body hash is independently checked too.
 * Retain immutable package bytes, control and IO context through reboot.
 * PREPARE checks the whole package and every runtime preimage before writes;
 * COMMIT repeats package/preimage checks inside acquired native exclusion.
 * There is intentionally no hot-unload/reset API for READY/RETAINED images. */
FxResult fx_capsule_init(FxCapsuleControl *,const uint8_t target[32],uint64_t available_dependency,FxCapsuleIO);
FxResult fx_capsule_prepare(FxCapsuleControl *,const uint8_t *,uint32_t,const uint8_t expected_package[32]);
/* Only for a platform that has independently proved no ingress into any
 * earlier IMAGE can execute (a verified revocation surviving retained RAM).
 * IMAGE preimages are then not required; every HOOK preimage still is. */
FxResult fx_capsule_prepare_unreachable(FxCapsuleControl *,const uint8_t *,uint32_t,const uint8_t expected_package[32]);
FxResult fx_capsule_commit(FxCapsuleControl *);
/* Ingress only, never an unload: IMAGE bytes, entry state and the published
 * registry keep their reboot lifetime. DISARM requires READY, every surviving
 * HOOK new and every immutable IMAGE new, then restores each surviving HOOK's
 * exact old bytes in reverse order inside acquired exclusion. From DISARMED
 * it only re-verifies that state. REARM requires DISARMED with every surviving
 * HOOK old and republishes the same new bytes in commit order. A write failure
 * restores the previous side; an unprovable restore is RETAINED with no
 * further transition. */
FxResult fx_capsule_disarm(FxCapsuleControl *);
FxResult fx_capsule_rearm(FxCapsuleControl *);
FxResult fx_capsule_query(FxCapsuleControl *,FxCapsuleStatus *);
/* Read-only record copy, also usable by an enter/admit callback. */
FxResult fx_capsule_record(const FxCapsuleControl *,uint32_t,FxCapsuleRecord *);
#endif
