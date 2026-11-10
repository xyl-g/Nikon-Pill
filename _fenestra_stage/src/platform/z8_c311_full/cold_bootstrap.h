/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FX_Z8_COLD_BOOTSTRAP_H
#define FX_Z8_COLD_BOOTSTRAP_H
#include "fenestra.h"
#include <stdatomic.h>
#define FX_COLD_BOOTSTRAP_ATTEMPTS 100u
#define FX_COLD_BOOTSTRAP_RETRIES 4u
enum {FX_COLD_EMPTY,FX_COLD_CLAIMED,FX_COLD_WAITING,FX_COLD_LOADING,
      FX_COLD_INSTALLING,FX_COLD_READY,FX_COLD_FAILED,FX_COLD_CANCELLED};
typedef struct {
    _Atomic uint32_t phase,error;
    uint32_t generation,attempts;
    uint32_t retries;
} FxColdBootstrap;
typedef struct {
    void *context;
    int (*cancelled)(void *,uint32_t generation);
    FxResult (*storage)(void *);
    FxResult (*load)(void *);
    /* Final publication must recheck cancellation under its consumer lock.
     * Success transitions into the normal retained runtime stop protocol. */
    FxResult (*install)(void *);
    FxResult (*delay)(void *);
    void (*stop)(void *);
    /* Nonzero once load changed target state that outlives the attempt.
     * Published state never ends CANCELLED: a successful load completes and
     * the normal runtime stop protocol owns the pending shutdown. */
    int (*published)(void *);
    /* Re-opens a CANCELLED attempt so the next load continues from its
     * completed work instead of freeing and allocating it again. */
    FxResult (*rewind)(void *);
} FxColdBootstrapIO;
static inline FxResult fx_cold_bootstrap_finish(FxColdBootstrap *s,uint32_t phase,FxResult error) {
    atomic_store_explicit(&s->error,(uint32_t)error,memory_order_relaxed);
    atomic_store_explicit(&s->phase,phase,memory_order_release);return error;
}
/* One cold request for the object's entire lifetime. Neither a mount event,
 * a terminal failure nor a later generation can reset this publication gate;
 * only fx_cold_bootstrap_retry restarts a CANCELLED attempt. */
static inline FxResult fx_cold_bootstrap_request(FxColdBootstrap *s,uint32_t generation) {
    if(!s || !generation)return FX_ARGUMENT;
    uint32_t empty=FX_COLD_EMPTY;
    if(!atomic_compare_exchange_strong_explicit(&s->phase,&empty,FX_COLD_CLAIMED,
        memory_order_acq_rel,memory_order_acquire))return FX_BUSY;
    s->generation=generation;s->attempts=0u;atomic_store(&s->error,FX_OK);
    atomic_store_explicit(&s->phase,FX_COLD_WAITING,memory_order_release);return FX_OK;
}
/* A single native task owns step. The caller schedules another step only on
 * BUSY. Only unselected-volume IO failures are retryable. Delay yields to the
 * original FS/event tasks. The attempt bound is not an IO-call time bound. */
static inline FxResult fx_cold_bootstrap_step(FxColdBootstrap *s,const FxColdBootstrapIO *io) {
    if(!s || !io || !io->cancelled || !io->storage || !io->load || !io->install || !io->delay || !io->stop ||
       !io->published || !io->rewind)return FX_ARGUMENT;
    if(atomic_load_explicit(&s->phase,memory_order_acquire)!=FX_COLD_WAITING)return FX_STATE;
    if(io->cancelled(io->context,s->generation))return fx_cold_bootstrap_finish(s,FX_COLD_CANCELLED,FX_INTERRUPTED);
    if(s->attempts>=FX_COLD_BOOTSTRAP_ATTEMPTS)return fx_cold_bootstrap_finish(s,FX_COLD_FAILED,FX_TIMEOUT);
    ++s->attempts;FxResult r=io->storage(io->context);
    if(r==FX_CLOSE)return fx_cold_bootstrap_finish(s,FX_COLD_FAILED,r);
    if(io->cancelled(io->context,s->generation))return fx_cold_bootstrap_finish(s,FX_COLD_CANCELLED,FX_INTERRUPTED);
    if(r!=FX_OK) {
        if(r==FX_IO && s->attempts<FX_COLD_BOOTSTRAP_ATTEMPTS) {
            r=io->delay(io->context);if(r==FX_OK)return FX_BUSY;
        } else if(r==FX_IO)r=FX_TIMEOUT;
        return fx_cold_bootstrap_finish(s,FX_COLD_FAILED,r);
    }
    atomic_store_explicit(&s->phase,FX_COLD_LOADING,memory_order_release);
    r=io->load(io->context);
    int published=io->published(io->context);
    if(r!=FX_OK || (!published && io->cancelled(io->context,s->generation))) {
        io->stop(io->context);
        /* CANCELLED only when the load stopped for the cancellation itself:
         * a real error is never continued, whatever the shutdown state. */
        if(!published && (r==FX_OK || r==FX_INTERRUPTED))return fx_cold_bootstrap_finish(s,FX_COLD_CANCELLED,FX_INTERRUPTED);
        return fx_cold_bootstrap_finish(s,FX_COLD_FAILED,r);
    }
    atomic_store_explicit(&s->phase,FX_COLD_INSTALLING,memory_order_release);
    r=io->install(io->context);
    if(r!=FX_OK) {
        io->stop(io->context);
        return fx_cold_bootstrap_finish(s,r==FX_INTERRUPTED && !io->published(io->context)?FX_COLD_CANCELLED:FX_COLD_FAILED,r);
    }
    return fx_cold_bootstrap_finish(s,FX_COLD_READY,FX_OK);
}
/* Retry only a scheduled WAITING result. A native callback can legitimately
 * fail with BUSY; its terminal error must not be replaced by a second step. */
static inline FxResult fx_cold_bootstrap_run(FxColdBootstrap *s,const FxColdBootstrapIO *io) {
    FxResult r;
    do {r=fx_cold_bootstrap_step(s,io);}while(r==FX_BUSY &&
        atomic_load_explicit(&s->phase,memory_order_acquire)==FX_COLD_WAITING);
    return r;
}
/* Continue a CANCELLED attempt (which by construction published nothing)
 * under a new generation, after the shutdown that cancelled it was itself
 * cancelled. rewind runs first; its refusal makes the attempt terminal.
 * Bounded per object lifetime; BOUNDS leaves the attempt CANCELLED. */
static inline FxResult fx_cold_bootstrap_retry(FxColdBootstrap *s,const FxColdBootstrapIO *io,uint32_t generation) {
    if(!s || !io || !io->rewind || !generation)return FX_ARGUMENT;
    if(atomic_load_explicit(&s->phase,memory_order_acquire)!=FX_COLD_CANCELLED)return FX_STATE;
    if(generation==s->generation)return FX_ARGUMENT;
    if(s->retries>=FX_COLD_BOOTSTRAP_RETRIES)return FX_BOUNDS;
    FxResult r=io->rewind(io->context);
    if(r!=FX_OK)return fx_cold_bootstrap_finish(s,FX_COLD_FAILED,r);
    ++s->retries;s->generation=generation;s->attempts=0u;atomic_store(&s->error,FX_OK);
    atomic_store_explicit(&s->phase,FX_COLD_WAITING,memory_order_release);return FX_OK;
}
#endif
