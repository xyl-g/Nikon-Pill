/* Project-owned minimal Z8 port. All native bindings are generated from a
 * separately validated Z8 profile. Stock menu contents remain byte-identical.
 * Heap state, descriptor and task stack are retained until reboot. */
#include "diag.h"
#include "fenestra_memory.h"
#define FX_Z8_DEFINE_DESCRIPTOR_BYTES 1
#include "z8_diag_bindings.h"
#define FN(a,t) ((t)(uintptr_t)(a))
typedef struct {
    uint32_t attributes,reserved;uintptr_t context,entry;
    uint32_t priority,reserved2;uint64_t stack_bytes;uintptr_t stack;
    uint32_t processor,affinity;
} FxZ8NativeTask;
_Static_assert(sizeof(FxZ8NativeTask)==56 && offsetof(FxZ8NativeTask,context)==8 &&
    offsetof(FxZ8NativeTask,entry)==16 && offsetof(FxZ8NativeTask,stack_bytes)==32 &&
    offsetof(FxZ8NativeTask,stack)==40 && offsetof(FxZ8NativeTask,affinity)==52,"Z8 task ABI");
static int ram(uintptr_t address,size_t bytes){
    return address>=FX_Z8_RAM_BEGIN && address<FX_Z8_RAM_END && bytes<=FX_Z8_RAM_END-address;
}
static FxNativePage *anchor_page(void){
    uintptr_t root=((const volatile uintptr_t *)(uintptr_t)FX_Z8_DATA_PAGE_ROOTS)[FX_Z8_ANCHOR_GROUP];
    uintptr_t offset=(uintptr_t)FX_Z8_ANCHOR_ID*sizeof(FxNativePage);
    if((root&7u) || !ram(root,offset+sizeof(FxNativePage)))return 0;
    return (FxNativePage *)(root+offset);
}
FxZ8DiagState *fx_z8_diag_owner(void){
    FxNativePage *page=anchor_page();if(!page)return 0;
    uintptr_t pointer=atomic_load_explicit((_Atomic uintptr_t *)&page->descriptor,memory_order_acquire);
    if(pointer==FX_Z8_ANCHOR_DESCRIPTOR || (pointer&7u) || !ram(pointer,sizeof(FxZ8DiagState)))return 0;
    FxZ8DiagState *s=(FxZ8DiagState *)pointer;
    if(s->magic!=FX_Z8_DIAG_MAGIC || s->version!=FX_Z8_DIAG_VERSION ||
       memcmp(&s->original_descriptor,fx_z8_original_descriptor,64u))return 0;
    return s;
}
FxPtp *fx_z8_diag_ptp_state(void){
    FxZ8DiagState *s=fx_z8_diag_owner();
    return s && atomic_load_explicit(&s->phase,memory_order_acquire)==FX_Z8_DIAG_ACTIVE &&
        atomic_load_explicit(&s->ready,memory_order_acquire) &&
        !atomic_load_explicit(&s->shutdown,memory_order_acquire)?&s->ptp:0;
}
int fx_z8_diag_active(void){
    FxPtp *ptp=fx_z8_diag_ptp_state();
    return ptp && atomic_load_explicit(&ptp->enabled,memory_order_acquire);
}
static void close_endpoint(FxZ8DiagState *s){
    atomic_store_explicit(&s->shutdown,1u,memory_order_release);
    atomic_store_explicit(&s->phase,FX_Z8_DIAG_CLOSED,memory_order_release);
    fx_ptp_enable(&s->ptp,0);
}
static void fail_endpoint(FxZ8DiagState *s,uint32_t error){
    atomic_store_explicit(&s->error,error,memory_order_release);
    atomic_store_explicit(&s->shutdown,1u,memory_order_release);fx_ptp_enable(&s->ptp,0);
    uint32_t phase=atomic_load_explicit(&s->phase,memory_order_acquire);
    while(phase!=FX_Z8_DIAG_CLOSED && phase!=FX_Z8_DIAG_FAILED &&
          !atomic_compare_exchange_weak_explicit(&s->phase,&phase,FX_Z8_DIAG_FAILED,
              memory_order_acq_rel,memory_order_acquire)){}
}
static void worker(void *context){
    FxZ8DiagState *s=context;
    atomic_store_explicit(&s->worker_running,1u,memory_order_release);
    if(!atomic_load_explicit(&s->shutdown,memory_order_acquire)){
        fx_ptp_enable(&s->ptp,1);
        atomic_store_explicit(&s->ready,1u,memory_order_release);
        uint32_t phase=FX_Z8_DIAG_STARTING;
        if(!atomic_compare_exchange_strong_explicit(&s->phase,&phase,FX_Z8_DIAG_ACTIVE,
            memory_order_acq_rel,memory_order_acquire))fx_ptp_enable(&s->ptp,0);
    }
    for(;;){
        if(!atomic_load_explicit(&s->shutdown,memory_order_acquire) &&
           atomic_load_explicit(&s->phase,memory_order_acquire)==FX_Z8_DIAG_ACTIVE)
            (void)fx_ptp_poll(&s->ptp,fx_z8_diag_backend,s);
        else{
            if(atomic_exchange_explicit(&s->worker_running,0u,memory_order_acq_rel))fx_ptp_enable(&s->ptp,0);
        }
        int32_t result=FN(FX_Z8_NATIVE_TASK_DELAY,int32_t (*)(uint32_t))(FX_Z8_TASK_DELAY_TICKS);
        if(result){
            fail_endpoint(s,(uint32_t)result);atomic_store_explicit(&s->worker_running,0u,memory_order_release);
            /* No unverified task-delete/exit ABI; retain all published storage.
             * WFE prevents a failed native delay from turning into a busy loop. */
            for(;;)__asm__ volatile("wfe");
        }
    }
}
static FxZ8DiagState *prepare_owner(void){
    FxZ8DiagState *existing=fx_z8_diag_owner();if(existing)return existing;
    FxNativePage *page=anchor_page();if(!page)return 0;
    uintptr_t original=atomic_load_explicit((_Atomic uintptr_t *)&page->descriptor,memory_order_acquire);
    if(original!=FX_Z8_ANCHOR_DESCRIPTOR || !ram(original,64u) ||
       memcmp((const void *)original,fx_z8_original_descriptor,64u))return 0;
    FxZ8DiagState *s=FN(FX_Z8_NATIVE_ALLOCATE,void *(*)(uint32_t,uint32_t))
        ((uint32_t)sizeof(*s),FX_Z8_ALLOCATE_POOL);
    if(!s || ((uintptr_t)s&7u) || !ram((uintptr_t)s,sizeof(*s)))return 0;
    memset(s,0,sizeof(*s));memcpy(&s->original_descriptor,(const void *)original,64u);
    s->magic=FX_Z8_DIAG_MAGIC;s->version=FX_Z8_DIAG_VERSION;s->task_id=-1;
    atomic_init(&s->phase,FX_Z8_DIAG_PREPARED);atomic_init(&s->ready,0u);
    atomic_init(&s->shutdown,0u);atomic_init(&s->error,0u);
    atomic_init(&s->worker_running,0u);fx_z8_diag_ptp_init(&s->ptp);
    /* Publish before the stock startup event runs. Shutdown during that stock
     * call sees this disabled owner; startup's late return cannot reopen it.
     * A race loser has no task and retains its local allocation until reboot. */
    if(!atomic_compare_exchange_strong_explicit((_Atomic uintptr_t *)&page->descriptor,&original,
        (uintptr_t)s,memory_order_acq_rel,memory_order_acquire))return fx_z8_diag_owner();
    return s;
}
static void start_worker(FxZ8DiagState *s){
    if(atomic_load_explicit(&s->shutdown,memory_order_acquire))return;
    uint32_t phase=FX_Z8_DIAG_PREPARED;
    if(!atomic_compare_exchange_strong_explicit(&s->phase,&phase,FX_Z8_DIAG_STARTING,
        memory_order_acq_rel,memory_order_acquire))return;
    if(atomic_load_explicit(&s->shutdown,memory_order_acquire))return;
    void *allocation=FN(FX_Z8_NATIVE_ALLOCATE,void *(*)(uint32_t,uint32_t))
        (FX_Z8_TASK_STACK_BYTES+15u,FX_Z8_ALLOCATE_POOL);
    if(!allocation || !ram((uintptr_t)allocation,FX_Z8_TASK_STACK_BYTES+15u)){
        fail_endpoint(s,UINT32_C(0xf8000002));return;
    }
    uintptr_t stack=((uintptr_t)allocation+15u)&~(uintptr_t)15u;
    s->task_stack=stack;s->task_stack_bytes=FX_Z8_TASK_STACK_BYTES;
    if(atomic_load_explicit(&s->shutdown,memory_order_acquire))return;
    FxZ8NativeTask task={FX_Z8_TASK_ATTRIBUTES,0u,(uintptr_t)s,(uintptr_t)worker,
        FX_Z8_TASK_PRIORITY,0u,FX_Z8_TASK_STACK_BYTES,stack,FX_Z8_TASK_PROCESSOR,FX_Z8_TASK_AFFINITY};
    int32_t id=FN(FX_Z8_NATIVE_TASK_CREATE,int32_t (*)(const FxZ8NativeTask *))(&task);
    s->task_id=id;
    if(id<1){fail_endpoint(s,id?(uint32_t)id:UINT32_C(0xf8000001));return;}
    if(atomic_load_explicit(&s->shutdown,memory_order_acquire))return;
    int32_t result=FN(FX_Z8_NATIVE_TASK_RESUME,int32_t (*)(int32_t))(id);
    if(result)fail_endpoint(s,(uint32_t)result);
}
uint64_t fx_z8_system_dispatch(void *message,uint32_t sync){
    uint32_t event=message?*(const uint32_t *)((const uint8_t *)message+FX_Z8_EVENTS_MESSAGE_ID_OFFSET):UINT32_MAX;
    FxZ8DiagState *s=fx_z8_diag_owner();
    if(event==FX_Z8_EVENTS_STARTUP_COMPLETE)s=prepare_owner();
    if(event==FX_Z8_EVENTS_SHUTDOWN && s)close_endpoint(s);
    uint64_t result=fx_z8_system_original(message,sync);
    /* No late preparation fallback: missing roots/descriptor at entry leave
     * this startup attempt closed even if the stock call initializes them. */
    if(event==FX_Z8_EVENTS_STARTUP_COMPLETE && s){
        if(fx_z8_diag_owner()!=s)fail_endpoint(s,UINT32_C(0xf8000003));
        else start_worker(s);
    }
    return result;
}
const uint32_t fx_z8_diag_metadata[]={FX_Z8_DIAG_MAGIC,FX_Z8_DIAG_VERSION,
    sizeof(FxZ8DiagState),offsetof(FxZ8DiagState,magic),offsetof(FxZ8DiagState,phase),
    offsetof(FxZ8DiagState,ready),offsetof(FxZ8DiagState,shutdown),offsetof(FxZ8DiagState,error),
    offsetof(FxZ8DiagState,worker_running),offsetof(FxZ8DiagState,task_id),
    offsetof(FxZ8DiagState,task_stack),offsetof(FxZ8DiagState,ptp),
    sizeof(FxPtp),offsetof(FxPtp,enabled),offsetof(FxPtp,generation),offsetof(FxPtp,executions),FX_Z8_DIAG_CAPS};
