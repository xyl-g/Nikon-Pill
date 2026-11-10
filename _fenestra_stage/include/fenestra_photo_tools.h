/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef FENESTRA_PHOTO_TOOLS_H
#define FENESTRA_PHOTO_TOOLS_H
#include "fenestra_platform.h"
#define FX_PHOTO_TOOLS_ABI 1u
#define FX_PHOTO_TOOLS_CONFIRM UINT32_C(0x4d455247)
enum {FX_PHOTO_TOOLS_QUERY,FX_PHOTO_TOOLS_START,FX_PHOTO_TOOLS_CANCEL};
enum {FX_PHOTO_NEWEST,FX_PHOTO_ALL};
enum {FX_PHOTO_PRECAPTURE=1u,FX_PHOTO_MERGE=2u,FX_PHOTO_RUNNING=4u,
      FX_PHOTO_CANCELLING=8u,FX_PHOTO_REVOKED=16u,FX_PHOTO_FAULT=32u};
typedef struct {uint32_t abi,bytes,mode,confirm;} FxPhotoToolsStart;
typedef struct {
    uint32_t abi,bytes,flags,serial,mode,status,phase,first,second,rows,total;
    uint32_t groups,targets,merged,attempted,seconds,release_mode,file_type,raw_recording;
} FxPhotoToolsStatus;
struct FxPhotoToolsControl;
typedef struct {
    /* start only queues; poll must run on the stock GUI task before starting I/O. */
    FxResult (*start)(struct FxPhotoToolsControl *);
    void (*poll)(struct FxPhotoToolsControl *);
    void (*snapshot)(struct FxPhotoToolsControl *,FxPhotoToolsStatus *);
} FxPhotoToolsOps;
typedef struct FxPhotoToolsControl {
    _Atomic uint32_t gate,running,cancel,fault,revoked;
    _Atomic uintptr_t owner;
    uint64_t token;
    uint32_t supported,serial,mode;
    _Atomic uint32_t status,phase,first,second,rows,total,groups,targets,merged,attempted,seconds;
    const FxPhotoToolsOps *ops;
    /* Native context and permanent task are core-owned until reboot. */
    void *native;
} FxPhotoToolsControl;
void fx_photo_tools_init(FxPhotoToolsControl *,uint32_t,const FxPhotoToolsOps *);
void fx_photo_tools_finish(FxPhotoToolsControl *,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t);
void fx_photo_tools_poll(FxPhotoToolsControl *);
FxResult fx_photo_tools_resource(FxPhotoToolsControl *,uint32_t,FxPlatformOwner *,uint64_t,uint64_t *,const FxServiceRequest *,FxServiceReply *);
uint32_t fx_z8_photo_tools_pending(FxPhotoToolsControl *);
void fx_z8_photo_tools_poll(FxPhotoToolsControl *);
FxResult fx_z8_photo_tools_resume(FxPhotoToolsControl *);
FxResult fx_z8_photo_tools_stop(FxPhotoToolsControl *);
void fx_z8_photo_tools_init(FxPhotoToolsControl *);
FxResult fx_z8_photo_tools_resource(void *,uint32_t,const FxNamedBinding *,FxPlatformOwner *,uint64_t,uint64_t *,const FxServiceRequest *,FxServiceReply *);
#endif
