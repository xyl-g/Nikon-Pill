/* SPDX-License-Identifier: GPL-3.0-only
 * camera.video.framing/v1: control the pinned, core-owned framing backend.
 * Open gate stays on the native frame-size/rate transaction path.
 */
#ifndef FENESTRA_FRAMING_H
#define FENESTRA_FRAMING_H
#include "fenestra_platform.h"
#define FX_FRAMING_ABI 1u
enum {FX_FRAMING_QUERY,FX_FRAMING_SELECT};
enum {FX_FRAMING_NRAW,FX_FRAMING_DESQUEEZE};
enum {FX_FRAMING_HIGH,FX_FRAMING_FINE,FX_FRAMING_NORMAL,FX_FRAMING_BASIC};
enum {FX_FRAMING_NRAW_AVAILABLE=1u,FX_FRAMING_DESQUEEZE_AVAILABLE=2u,
      FX_FRAMING_NATIVE_OPEN_GATE=4u};
enum {FX_FRAMING_RECORDING=1u,FX_FRAMING_FAULT=2u,FX_FRAMING_REVOKED=4u};
typedef struct {uint32_t abi,bytes,control,value;} FxFramingSelection;
typedef struct {
    uint32_t abi,bytes,capabilities,flags,nraw,desqueeze,open_gate,rate,revision;
} FxFramingStatus;
typedef FxResult (*FxFramingRead)(void *,FxFramingStatus *);
typedef FxResult (*FxFramingWrite)(void *,uint32_t,uint32_t);
typedef struct {
    _Atomic uint32_t gate;
    uintptr_t owner;uint64_t token;uint32_t revoked,revision;
    void *context;FxFramingRead read;FxFramingWrite write;
} FxFramingControl;
void fx_framing_init(FxFramingControl *,void *,FxFramingRead,FxFramingWrite);
FxResult fx_framing_resource(FxFramingControl *,uint32_t,FxPlatformOwner *,uint64_t,
    uint64_t *,const FxServiceRequest *,FxServiceReply *);
/* These accessors are supplied by the native menu controller and suite lock. */
FxFramingControl *fx_z8_framing_control(void);
void fx_z8_framing_init(FxFramingControl *);
FxResult fx_z8_framing_resource(void *,uint32_t,const FxNamedBinding *,FxPlatformOwner *,
    uint64_t,uint64_t *,const FxServiceRequest *,FxServiceReply *);
#endif
