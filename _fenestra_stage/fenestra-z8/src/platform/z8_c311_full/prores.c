/* SPDX-License-Identifier: GPL-3.0-only
 * Encoding/container/display semantics ported from z8prores/back_half.py.
 * Selection belongs to an SDK provider; executable code is always core-owned.
 */
#include "fenestra_prores.h"
#include "z8_prores_tables.h"
extern FxProResControl *fx_z8_prores_control(void);
uint32_t fx_z8_prores_selected(void){return fx_prores_selected(fx_z8_prores_control());}
uintptr_t fx_z8_prores_rate(void) {
    uint32_t grade=fx_z8_prores_selected();
    return grade==UINT32_MAX?0u:grade?(uintptr_t)fx_z8_prores_rates[grade-1u]:UINT64_C(0x42ab3a50);
}
void fx_z8_prores_remember(uint32_t grade) {
    FxProResControl *s=fx_z8_prores_control();
    if(s)atomic_store_explicit(&s->playback,grade<4u?grade:0u,memory_order_release);
}
uint32_t fx_z8_prores_playback(void) {
    FxProResControl *s=fx_z8_prores_control();return s?atomic_load_explicit(&s->playback,memory_order_acquire):0u;
}
void fx_z8_prores_movie_open(uintptr_t context){fx_prores_movie_open(fx_z8_prores_control(),context);}
void fx_z8_prores_movie_close(uintptr_t context){fx_prores_movie_close(fx_z8_prores_control(),context);}
FxResult fx_z8_prores_resource(void *context,uint32_t op,const FxNamedBinding *binding,FxPlatformOwner *o,
    uint64_t token,uint64_t *cookie,const FxServiceRequest *q,FxServiceReply *a) {
    (void)context;(void)binding;return fx_prores_resource(fx_z8_prores_control(),op,o,token,cookie,q,a);
}
const uint32_t fx_z8_prores_metadata[]={UINT32_C(0x46585052),FX_PRORES_ABI,sizeof(FxProResControl),
    offsetof(FxProResControl,enabled),offsetof(FxProResControl,owner),offsetof(FxProResControl,active),
    offsetof(FxProResControl,playback),offsetof(FxProResControl,fault),offsetof(FxProResControl,uncertain),
    offsetof(FxProResControl,value),FX_PRORES_MOVIES};
