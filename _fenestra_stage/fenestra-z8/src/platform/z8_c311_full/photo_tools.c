/* SPDX-License-Identifier: GPL-3.0-only
 * Bridge to the reviewed photo-tools implementation at a21fc8c04b6dd863439c4192d387b755633287f6.
 * Start/finish publication is GUI-task-only. All encoding/FS/NEFX work stays in
 * the original core-owned permanent worker. No callback points into WLM code.
 */
#include "fenestra_photo_tools.h"
#include "fenestra_memory.h"
#ifdef FX_Z8_FEATURE_SUITE
#include "z8_feature_suite_symbols.h"
/* Offsets compiled with clang from the exact upstream psm_runtime. Runtime is
 * 10496 bytes in a 12288-byte cold-start copy. See host/z8_photo_tools_profile.py. */
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
#ifdef FX_PHOTO_TOOLS_SYNTHETIC
extern uint8_t *fx_photo_test_runtime,*fx_photo_test_registry;
extern void *fx_photo_test_allocate(uint32_t,uint32_t);
extern void fx_photo_test_free(void *);
extern void fx_photo_test_task_create(void *,void (*)(void *),uint32_t *);
extern void fx_photo_test_task_start(void *,void *);
extern uint32_t fx_photo_test_get(uint32_t);
extern void fx_photo_test_set(uint32_t,uint32_t);
extern uint32_t fx_photo_test_timer_kind(void);
extern void fx_photo_test_timer_hold(uint32_t);
extern void fx_photo_test_timer_set(uint32_t);
extern uint64_t fx_photo_test_ticks(void);
extern uint64_t fx_photo_test_rate(void);
#define RT fx_photo_test_runtime
#define REGISTRY fx_photo_test_registry
#define ALLOC fx_photo_test_allocate
#define FREE fx_photo_test_free
#define TASK_CREATE fx_photo_test_task_create
#define TASK_START fx_photo_test_task_start
#define GET fx_photo_test_get
#define SET fx_photo_test_set
#define TIMER_KIND fx_photo_test_timer_kind
#define TIMER_HOLD fx_photo_test_timer_hold
#define TIMER_SET fx_photo_test_timer_set
#define ticks fx_photo_test_ticks
#define tick_rate fx_photo_test_rate
#else
#define RT ((uint8_t *)(uintptr_t)UINT64_C(0xa0ffed000))
#define REGISTRY ((uint8_t *)(uintptr_t)UINT64_C(0xa03203e08))
#define ALLOC ((void *(*)(uint32_t,uint32_t))(uintptr_t)UINT64_C(0x40d2c408))
#define FREE ((void (*)(void *))(uintptr_t)UINT64_C(0x40d2c448))
#define TASK_CREATE ((void (*)(void *,void (*)(void *),uint32_t *))(uintptr_t)UINT64_C(0x40d2d498))
#define TASK_START ((void (*)(void *,void *))(uintptr_t)UINT64_C(0x40d2d5cc))
#define GET ((uint32_t (*)(uint32_t))(uintptr_t)UINT64_C(0x403ec398))
#define SET ((void (*)(uint32_t,uint32_t))(uintptr_t)UINT64_C(0x403ec39c))
#define TIMER_KIND ((uint32_t (*)(void))(uintptr_t)UINT64_C(0x403884a0))
#define TIMER_HOLD ((void (*)(uint32_t))(uintptr_t)UINT64_C(0x403884b8))
#define TIMER_SET ((void (*)(uint32_t))(uintptr_t)UINT64_C(0x40388300))
static uint64_t ticks(void){uint64_t n;__asm__ volatile("mrs %0,cntvct_el0":"=r"(n));return n;}
static uint64_t tick_rate(void){uint64_t n;__asm__ volatile("mrs %0,cntfrq_el0":"=r"(n));return n&UINT32_MAX;}
#endif
static uint32_t rd(uint32_t at){return __atomic_load_n((uint32_t *)(RT+at),__ATOMIC_ACQUIRE);}
static void wr(uint32_t at,uint32_t value){__atomic_store_n((uint32_t *)(RT+at),value,__ATOMIC_RELEASE);}
static uintptr_t ptr(uint32_t at){return __atomic_load_n((uintptr_t *)(RT+at),__ATOMIC_ACQUIRE);}
static void putptr(uint32_t at,uintptr_t value){__atomic_store_n((uintptr_t *)(RT+at),value,__ATOMIC_RELEASE);}
typedef struct { _Atomic uint32_t pending,posted,serial;uint32_t held,delay,timer;uint64_t posted_at;} Bridge;
static void restore(Bridge *b){if(b->held){SET(MENU_DELAY,b->delay);TIMER_SET(b->timer);b->held=0;}}
static int alive(void){
    uint32_t id=rd(R_WORKER_ID);uintptr_t task=ptr(R_WORKER);
    if(!task||!id||id>0x300u)return 0;
    const uint8_t *entry=REGISTRY+16u*id;
    if(__atomic_load_n((const uint32_t *)entry,__ATOMIC_ACQUIRE)!=id||
       __atomic_load_n((const uintptr_t *)(entry+8),__ATOMIC_ACQUIRE)!=task)return 0;
    return *(const uintptr_t *)(task+8)==(uintptr_t)FX_Z8_PSM_WORKER;
}
static int spawn(void){
    if(alive())return 1;
    uint32_t parameters[7]={2,40,32768,0,0,0,0};
    void *task=ALLOC(64,0);if(!task)return 0;memset(task,0,64);memcpy((uint8_t *)parameters+12,"FxPSMerge",10);
    TASK_CREATE(task,(void (*)(void *))(uintptr_t)FX_Z8_PSM_WORKER,parameters);
    int id=*(int *)task;if(id<=0){FREE(task);return 0;}
    putptr(R_WORKER,(uintptr_t)task);wr(R_WORKER_ID,(uint32_t)id);wr(R_WORKERS,rd(R_WORKERS)+1u);
    TASK_START(task,0);return 1; /* The OS owns task/stack until reboot. */
}
static FxResult start(FxPhotoToolsControl *s){
    Bridge *b=s->native;if(!b)return FX_BUDGET;
    atomic_store_explicit(&b->pending,1u,memory_order_release);return FX_OK;
}
static void finish(FxPhotoToolsControl *s,Bridge *b,uint32_t status){
    restore(b);b->posted=0;
    fx_photo_tools_finish(s,status,rd(R_GROUP_COUNT),rd(R_TARGETS),rd(R_MERGED),rd(R_ATTEMPTED),rd(R_SECONDS));
}
static void poll(FxPhotoToolsControl *s){
    Bridge *b=s->native;if(!b||!atomic_load_explicit(&s->running,memory_order_acquire))return;
    if(atomic_exchange_explicit(&b->pending,0u,memory_order_acq_rel)){
        if(atomic_load(&s->cancel)||atomic_load(&s->revoked)){fx_photo_tools_finish(s,PSM_CANCELLED,0,0,0,0,0);return;}
        if(!rd(R_MAGIC)){
            /* Only the known all-zero cold-start reservation may be initialized. */
            for(uint32_t i=0;i<R_RESERVED;i+=4u)if(rd(i)){fx_photo_tools_finish(s,PSM_BUSY,0,0,0,0,0);return;}
            wr(R_MAGIC,PSM_MAGIC);
        }
        if(rd(R_MAGIC)!=PSM_MAGIC||rd(R_STATE)==1u||rd(R_MODAL)||rd(R_BROWSER_OPEN)||rd(R_SERIAL)==UINT32_MAX){
            fx_photo_tools_finish(s,PSM_BUSY,0,0,0,0,0);return;
        }
        if(!spawn()){fx_photo_tools_finish(s,5u,0,0,0,0,0);return;}
        b->delay=GET(MENU_DELAY);b->timer=TIMER_KIND();b->held=1;TIMER_HOLD(UINT32_MAX);SET(MENU_DELAY,8u);
        if(GET(MENU_DELAY)!=8u){restore(b);fx_photo_tools_finish(s,1u,0,0,0,0,0);return;}
        b->serial=rd(R_SERIAL)+1u;b->posted=1u;b->posted_at=ticks();
        wr(R_CANCEL,0u);wr(R_STATUS,0u);wr(R_OPERATION,0u);wr(R_JOB_MODE,s->mode);
        wr(R_GROUP_INDEX,0u);wr(R_GROUP_COUNT,0u);wr(R_MERGED,0u);wr(R_TARGETS,0u);wr(R_ATTEMPTED,0u);wr(R_SECONDS,0u);
        wr(R_ROWS,0u);wr(R_TOTAL,0u);wr(R_PHASE,0u);wr(R_MODAL_SERIAL,0u);
        if(atomic_load(&s->cancel)||atomic_load(&s->revoked)){restore(b);b->posted=0;fx_photo_tools_finish(s,PSM_CANCELLED,0,0,0,0,0);return;}
        wr(R_SERIAL,b->serial);wr(R_SPAWNED,b->serial);wr(R_STATE,1u);
        if(atomic_load(&s->cancel)||atomic_load(&s->revoked))wr(R_CANCEL,1u);
        return; /* Native worker takes this serial exactly once. */
    }
    if(!b->posted)return;
    if(rd(R_SERIAL)!=b->serial){
        /* Unknown replacement of an active job: cancel and retain ownership. */
        wr(R_CANCEL,1u);atomic_store(&s->fault,FX_STALE);return;
    }
    if(atomic_load(&s->cancel)||atomic_load(&s->revoked))wr(R_CANCEL,1u);
    atomic_store(&s->first,rd(R_FIRST));atomic_store(&s->second,rd(R_SECOND));atomic_store(&s->phase,rd(R_PHASE));
    atomic_store(&s->rows,rd(R_ROWS));atomic_store(&s->total,rd(R_TOTAL));atomic_store(&s->groups,rd(R_GROUP_COUNT));
    atomic_store(&s->targets,rd(R_TARGETS));atomic_store(&s->merged,rd(R_MERGED));atomic_store(&s->attempted,rd(R_ATTEMPTED));
    if(rd(R_STATE)!=1u){finish(s,b,rd(R_STATUS));return;}
    uint64_t rate=tick_rate();
    if(rd(R_CLAIMED)!=b->serial&&rate&&ticks()-b->posted_at>=5u*rate){
        /* Revoke before claim; a late native worker sees claimed==serial and skips. */
        uint32_t old=rd(R_CLAIMED);
        if(old!=b->serial&&__atomic_compare_exchange_n((uint32_t *)(RT+R_CLAIMED),&old,b->serial,0,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE)){
            wr(R_STATUS,PSM_BUSY);wr(R_STATE,2u);finish(s,b,PSM_BUSY);
        }
    }
}
static void snap(FxPhotoToolsControl *s,FxPhotoToolsStatus *v){
    (void)s;v->release_mode=GET(0x2126u);v->file_type=GET(0x06000604u);v->raw_recording=GET(0x06000608u);
}
static const FxPhotoToolsOps ops={start,poll,snap};
void fx_z8_photo_tools_init(FxPhotoToolsControl *s){
    fx_photo_tools_init(s,FX_PHOTO_PRECAPTURE|FX_PHOTO_MERGE,&ops);
    s->native=ALLOC(sizeof(Bridge),0);if(s->native)memset(s->native,0,sizeof(Bridge));else s->supported=FX_PHOTO_PRECAPTURE;
}
#else
void fx_z8_photo_tools_init(FxPhotoToolsControl *s){fx_photo_tools_init(s,0u,0);}
#endif
uint32_t fx_z8_photo_tools_pending(FxPhotoToolsControl *s){return s?atomic_load_explicit(&s->running,memory_order_acquire):0u;}
void fx_z8_photo_tools_poll(FxPhotoToolsControl *s){fx_photo_tools_poll(s);}
FxResult fx_z8_photo_tools_resume(FxPhotoToolsControl *s){
    if(!s)return FX_ARGUMENT;
    if(atomic_load_explicit(&s->running,memory_order_acquire)||atomic_load(&s->fault))return FX_BUSY;
    atomic_store(&s->cancel,0u);atomic_store(&s->revoked,0u);return FX_OK;
}
FxResult fx_z8_photo_tools_stop(FxPhotoToolsControl *s){
    if(!s)return FX_ARGUMENT;atomic_store(&s->revoked,1u);atomic_store(&s->cancel,1u);
#ifdef FX_Z8_FEATURE_SUITE
    Bridge *b=s->native;
    if(b&&atomic_exchange_explicit(&b->pending,0u,memory_order_acq_rel))
        fx_photo_tools_finish(s,PSM_CANCELLED,0,0,0,0,0);
    else if(b&&atomic_load_explicit(&b->posted,memory_order_acquire)&&rd(R_SERIAL)==atomic_load(&b->serial)&&rd(R_STATE)==1u)
        wr(R_CANCEL,1u); /* Thread-safe native cancellation even after GUI stops. */
#endif
    return atomic_load_explicit(&s->running,memory_order_acquire)||atomic_load(&s->fault)?FX_BUSY:FX_OK;
}
extern FxPhotoToolsControl *fx_z8_photo_tools_control(void);
FxResult fx_z8_photo_tools_resource(void *context,uint32_t operation,const FxNamedBinding *binding,FxPlatformOwner *o,uint64_t token,uint64_t *cookie,const FxServiceRequest *q,FxServiceReply *a){
    (void)context;(void)binding;return fx_photo_tools_resource(fx_z8_photo_tools_control(),operation,o,token,cookie,q,a);
}
