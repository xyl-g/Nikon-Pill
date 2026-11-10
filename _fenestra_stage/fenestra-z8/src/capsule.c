/* SPDX-License-Identifier: GPL-3.0-only */
#include "fenestra_capsule.h"
#include "fenestra_memory.h"
#define MAGIC UINT32_C(0x46584341)
static int enter_gate(FxCapsuleControl *c){uint32_t z=0u;return atomic_compare_exchange_strong_explicit(&c->gate,&z,1u,memory_order_acquire,memory_order_relaxed);}
static void leave_gate(FxCapsuleControl *c){atomic_store_explicit(&c->gate,0u,memory_order_release);}
static int nonzero(const uint8_t *p,uint32_t n){uint32_t v=0u;for(uint32_t i=0u;i<n;++i)v|=p[i];return v!=0u;}
static int digest(const uint8_t *p,uint32_t n,const uint8_t expected[32]){uint8_t actual[32];fx_sha256(p,n,actual);return !memcmp(actual,expected,32u);}
static int overlap(uint64_t a,uint64_t an,uint64_t b,uint64_t bn){return a<b+bn && b<a+an;}
static void first_rollback_error(FxCapsuleControl *c,FxResult e){if(e!=FX_OK && !c->status.rollback_error)c->status.rollback_error=(uint32_t)e;}
FxResult fx_capsule_init(FxCapsuleControl *c,const uint8_t target[32],uint64_t dependency,FxCapsuleIO io) {
    if(!c || !target || !nonzero(target,32u))return FX_ARGUMENT;
    if(c->magic)return FX_STATE;
    memset(c,0,sizeof(*c));c->magic=MAGIC;atomic_init(&c->gate,0u);c->io=io;c->available_dependency=dependency;
    memcpy(c->target_sha256,target,32u);c->status=(FxCapsuleStatus){.abi=1u,.bytes=sizeof(FxCapsuleStatus),.failed_record=UINT32_MAX};return FX_OK;
}
FxResult fx_capsule_record(const FxCapsuleControl *c,uint32_t index,FxCapsuleRecord *out) {
    if(!c || c->magic!=MAGIC || !out || !c->package || index>=c->header.record_count)return FX_ARGUMENT;
    uint64_t at=(uint64_t)c->header.records_offset+(uint64_t)index*FX_CAPSULE_RECORD_BYTES;
    if(at>c->package_bytes || FX_CAPSULE_RECORD_BYTES>c->package_bytes-at)return FX_BOUNDS;
    memcpy(out,c->package+(uint32_t)at,sizeof(*out));return FX_OK;
}
static FxResult current_bytes(FxCapsuleControl *c,const FxCapsuleRecord *r,const uint8_t *expected) {
    uint8_t actual[256];uint32_t offset=0u;
    while(offset<r->bytes) {
        uint32_t count=r->bytes-offset;if(count>sizeof(actual))count=sizeof(actual);
        FxResult result=c->io.read(c->io.context,r->address+offset,actual,count);if(result!=FX_OK)return result;
        if(memcmp(actual,expected+offset,count))return FX_STALE;offset+=count;
    }
    return FX_OK;
}
static FxResult preimages(FxCapsuleControl *c) {
    for(uint32_t i=0u;i<c->header.record_count;++i) {
        FxCapsuleRecord r;FxResult result=fx_capsule_record(c,i,&r);if(result!=FX_OK)return result;
        result=c->io.admit(c->io.context,&c->header,i,&r);
        /* Unreachable previous IMAGE bytes are overwritten, never trusted. */
        if(result==FX_OK && !(c->images_unreachable && r.kind==FX_CAPSULE_IMAGE))
            result=current_bytes(c,&r,c->package+r.old_offset);
        if(result!=FX_OK){c->status.failed_record=i;return result;}
        c->status.validated_records=i+1u;
    }
    return FX_OK;
}
static FxResult validate(FxCapsuleControl *c) {
    static const uint8_t magic[8]={'F','X','C','A','P','S','1',0};
    if(c->package_bytes<FX_CAPSULE_HEADER_BYTES || c->package_bytes>FX_CAPSULE_MAX_PACKAGE)return FX_BOUNDS;
    if(!digest(c->package,c->package_bytes,c->package_sha256))return FX_INTEGRITY;
    memcpy(&c->header,c->package,sizeof(c->header));const FxCapsuleHeader *h=&c->header;
    if(memcmp(h->magic,magic,sizeof(magic)) || h->abi!=1u || h->header_bytes!=FX_CAPSULE_HEADER_BYTES ||
       h->package_bytes!=c->package_bytes || h->records_offset!=FX_CAPSULE_HEADER_BYTES || h->flags ||
       !h->record_count || h->record_count>FX_CAPSULE_MAX_RECORDS || !h->package_id)return FX_ARGUMENT;
    if(memcmp(h->target_sha256,c->target_sha256,32u))return FX_CAPABILITY;
    if(h->requires_id!=c->available_dependency)return FX_STATE;
    uint64_t table_end=(uint64_t)h->records_offset+(uint64_t)h->record_count*FX_CAPSULE_RECORD_BYTES;
    if(table_end>c->package_bytes)return FX_BOUNDS;
    if(!digest(c->package+FX_CAPSULE_HEADER_BYTES,c->package_bytes-FX_CAPSULE_HEADER_BYTES,h->body_sha256))return FX_INTEGRITY;
    uint64_t total=0u;uint32_t hooks=0u,entry_found=h->entry==0u;
    if(h->entry&3u)return FX_ARGUMENT;
    for(uint32_t i=0u;i<h->record_count;++i) {
        FxCapsuleRecord r;FxResult result=fx_capsule_record(c,i,&r);if(result!=FX_OK)return result;
        c->status.failed_record=i;
        if(!r.address || !r.bytes || r.bytes>FX_CAPSULE_MAX_RECORD || r.address>UINT64_MAX-r.bytes ||
           r.new_offset<table_end || r.old_offset<table_end || r.new_offset>c->package_bytes ||
           r.old_offset>c->package_bytes || r.bytes>c->package_bytes-r.new_offset ||
           r.bytes>c->package_bytes-r.old_offset)return FX_BOUNDS;
        for(uint32_t j=0u;j<4u;++j)if(r.reserved[j])return FX_ARGUMENT;
        if(r.kind==FX_CAPSULE_IMAGE) {
            if(hooks || r.flags&~3u || (r.flags&3u)==3u || r.depends_on!=FX_CAPSULE_NO_DEPENDENCY)return FX_ARGUMENT;
            ++c->status.image_records;
            if((r.flags&FX_CAPSULE_EXECUTE) && h->entry>=r.address && h->entry-r.address<r.bytes &&
               r.bytes-(h->entry-r.address)>=4u)entry_found=1u;
        } else if(r.kind==FX_CAPSULE_HOOK) {
            hooks=1u;
            if(r.flags!=FX_CAPSULE_INSTRUCTION && r.flags!=FX_CAPSULE_POINTER && r.flags!=FX_CAPSULE_DATA)return FX_ARGUMENT;
            if((r.flags==FX_CAPSULE_INSTRUCTION && ((r.address|r.bytes)&3u)) ||
               (r.flags==FX_CAPSULE_POINTER && ((r.address|r.bytes)&7u)))return FX_ARGUMENT;
            if(r.depends_on!=FX_CAPSULE_NO_DEPENDENCY) {
                FxCapsuleRecord parent;
                if(r.depends_on>=i || fx_capsule_record(c,r.depends_on,&parent)!=FX_OK || parent.kind!=FX_CAPSULE_IMAGE)return FX_ARGUMENT;
            }
        } else return FX_ARGUMENT;
        total+=r.bytes;if(total>FX_CAPSULE_MAX_WRITE)return FX_BUDGET;
        if(overlap(r.old_offset,r.bytes,r.new_offset,r.bytes))return FX_BOUNDS;
        for(uint32_t j=0u;j<i;++j) {
            FxCapsuleRecord p;result=fx_capsule_record(c,j,&p);if(result!=FX_OK)return result;
            if(overlap(r.address,r.bytes,p.address,p.bytes) || overlap(r.old_offset,r.bytes,p.old_offset,p.bytes) ||
               overlap(r.old_offset,r.bytes,p.new_offset,p.bytes) || overlap(r.new_offset,r.bytes,p.old_offset,p.bytes) ||
               overlap(r.new_offset,r.bytes,p.new_offset,p.bytes))return FX_BOUNDS;
        }
        if(!digest(c->package+r.old_offset,r.bytes,r.old_sha256) || !digest(c->package+r.new_offset,r.bytes,r.new_sha256))return FX_INTEGRITY;
    }
    if(total!=h->total_write_bytes || !entry_found)return FX_ARGUMENT;
    c->status.package_id=h->package_id;c->status.total_write_bytes=total;c->status.failed_record=UINT32_MAX;
    return preimages(c);
}
static FxResult prepare(FxCapsuleControl *c,const uint8_t *package,uint32_t bytes,const uint8_t expected[32],uint32_t unreachable) {
    if(!c || c->magic!=MAGIC || !package || !expected || !nonzero(expected,32u))return FX_ARGUMENT;
    if(!enter_gate(c))return FX_BUSY;
    if(c->status.phase!=FX_CAPSULE_EMPTY && c->status.phase!=FX_CAPSULE_FAILED){leave_gate(c);return FX_STATE;}
    c->status=(FxCapsuleStatus){.abi=1u,.bytes=sizeof(FxCapsuleStatus),.phase=FX_CAPSULE_VALIDATING,.failed_record=UINT32_MAX};
    c->package=package;c->package_bytes=bytes;c->attempted=c->entry_started=0u;c->lease=0u;c->images_unreachable=unreachable;
    memcpy(c->package_sha256,expected,32u);memcpy(c->status.package_sha256,expected,32u);
    FxResult result=(!c->io.read || !c->io.admit)?FX_CAPABILITY:validate(c);
    c->status.error=(uint32_t)result;c->status.phase=result==FX_OK?FX_CAPSULE_PREPARED:FX_CAPSULE_FAILED;
    leave_gate(c);return result;
}
FxResult fx_capsule_prepare(FxCapsuleControl *c,const uint8_t *package,uint32_t bytes,const uint8_t expected[32]) {
    return prepare(c,package,bytes,expected,0u);
}
FxResult fx_capsule_prepare_unreachable(FxCapsuleControl *c,const uint8_t *package,uint32_t bytes,const uint8_t expected[32]) {
    return prepare(c,package,bytes,expected,1u);
}
static FxResult apply(FxCapsuleControl *c,uint32_t index,uint32_t rollback) {
    FxCapsuleRecord r;FxResult result=fx_capsule_record(c,index,&r);if(result!=FX_OK)return result;
    const uint8_t *data=c->package+(rollback?r.old_offset:r.new_offset);
    result=c->io.apply(c->io.context,c->lease,&r,data,rollback);if(result!=FX_OK)return result;
    result=current_bytes(c,&r,data);return result==FX_STALE?FX_INTEGRITY:result;
}
static void rollback(FxCapsuleControl *c) {
    c->status.phase=FX_CAPSULE_ROLLING_BACK;uint32_t retained=0u,reachable=0u;
    /* Restore every attempted HOOK first, including a partially failed write.
     * If any ingress cannot be revoked, preserve every IMAGE and entry state. */
    for(uint32_t i=c->attempted;i>c->status.image_records;--i) {
        FxResult result=apply(c,i-1u,1u);first_rollback_error(c,result);
        if(result!=FX_OK){++retained;reachable=1u;}
    }
    if(!reachable && c->entry_started) {
        FxResult result=c->io.abort_entry(c->io.context,c->lease,c->header.entry);first_rollback_error(c,result);
        if(result!=FX_OK)reachable=1u;
    }
    uint32_t images=c->attempted<c->status.image_records?c->attempted:c->status.image_records;
    if(reachable)retained+=images;
    else for(uint32_t i=images;i>0u;--i) {
        FxResult result=apply(c,i-1u,1u);first_rollback_error(c,result);if(result!=FX_OK)++retained;
    }
    FxResult left=c->io.leave(c->io.context,c->lease,retained || reachable?FX_CAPSULE_LEAVE_RETAINED:FX_CAPSULE_LEAVE_ROLLBACK);
    first_rollback_error(c,left);c->lease=0u;
    if(left!=FX_OK && !retained)retained=c->attempted?c->attempted:1u;
    c->status.retained_records=retained;c->status.applied_records=retained;
    c->status.phase=retained || reachable || left!=FX_OK?FX_CAPSULE_RETAINED:FX_CAPSULE_FAILED;
}
FxResult fx_capsule_commit(FxCapsuleControl *c) {
    if(!c || c->magic!=MAGIC)return FX_ARGUMENT;
    if(!enter_gate(c))return FX_BUSY;
    if(c->status.phase!=FX_CAPSULE_PREPARED){leave_gate(c);return FX_STATE;}
    if(!c->io.enter || !c->io.apply || !c->io.leave ||
       (c->header.entry && (!c->io.stage_entry || !c->io.abort_entry || !c->io.publish_entry))) {
        c->status.phase=FX_CAPSULE_FAILED;c->status.error=FX_CAPABILITY;leave_gate(c);return FX_CAPABILITY;
    }
    c->status.phase=FX_CAPSULE_COMMITTING;c->status.validated_records=0u;c->status.failed_record=UINT32_MAX;
    FxResult result=digest(c->package,c->package_bytes,c->package_sha256)?FX_OK:FX_INTEGRITY;
    if(result==FX_OK)result=c->io.enter(c->io.context,&c->header,c->package,&c->lease);
    if(result!=FX_OK) {
        if(c->lease) {
            FxResult left=c->io.leave(c->io.context,c->lease,FX_CAPSULE_LEAVE_ROLLBACK);first_rollback_error(c,left);
            c->status.phase=left==FX_OK?FX_CAPSULE_FAILED:FX_CAPSULE_RETAINED;c->status.retained_records=left==FX_OK?0u:1u;c->lease=0u;
        } else c->status.phase=FX_CAPSULE_FAILED;
    } else if(!c->lease) {result=FX_INTEGRITY;c->status.phase=FX_CAPSULE_RETAINED;c->status.retained_records=1u;}
    else {
        /* The package and every current preimage are checked a SECOND time
         * after real native exclusion, before any code or hook publication. */
        if(!digest(c->package,c->package_bytes,c->package_sha256))result=FX_INTEGRITY;
        else result=preimages(c);
        for(uint32_t i=0u;result==FX_OK && i<c->status.image_records;++i) {
            c->status.failed_record=i;c->attempted=i+1u;result=apply(c,i,0u);if(result==FX_OK)++c->status.applied_records;
        }
        if(result==FX_OK && c->header.entry) {
            c->entry_started=1u;result=c->io.stage_entry(c->io.context,c->lease,c->header.entry);
        }
        for(uint32_t i=c->status.image_records;result==FX_OK && i<c->header.record_count;++i) {
            c->status.failed_record=i;c->attempted=i+1u;result=apply(c,i,0u);if(result==FX_OK)++c->status.applied_records;
        }
        if(result!=FX_OK)rollback(c);
        else {
            result=c->io.leave(c->io.context,c->lease,FX_CAPSULE_LEAVE_COMMIT);c->lease=0u;
            if(result!=FX_OK) {c->status.phase=FX_CAPSULE_RETAINED;c->status.retained_records=c->attempted;}
            else {
                if(c->header.entry)c->io.publish_entry(c->io.context,c->header.entry);
                c->status.phase=FX_CAPSULE_READY;c->status.failed_record=UINT32_MAX;
            }
        }
    }
    c->status.error=(uint32_t)result;leave_gate(c);return result;
}
static int survives(FxCapsuleControl *c,uint32_t index) {
    FxCapsuleRecord r;if(fx_capsule_record(c,index,&r)!=FX_OK)return 1;
    return r.kind==FX_CAPSULE_HOOK && (!c->io.survives || c->io.survives(c->io.context,&r));
}
/* Every surviving HOOK must be on one side; immutable IMAGE bytes must still
 * be new. WRITE images and boot-rebuilt HOOK targets are never compared. */
