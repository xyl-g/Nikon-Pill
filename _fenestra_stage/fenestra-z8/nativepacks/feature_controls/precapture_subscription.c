/* SPDX-License-Identifier: GPL-3.0-only
 * Late-load equivalent of the stock DCF subscription constructor.
 * Entry only reads native state. PUBLISH runs before the rendezvous work
 * callback returns, after all-CPU sync ACK but while all RTOS CPUs remain parked.
 * Never re-register, replace the callback/client context, allocate or take an
 * original mutex in this epoch. The same resident slot survives STOP/RESUME.
 */
#include "internal.h"
#include "precapture_subscription_preimages.h"
#define DCF_SLOTS 48u
#define DCF_STRIDE 0xf98u
#define DCF_BITMAP 0xf80u
static const uint32_t events[]={0x00000601u,0x02000688u,0x06000689u,0x0200172au,0x02000600u,
    0x06000604u,0x06000608u,0x06000605u,0x06000606u};
static uint32_t read32(const volatile uint8_t *p){return *(const volatile uint32_t *)(const void *)p;}
static uintptr_t read64(const volatile uint8_t *p){return *(const volatile uintptr_t *)(const void *)p;}
static uint8_t bitmap(uint32_t byte,uint32_t count){
    uint8_t v=0u;
    for(uint32_t i=0u;i<count;++i){uint32_t id=events[i],at=((id>>8)&255u)*32u+((id>>3)&31u);if(byte==at)v|=(uint8_t)(1u<<(id&7u));}
    return v;
}
static int original(const uint8_t *p,const uint8_t *expected,uint32_t bytes){
    if(!p)return 0;for(uint32_t i=0u;i<bytes;++i)if(((const volatile uint8_t *)p)[i]!=expected[i])return 0;return 1;
}
FxResult fx_controls_precapture_stage(FxControlsDcfSubscription *s,const FxControlsProfile *p){
    if(!s||!p||!p->dcf_bank)return FX_ARGUMENT;
    if(s->staged||s->published)return FX_STATE;
    if(!original(p->dcf_constructor,fx_dcf_constructor_original,sizeof(fx_dcf_constructor_original))||
       !original(p->dcf_registration,fx_dcf_registration_original,sizeof(fx_dcf_registration_original)))return FX_INTEGRITY;
    uintptr_t selected=0u;uint32_t already_new=0u;
    for(uint32_t i=0u;i<DCF_SLOTS;++i){
        volatile uint8_t *slot=p->dcf_bank+i*DCF_STRIDE;
        if(read64(slot+8u)!=UINT64_C(0x408703b4))continue;
        /* +0 belongs to the global counter/preceding bitmap, not this slot.
         * The real record begins at +4. Every other native header field is
         * verified; another occurrence of this callback is ambiguous. */
        uint32_t count=read32(slot+0x18u);
        if(selected||read32(slot+4u)||read32(slot+0x10u)||read32(slot+0x14u)!=UINT32_C(0x04320000)||
           (count!=5u&&count!=9u))return FX_INTEGRITY;
        for(uint32_t j=0u;j<DCF_BITMAP;++j)if(slot[0x1cu+j]!=bitmap(j,count))return FX_INTEGRITY;
        selected=(uintptr_t)slot;already_new=count==9u;
    }
    if(!selected)return FX_INTEGRITY;
    s->slot=selected;s->already_new=already_new;s->staged=1u;return FX_OK;
}
static void clean(uintptr_t a,uintptr_t b){
#ifdef FX_CONTROLS_TESTING
    extern void fx_controls_test_dcf_clean(uintptr_t,uintptr_t);
    fx_controls_test_dcf_clean(a,b);
#elif defined(__aarch64__)
    uint64_t ctr;__asm__ volatile("mrs %0,ctr_el0":"=r"(ctr));
    uintptr_t line=(uintptr_t)4u<<((ctr>>16)&15u),mask=line-1u;
    a&=~mask;b&=~mask;
    __asm__ volatile("dc cvac,%0"::"r"(a):"memory");
    if(a!=b)__asm__ volatile("dc cvac,%0"::"r"(b):"memory");
    __asm__ volatile("dsb sy":::"memory");
#else
    (void)a;(void)b;
#endif
}
void fx_controls_precapture_publish(FxControlsDcfSubscription *s){
    if(!s||!s->staged||s->published)return;
    if(!s->already_new){
        volatile uint8_t *slot=(void *)s->slot;
        /* Derive the changed bytes by the same event-to-bitmap algorithm as
         * the original constructor; no guessed hardcoded bit writes. */
        for(uint32_t j=0u;j<DCF_BITMAP;++j){uint8_t old=bitmap(j,5u),next=bitmap(j,9u);if(old!=next)slot[0x1cu+j]=next;}
        *(volatile uint32_t *)(void *)(slot+0x18u)=9u;
        clean(s->slot+0x18u,s->slot+0xdcu);
    }
    s->published=1u;
}
