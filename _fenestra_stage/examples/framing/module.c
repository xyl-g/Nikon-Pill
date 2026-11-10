/* SPDX-License-Identifier: GPL-3.0-only
 * Framing control panel. Open gate uses the core's complete native menu path.
 */
#include "fenestra_sdk.h"
#include "fenestra_framing.h"
#include "fenestra_memory.h"
enum {CONFIGURE,OPEN,PAGE,QUERY,NRAW,DESQUEEZE,OPEN_GATE,STATE,POLL,SELECT};
static FxSdkClient client;
static uint32_t stage,unavailable,published,desired,selected_control;
static uint32_t nraw_revision,desqueeze_revision;
static uint64_t binding;
static FxFramingStatus current,shown;
static void copy_text(char *out,const char *in,uint32_t bytes){uint32_t i=0u;while(i+1u<bytes && in[i]){out[i]=in[i];++i;}out[i]=0;}
#define text(out,in) copy_text(out,in,(uint32_t)sizeof(out))
static int snapshot(const FxServiceReply *reply) {
    if(reply->bytes!=sizeof(current))return 0;
    FxFramingStatus next;memcpy(&next,reply->data,sizeof(next));
    if(next.abi!=FX_FRAMING_ABI || next.bytes!=sizeof(next) || next.nraw>=4u ||
       next.desqueeze>=8u || next.open_gate>1u)return 0;
    current=next;return 1;
}
static uint32_t editable(uint32_t capability) {
    return !unavailable && (current.capabilities&capability) &&
        !(current.flags&(FX_FRAMING_RECORDING|FX_FRAMING_FAULT|FX_FRAMING_REVOKED));
}
int fenestra_module_entry(const FxModuleServices *base) {
    if(!base || base->bytes!=sizeof(FxServiceModule))return 1;
    const FxServiceModule *api=(const FxServiceModule *)base;
    if(api->service_abi!=FX_SERVICE_ABI)return 2;
    if(client.pending) {
        FxServiceReply reply;FxResult taken=fx_sdk_take(&client,api,&reply);
        if(taken==FX_BUSY)return 0;
        if(taken!=FX_OK)return 3;
        if(reply.result==FX_BUSY && reply.reserved==FX_SERVICE_DEFERRED) { /* Retry the copied request until its native reply arrives. */ }
        else if(stage==SELECT && (reply.result==FX_BUSY || reply.result==FX_STATE)) {
            /* A rejected setting is never queued for a later, idle camera. */
            published=0u;stage=QUERY;
        } else if(reply.result==FX_BUSY) { /* Retry this same idempotent operation. */ }
        else if(stage==CONFIGURE && reply.result==FX_OK)stage=OPEN;
        else if(stage==OPEN && reply.result==FX_OK) {binding=reply.value[0];if(!binding)return 4;stage=PAGE;}
        else if(stage==OPEN && (reply.result==FX_CAPABILITY || reply.result==FX_STATE)) {unavailable=1u;stage=PAGE;}
        else if(stage==PAGE && reply.result==FX_OK)stage=binding?QUERY:NRAW;
        else if(stage==QUERY && reply.result==FX_OK) {
            if(!snapshot(&reply))return 5;
            stage=!published || memcmp(&current,&shown,sizeof(current))?NRAW:POLL;
        } else if(stage==NRAW && reply.result==FX_OK) {nraw_revision=(uint32_t)reply.value[0];stage=DESQUEEZE;}
        else if(stage==DESQUEEZE && reply.result==FX_OK) {desqueeze_revision=(uint32_t)reply.value[0];stage=OPEN_GATE;}
        else if(stage==OPEN_GATE && reply.result==FX_OK)stage=STATE;
        else if(stage==STATE && reply.result==FX_OK) {shown=current;published=1u;stage=POLL;}
        else if(stage==POLL && (reply.result==FX_OK || reply.result==FX_EMPTY)) {
            stage=binding?QUERY:POLL;
            if(reply.result==FX_OK && reply.bytes==sizeof(FxUiEvent)) {
                FxUiEvent event;memcpy(&event,reply.data,sizeof(event));
                if(event.event==FX_INPUT_CHANGE && event.control==2u && event.value<4u &&
                   event.reserved==nraw_revision && editable(FX_FRAMING_NRAW_AVAILABLE)) {
                    desired=event.value;selected_control=FX_FRAMING_NRAW;stage=SELECT;
                } else if(event.event==FX_INPUT_CHANGE && event.control==3u && event.value<8u &&
                   event.reserved==desqueeze_revision && editable(FX_FRAMING_DESQUEEZE_AVAILABLE)) {
                    desired=event.value;selected_control=FX_FRAMING_DESQUEEZE;stage=SELECT;
                }
            }
        } else if(stage==SELECT && reply.result==FX_OK) {
            if(!snapshot(&reply))return 6;
            stage=NRAW;
        } else return 7;
    }
    FxResult result;
    if(stage==CONFIGURE) {
        const uint64_t args[4]={FX_LIFE_CONFIGURE,FX_MODULE_EXTENSION,0u,0u};
        result=fx_sdk_submit(&client,api,FX_S_LIFECYCLE,args,0,0u);
    } else if(stage==OPEN)result=fx_sdk_open(&client,api,"camera.video.framing",FX_FRAMING_ABI);
    else if(stage==QUERY)result=fx_sdk_platform(&client,api,FX_BIND_CALL,binding,FX_FRAMING_QUERY,0u,0,0u);
    else if(stage==SELECT) {
        FxFramingSelection request={FX_FRAMING_ABI,sizeof(request),selected_control,desired};
        result=fx_sdk_platform(&client,api,FX_BIND_CALL,binding,FX_FRAMING_SELECT,0u,&request,sizeof(request));
    } else if(stage==POLL)result=fx_sdk_events(&client,api);
    else {
        FxUiControl c={.abi=1u,.bytes=sizeof(c),.id=stage==PAGE?1u:stage==NRAW?2u:stage==DESQUEEZE?3u:stage==OPEN_GATE?4u:5u,
            .parent=stage==PAGE?0u:1u,.kind=stage==PAGE?FX_UI_PAGE:(stage==NRAW || stage==DESQUEEZE)?FX_UI_CHOICE:FX_UI_ACTION,
            .flags=FX_UI_VISIBLE};
        if(stage==PAGE){c.flags|=FX_UI_ENABLED;text(c.label,"画幅 / N-RAW");}
        else if(stage==NRAW) {
            if(editable(FX_FRAMING_NRAW_AVAILABLE))c.flags|=FX_UI_ENABLED;
            text(c.label,"N-RAW 品质");c.value=current.nraw;c.option_count=4u;
            text(c.options[0],"高");text(c.options[1],"精细");text(c.options[2],"标准");text(c.options[3],"基本");
        } else if(stage==DESQUEEZE) {
            if(editable(FX_FRAMING_DESQUEEZE_AVAILABLE))c.flags|=FX_UI_ENABLED;
            text(c.label,"变形镜头还原");c.value=current.desqueeze;c.option_count=8u;
            text(c.options[0],"关闭");text(c.options[1],"1.25x");text(c.options[2],"1.30x");text(c.options[3],"1.33x");
            text(c.options[4],"1.50x");text(c.options[5],"1.60x");text(c.options[6],"1.80x");text(c.options[7],"2.00x");
        } else if(stage==OPEN_GATE) {
            text(c.label,current.open_gate?"已选择开放画幅":"开放画幅：相机菜单");
            text(c.text,"原始画幅：[FX 3:2] 30/25/24p");
        } else {
            text(c.label,"状态");
            text(c.text,unavailable?"功能未准备完成":
                current.flags&FX_FRAMING_FAULT?"相机状态错误":
                current.flags&FX_FRAMING_REVOKED?"正在停止":
                current.flags&FX_FRAMING_RECORDING?"录制中，已锁定":"准备完成，保留原始画素");
        }
        result=fx_sdk_ui(&client,api,&c);
    }
    return result==FX_OK?0:8;
}