static FxResult ingress_state(FxCapsuleControl *c,uint32_t hooks_new) {
    for(uint32_t i=0u;i<c->header.record_count;++i) {
        FxCapsuleRecord r;FxResult result=fx_capsule_record(c,i,&r);if(result!=FX_OK)return result;
        if(r.kind==FX_CAPSULE_IMAGE && (r.flags&FX_CAPSULE_WRITE))continue;
        if(r.kind==FX_CAPSULE_HOOK && !survives(c,i))continue;
        uint32_t offset=r.kind==FX_CAPSULE_HOOK && !hooks_new?r.old_offset:r.new_offset;
        result=c->io.admit(c->io.context,&c->header,i,&r);
        if(result==FX_OK)result=current_bytes(c,&r,c->package+offset);
        if(result!=FX_OK){c->status.failed_record=i;return result;}
    }
    return FX_OK;
}
static FxResult ingress(FxCapsuleControl *c,uint32_t disarm) {
    const uint32_t first=c->status.image_records,count=c->header.record_count-first;
    const uint32_t previous=c->status.phase,next=disarm?FX_CAPSULE_DISARMED:FX_CAPSULE_READY;
    if(!c->io.enter || !c->io.apply || !c->io.leave)return FX_CAPABILITY;
    if(!digest(c->package,c->package_bytes,c->package_sha256))return FX_INTEGRITY;
    c->status.phase=disarm?FX_CAPSULE_ROLLING_BACK:FX_CAPSULE_COMMITTING;
    c->status.failed_record=UINT32_MAX;c->status.rollback_error=0u;c->lease=0u;
    FxResult result=c->io.enter(c->io.context,&c->header,c->package,&c->lease);
    if(result!=FX_OK || !c->lease) {
        FxResult left=c->lease?c->io.leave(c->io.context,c->lease,FX_CAPSULE_LEAVE_ROLLBACK):FX_OK;
        first_rollback_error(c,left);
        if(result==FX_OK){result=FX_INTEGRITY;left=FX_INTEGRITY;}
        c->lease=0u;c->status.phase=left==FX_OK?previous:FX_CAPSULE_RETAINED;
        c->status.error=(uint32_t)result;return result;
    }
    /* Both sides are checked again inside exclusion before any HOOK write.
     * A DISARMED repeat only re-proves the old side; it writes nothing. */
    uint32_t attempted=0u,retained=0u,outcome=FX_CAPSULE_LEAVE_COMMIT;
    result=ingress_state(c,disarm && previous==FX_CAPSULE_READY);
    while(result==FX_OK && previous!=next && attempted<count) {
        uint32_t index=disarm?first+count-1u-attempted:first+attempted;++attempted;
        if(!survives(c,index))continue;
        c->status.failed_record=index;result=apply(c,index,disarm);
    }
    if(result!=FX_OK) {
        /* Undo newest first, including a partially failed write. */
        outcome=FX_CAPSULE_LEAVE_ROLLBACK;
        while(attempted) {
            --attempted;uint32_t index=disarm?first+count-1u-attempted:first+attempted;
            if(!survives(c,index))continue;
            FxResult undo=apply(c,index,disarm?0u:1u);first_rollback_error(c,undo);if(undo!=FX_OK)++retained;
        }
        if(retained)outcome=FX_CAPSULE_LEAVE_RETAINED;
    }
    FxResult left=c->io.leave(c->io.context,c->lease,outcome);c->lease=0u;first_rollback_error(c,left);
    if(result==FX_OK)result=left;
    if(retained || left!=FX_OK) {c->status.phase=FX_CAPSULE_RETAINED;c->status.retained_records=retained?retained:1u;}
    else {c->status.phase=result==FX_OK?next:previous;if(result==FX_OK)c->status.failed_record=UINT32_MAX;}
    c->status.error=(uint32_t)result;return result;
}
FxResult fx_capsule_disarm(FxCapsuleControl *c) {
    if(!c || c->magic!=MAGIC)return FX_ARGUMENT;
    if(!enter_gate(c))return FX_BUSY;
    uint32_t phase=c->status.phase;
    FxResult result=phase==FX_CAPSULE_READY || phase==FX_CAPSULE_DISARMED?ingress(c,1u):FX_STATE;
    leave_gate(c);return result;
}
FxResult fx_capsule_rearm(FxCapsuleControl *c) {
    if(!c || c->magic!=MAGIC)return FX_ARGUMENT;
    if(!enter_gate(c))return FX_BUSY;
    FxResult result=c->status.phase==FX_CAPSULE_DISARMED?ingress(c,0u):FX_STATE;
    leave_gate(c);return result;
}
FxResult fx_capsule_query(FxCapsuleControl *c,FxCapsuleStatus *out) {
    if(!c || c->magic!=MAGIC || !out)return FX_ARGUMENT;
    if(!enter_gate(c))return FX_BUSY;
    *out=c->status;leave_gate(c);return FX_OK;
}
