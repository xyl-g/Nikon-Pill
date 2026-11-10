/* SPDX-License-Identifier: GPL-3.0-only
 * Derived from src/platform/z8_c311_full/photo_tools.c. All controls/bridge state
 * is resident pack/native heap storage; no callback points into an EL0 module.
 */
#include "internal.h"
enum {R_MAGIC=0,R_STATE=4,R_CANCEL=8,R_STATUS=12,R_SERIAL=16,R_CLAIMED=20,R_MODAL=24,
 R_WORKER=48,R_WORKER_ID=56,R_WORKERS=60,R_SPAWNED=64,R_ROWS=396,R_TOTAL=400,
 R_PHASE=404,R_FIRST=408,R_SECOND=412,R_CONTEXT_SERIAL=416,R_JOB_MODE=480,
 R_GROUP_INDEX=484,R_GROUP_COUNT=488,R_MERGED=492,R_TARGETS=496,R_ATTEMPTED=500,
 R_SECONDS=504,R_BROWSER_OPEN=1696,R_OPERATION=1700,R_MODAL_SERIAL=10464,R_RESULT=10468,
 R_REVIEW_SERIAL=10484,R_BYTES=10496,R_RESERVED=12288};

#define PSM_MAGIC UINT32_C(0x42534d50)
#define PSM_CANCELLED 7u
#define PSM_BUSY 17u
#define MENU_DELAY UINT32_C(0x0200171e)
static uint32_t rd(const FxControlsPhotoBridge *b,uint32_t at){return __atomic_load_n((uint32_t *)(b->profile->psm_runtime+at),__ATOMIC_ACQUIRE);}
static void wr(const FxControlsPhotoBridge *b,uint32_t at,uint32_t value){__atomic_store_n((uint32_t *)(b->profile->psm_runtime+at),value,__ATOMIC_RELEASE);}
static uintptr_t ptr(const FxControlsPhotoBridge *b,uint32_t at){return __atomic_load_n((uintptr_t *)(b->profile->psm_runtime+at),__ATOMIC_ACQUIRE);}
static void putptr(const FxControlsPhotoBridge *b,uint32_t at,uintptr_t value){__atomic_store_n((uintptr_t *)(b->profile->psm_runtime+at),value,__ATOMIC_RELEASE);}
static uint32_t get(const FxControlsPhotoBridge *b,uint32_t id){return ((uint32_t (*)(uint32_t))b->profile->get)(id);}
static void set(const FxControlsPhotoBridge *b,uint32_t id,uint32_t value){(void)((uint32_t (*)(uint32_t,uint32_t))b->profile->set)(id,value);}
static void restore(FxControlsPhotoBridge *b){if(b->held){set(b,MENU_DELAY,b->delay);((void (*)(uint32_t))b->profile->timer_set)(b->timer);b->held=0;}}
static int alive(FxControlsPhotoBridge *b){
    uint32_t id=rd(b,R_WORKER_ID);uintptr_t task=ptr(b,R_WORKER);
    if(!task||!id||id>0x300u)return 0;
    const uint8_t *entry=b->profile->task_registry+16u*id;
    if(__atomic_load_n((const uint32_t *)entry,__ATOMIC_ACQUIRE)!=id||
       __atomic_load_n((const uintptr_t *)(entry+8),__ATOMIC_ACQUIRE)!=task)return 0;
    return *(const uintptr_t *)(task+8)==b->profile->psm_worker;
}
static int spawn(FxControlsPhotoBridge *b){
    if(alive(b))return 1;
    uint32_t parameters[7]={2,40,32768,0,0,0,0};
    void *task=((void *(*)(uint32_t,uint32_t))b->profile->allocate)(64u,0u);
    if(!task)return 0;memset(task,0,64);memcpy((uint8_t *)parameters+12,"FxPSMerge",10);
    ((void (*)(void *,void (*)(void *),uint32_t *))b->profile->task_create)(task,(void (*)(void *))b->profile->psm_worker,parameters);
    int id=*(int *)task;if(id<=0){((void (*)(void *))b->profile->release)(task);return 0;}
    putptr(b,R_WORKER,(uintptr_t)task);wr(b,R_WORKER_ID,(uint32_t)id);wr(b,R_WORKERS,rd(b,R_WORKERS)+1u);
    ((void (*)(void *,void *))b->profile->task_start)(task,0);return 1;
    /* Original RTOS owns this permanent task/stack; its code dependency remains
     * resident. A finished merge is not permission to unload its worker image. */
}
static FxResult start(FxPhotoToolsControl *s){
    FxControlsPhotoBridge *b=s->native;if(!b)return FX_STATE;
    atomic_store_explicit(&b->pending,1u,memory_order_release);return FX_OK;
}
static void finish(FxPhotoToolsControl *s,FxControlsPhotoBridge *b,uint32_t status){
    restore(b);b->posted=0;
    fx_photo_tools_finish(s,status,rd(b,R_GROUP_COUNT),rd(b,R_TARGETS),rd(b,R_MERGED),rd(b,R_ATTEMPTED),rd(b,R_SECONDS));
}
static void poll(FxPhotoToolsControl *s){
    FxControlsPhotoBridge *b=s->native;if(!b||!atomic_load_explicit(&s->running,memory_order_acquire))return;
    if(atomic_exchange_explicit(&b->pending,0u,memory_order_acq_rel)){
        if(atomic_load(&s->cancel)||atomic_load(&s->revoked)){fx_photo_tools_finish(s,PSM_CANCELLED,0,0,0,0,0);return;}
        if(!rd(b,R_MAGIC)){
            for(uint32_t i=0;i<R_RESERVED;i+=4u)if(rd(b,i)){fx_photo_tools_finish(s,PSM_BUSY,0,0,0,0,0);return;}
            wr(b,R_MAGIC,PSM_MAGIC);
        }
        if(rd(b,R_MAGIC)!=PSM_MAGIC||rd(b,R_STATE)==1u||rd(b,R_MODAL)||rd(b,R_BROWSER_OPEN)||rd(b,R_SERIAL)==UINT32_MAX){
            fx_photo_tools_finish(s,PSM_BUSY,0,0,0,0,0);return;
        }
        if(!spawn(b)){fx_photo_tools_finish(s,5u,0,0,0,0,0);return;}
        b->delay=get(b,MENU_DELAY);b->timer=((uint32_t (*)(void))b->profile->timer_kind)();b->held=1;
        ((void (*)(uint32_t))b->profile->timer_hold)(UINT32_MAX);set(b,MENU_DELAY,8u);
        if(get(b,MENU_DELAY)!=8u){restore(b);fx_photo_tools_finish(s,1u,0,0,0,0,0);return;}
        b->serial=rd(b,R_SERIAL)+1u;b->posted=1u;b->posted_at=b->profile->ticks();
        wr(b,R_CANCEL,0u);wr(b,R_STATUS,0u);wr(b,R_OPERATION,0u);wr(b,R_JOB_MODE,s->mode);
        wr(b,R_GROUP_INDEX,0u);wr(b,R_GROUP_COUNT,0u);wr(b,R_MERGED,0u);wr(b,R_TARGETS,0u);wr(b,R_ATTEMPTED,0u);wr(b,R_SECONDS,0u);
        wr(b,R_ROWS,0u);wr(b,R_TOTAL,0u);wr(b,R_PHASE,0u);wr(b,R_MODAL_SERIAL,0u);
        if(atomic_load(&s->cancel)||atomic_load(&s->revoked)){restore(b);b->posted=0;fx_photo_tools_finish(s,PSM_CANCELLED,0,0,0,0,0);return;}
        wr(b,R_SERIAL,b->serial);wr(b,R_SPAWNED,b->serial);wr(b,R_STATE,1u);
        if(atomic_load(&s->cancel)||atomic_load(&s->revoked))wr(b,R_CANCEL,1u);
        return;
    }
    if(!b->posted)return;
    if(rd(b,R_SERIAL)!=b->serial){wr(b,R_CANCEL,1u);atomic_store(&s->fault,FX_STALE);return;}
    if(atomic_load(&s->cancel)||atomic_load(&s->revoked))wr(b,R_CANCEL,1u);
    atomic_store(&s->first,rd(b,R_FIRST));atomic_store(&s->second,rd(b,R_SECOND));atomic_store(&s->phase,rd(b,R_PHASE));
    atomic_store(&s->rows,rd(b,R_ROWS));atomic_store(&s->total,rd(b,R_TOTAL));atomic_store(&s->groups,rd(b,R_GROUP_COUNT));
    atomic_store(&s->targets,rd(b,R_TARGETS));atomic_store(&s->merged,rd(b,R_MERGED));atomic_store(&s->attempted,rd(b,R_ATTEMPTED));
    if(rd(b,R_STATE)!=1u){finish(s,b,rd(b,R_STATUS));return;}
    uint64_t rate=b->profile->tick_rate();
    if(rd(b,R_CLAIMED)!=b->serial&&rate&&b->profile->ticks()-b->posted_at>=5u*rate){
        uint32_t old=rd(b,R_CLAIMED);
        if(old!=b->serial&&__atomic_compare_exchange_n((uint32_t *)(b->profile->psm_runtime+R_CLAIMED),&old,b->serial,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE)){
            wr(b,R_STATUS,PSM_BUSY);wr(b,R_STATE,2u);finish(s,b,PSM_BUSY);
        }
    }
}
static void snapshot(FxPhotoToolsControl *s,FxPhotoToolsStatus *v){
    FxControlsPhotoBridge *b=s->native;v->release_mode=get(b,0x2126u);v->file_type=get(b,0x06000604u);v->raw_recording=get(b,0x06000608u);
}
static const FxPhotoToolsOps operations={start,poll,snapshot};
void fx_controls_photo_init(FxPhotoToolsControl *s,FxControlsPhotoBridge *b,const FxControlsProfile *p){
    fx_photo_tools_init(s,FX_PHOTO_PRECAPTURE|FX_PHOTO_MERGE,&operations);memset(b,0,sizeof(*b));b->profile=p;s->native=b;
}
FxResult fx_controls_photo_stop(FxPhotoToolsControl *s){
    if(!s)return FX_ARGUMENT;atomic_store(&s->revoked,1u);atomic_store(&s->cancel,1u);
    FxControlsPhotoBridge *b=s->native;
    if(b&&atomic_exchange_explicit(&b->pending,0u,memory_order_acq_rel))fx_photo_tools_finish(s,PSM_CANCELLED,0,0,0,0,0);
    else if(b&&atomic_load_explicit(&b->posted,memory_order_acquire)&&rd(b,R_SERIAL)==atomic_load(&b->serial)&&rd(b,R_STATE)==1u)
        wr(b,R_CANCEL,1u);
    return atomic_load_explicit(&s->running,memory_order_acquire)||atomic_load(&s->fault)?FX_BUSY:FX_OK;
}
FxResult fx_controls_photo_resume(FxPhotoToolsControl *s){
    if(!s)return FX_ARGUMENT;if(atomic_load_explicit(&s->running,memory_order_acquire)||atomic_load(&s->fault))return FX_BUSY;
    atomic_store(&s->cancel,0u);atomic_store(&s->revoked,0u);return FX_OK;
}
