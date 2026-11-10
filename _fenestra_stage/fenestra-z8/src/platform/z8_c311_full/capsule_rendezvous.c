/* SPDX-License-Identifier: GPL-3.0-only */
#include "capsule_rendezvous.h"
#include "fenestra_capsule.h"
/* Native C311 stock addresses: this target adapter contains no package or
 * consumer addresses. Source MAIN SHA256 is 2b43a31c9de838f84b12416de4348a700
 * 4e65be07f853ec56ec2b10f3a8e3853. Recovered native type-4 SGI callback ABI:
 * call3(callback,arg0,arg1,mask), callback(arg0,arg1), cores MPIDR[3:0]=0..2.
 * Original dispatch waits for each completion word using WFE, without bound.
 */
#define NATIVE_DISPATCH UINT64_C(0x40174108)
#define NATIVE_ISR UINT64_C(0x40173f0c)
#define NATIVE_IRQ_REGISTER UINT64_C(0x4016c8f4)
#define NATIVE_CPU_ID UINT64_C(0x40173c54)
#define NATIVE_READY UINT64_C(0xa04b6c038)
#define NATIVE_CONTROLLER UINT64_C(0xa04b51820)
#define NATIVE_DESCRIPTORS UINT64_C(0xa04b6bfd8)
#define CPU_MASK 7u
#if defined(FX_Z8_CAPSULE_RENDEZVOUS_TEST)
/* Synthetic CPU/transport seams are test-only, never selected by target build. */
extern uint64_t fx_rendezvous_test_register(uint32_t);
extern void fx_rendezvous_test_daif(uint64_t);
extern void fx_rendezvous_test_mask(void);
extern void fx_rendezvous_test_sync(void);
extern void fx_rendezvous_test_wait(void);
extern void fx_rendezvous_test_event(void);
extern uint64_t fx_rendezvous_test_read(uint64_t,uint32_t);
extern int64_t fx_rendezvous_test_dispatch(void (*)(void *,uintptr_t),void *,uintptr_t,uint32_t);
static uint64_t reg(uint32_t n) {return fx_rendezvous_test_register(n);}
static void restore(uint64_t n) {fx_rendezvous_test_daif(n);}
static void mask(void) {fx_rendezvous_test_mask();}
static void sync_local(void) {fx_rendezvous_test_sync();}
static void wait_event(void) {fx_rendezvous_test_wait();}
static void send_event(void) {fx_rendezvous_test_event();}
static uint64_t native_read(uint64_t a,uint32_t n) {return fx_rendezvous_test_read(a,n);}
#else
static uint64_t reg(uint32_t n) {
    uint64_t v;
    if(n==0u)__asm__ volatile("mrs %0, mpidr_el1":"=r"(v));
    else if(n==1u)__asm__ volatile("mrs %0, daif":"=r"(v));
    else __asm__ volatile("mrs %0, CurrentEL":"=r"(v));
    return v;
}
static void restore(uint64_t n) {__asm__ volatile("msr daif, %0; isb"::"r"(n):"memory");}
static void mask(void) {__asm__ volatile("msr daifset, #3; isb":::"memory");}
static void sync_local(void) {
    __asm__ volatile("dsb sy; tlbi vmalle1; dsb sy; ic iallu; dsb sy; isb":::"memory");
}
static void wait_event(void) {__asm__ volatile("wfe":::"memory");}
static void send_event(void) {__asm__ volatile("dsb sy; sev":::"memory");}
static uint64_t native_read(uint64_t a,uint32_t n) {
    if(n==8u)return *(const volatile uint64_t *)(uintptr_t)a;
    if(n==4u)return *(const volatile uint32_t *)(uintptr_t)a;
    return *(const volatile uint8_t *)(uintptr_t)a;
}
#endif
static uint32_t cpu_id(void) {return (uint32_t)(reg(0u)&15u);}
static FxResult native_profile(void) {
    static const uint64_t addresses[4]={NATIVE_DISPATCH,NATIVE_ISR,NATIVE_IRQ_REGISTER,NATIVE_CPU_ID};
    static const uint8_t original[4][16]={
        {0xff,0x83,0x02,0xd1,0xfc,0x23,0x00,0xf9,0xfb,0x6b,0x05,0xa9,0xf9,0x63,0x06,0xa9},
        {0xff,0xc3,0x01,0xd1,0xf9,0x63,0x03,0xa9,0xf7,0x5b,0x04,0xa9,0xf5,0x53,0x05,0xa9},
        {0x08,0x04,0x83,0xd2,0xa8,0x96,0xa0,0xf2,0x48,0x01,0xc0,0xf2,0x08,0x00,0xe0,0xf2},
        {0xa8,0x00,0x38,0xd5,0x00,0x0d,0x00,0x12,0xc0,0x03,0x5f,0xd6,0xe0,0x01,0x00,0xb4}};
    for(uint32_t i=0u;i<4u;++i)for(uint32_t j=0u;j<16u;++j)
        if(native_read(addresses[i]+j,1u)!=original[i][j])return FX_INTEGRITY;
    for(uint32_t i=0u;i<3u;++i) {
        uint64_t descriptor=NATIVE_DESCRIPTORS+(uint64_t)i*32u;
        if(native_read(NATIVE_READY+(uint64_t)i*24u,4u)!=5u)return FX_BUSY;
        if(native_read(NATIVE_CONTROLLER+0x2000u+(uint64_t)i*256u+15u*8u,8u)!=descriptor ||
           native_read(descriptor,4u)!=15u || native_read(descriptor+16u,8u)!=NATIVE_ISR)return FX_INTEGRITY;
    }
    return FX_OK;
}
static void native_callback(void *opaque,uintptr_t unused) {
    FxZ8CapsuleRendezvous *r=opaque;uint32_t cpu=cpu_id();(void)unused;
    /* Preflight requires this exact three-core profile. A changed MPIDR in
     * native dispatch is not recoverable by inventing a fourth completion. */
    if(cpu>=3u)return;
    if(cpu==r->initiator) {
        uint32_t others=CPU_MASK^(1u<<cpu);
        while((atomic_load_explicit(&r->parked,memory_order_acquire)&others)!=others)wait_event();
        r->result=r->work(r->argument);
        if(r->held && atomic_load_explicit(&r->sync_epoch,memory_order_acquire)==0u) {
            /* A malformed work callback must not release modified mappings
             * without even one all-CPU synchronization. It never earns OK. */
            (void)fx_z8_capsule_exclusion_leave(r,r->lease,FX_CAPSULE_LEAVE_RETAINED);
            r->result=FX_STATE;
        }
        atomic_store_explicit(&r->exit_requested,1u,memory_order_release);send_event();
        return;
    }
    uint64_t daif=reg(1u);mask();uint32_t seen=0u;
    atomic_fetch_or_explicit(&r->parked,1u<<cpu,memory_order_release);send_event();
    while(!atomic_load_explicit(&r->exit_requested,memory_order_acquire)) {
        uint32_t epoch=atomic_load_explicit(&r->sync_epoch,memory_order_acquire);
        if(epoch!=seen) {
            sync_local();seen=epoch;
            atomic_store_explicit(&r->acknowledged[cpu],epoch,memory_order_release);send_event();
        } else wait_event();
    }
    restore(daif);
}
FxResult fx_z8_capsule_rendezvous_run(FxZ8CapsuleRendezvous *r,FxResult (*work)(void *),void *arg) {
    if(!r || !work)return FX_ARGUMENT;
    uint32_t zero=0u;
    if(!atomic_compare_exchange_strong_explicit(&r->gate,&zero,1u,memory_order_acquire,memory_order_relaxed))return FX_BUSY;
    FxResult result=FX_STATE;uint32_t cpu=cpu_id();
    if(reg(2u)!=4u || cpu>=3u)goto done;
    if(reg(1u)&UINT64_C(0x80)) {result=FX_BUSY;goto done;}
    result=native_profile();if(result!=FX_OK)goto done;
    if(r->serial==UINT64_MAX) {result=FX_BOUNDS;goto done;}
    ++r->serial;r->initiator=cpu;r->held=0u;r->lease=0u;r->last_outcome=0u;
    r->work=work;r->argument=arg;r->result=FX_STATE;
    atomic_store_explicit(&r->parked,0u,memory_order_relaxed);
    atomic_store_explicit(&r->exit_requested,0u,memory_order_relaxed);
    atomic_store_explicit(&r->sync_epoch,0u,memory_order_relaxed);
    for(uint32_t i=0u;i<3u;++i)atomic_store_explicit(&r->acknowledged[i],0u,memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
#if defined(FX_Z8_CAPSULE_RENDEZVOUS_TEST)
    int64_t native=fx_rendezvous_test_dispatch(native_callback,r,0u,CPU_MASK);
#else
    int64_t native=((int64_t (*)(void (*)(void *,uintptr_t),void *,uintptr_t,uint32_t))(uintptr_t)NATIVE_DISPATCH)(native_callback,r,0u,CPU_MASK);
#endif
    /* Original dispatcher returns only after both remote callbacks returned. */
    if(r->held) {restore(r->saved_daif);r->held=0u;}
    result=native==0?r->result:FX_IO;
    r->work=NULL;r->argument=NULL;r->lease=0u;
done:
    atomic_store_explicit(&r->gate,0u,memory_order_release);return result;
}
FxResult fx_z8_capsule_exclusion_enter(FxZ8CapsuleRendezvous *r,uint64_t *lease) {
    if(!r || !lease)return FX_ARGUMENT;
    *lease=0u;
    if(!atomic_load_explicit(&r->gate,memory_order_acquire) || cpu_id()!=r->initiator || !r->work || r->held)return FX_STATE;
    uint32_t others=CPU_MASK^(1u<<r->initiator);
    if((atomic_load_explicit(&r->parked,memory_order_acquire)&others)!=others)return FX_BUSY;
    r->saved_daif=reg(1u);mask();
    atomic_fetch_or_explicit(&r->parked,1u<<r->initiator,memory_order_release);
    r->lease=r->serial;r->held=1u;*lease=r->lease;return FX_OK;
}
FxResult fx_z8_capsule_exclusion_leave(FxZ8CapsuleRendezvous *r,uint64_t lease,uint32_t outcome) {
    if(!r || !lease || outcome>FX_CAPSULE_LEAVE_RETAINED)return FX_ARGUMENT;
    if(!atomic_load_explicit(&r->gate,memory_order_acquire) || cpu_id()!=r->initiator || !r->held || lease!=r->lease)return FX_STATE;
    if(atomic_load_explicit(&r->parked,memory_order_acquire)!=CPU_MASK)return FX_STATE;
    uint32_t old=atomic_load_explicit(&r->sync_epoch,memory_order_relaxed);
    if(old==UINT32_MAX)return FX_BOUNDS;
    uint32_t epoch=old+1u;
    atomic_store_explicit(&r->sync_epoch,epoch,memory_order_release);send_event();
    sync_local();atomic_store_explicit(&r->acknowledged[r->initiator],epoch,memory_order_release);send_event();
    for(uint32_t i=0u;i<3u;++i)while(atomic_load_explicit(&r->acknowledged[i],memory_order_acquire)!=epoch)wait_event();
    r->last_outcome=outcome;return FX_OK;
}
