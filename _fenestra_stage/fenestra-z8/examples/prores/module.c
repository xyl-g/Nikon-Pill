/* SPDX-License-Identifier: GPL-3.0-only
 * ProRes quality extension. Requires camera.video.prores/v1 in the Z8 core.
 */
#include "fenestra_sdk.h"
#include "fenestra_prores.h"
#include "fenestra_memory.h"
enum {CONFIGURE,OPEN,PAGE,QUERY,CHOICE,STATE,POLL,SELECT};
static FxSdkClient client;
static uint32_t stage,unavailable,published,desired,choice_revision;
static uint64_t binding;
static FxProResStatus current,shown;
static void text(char *out,const char *in){uint32_t i=0u;do{out[i]=in[i];}while(in[i++]);}
static int snapshot(const FxServiceReply *reply) {
    if(reply->bytes!=sizeof(current))return 0;
    FxProResStatus next;memcpy(&next,reply->data,sizeof(next));
    if(next.abi!=FX_PRORES_ABI || next.bytes!=sizeof(next) || next.grades!=4u || next.selected>=4u)return 0;
    current=next;return 1;
}
static uint32_t editable(void) {
    return !unavailable && (current.flags&FX_PRORES_ENABLED) &&
        !(current.flags&(FX_PRORES_RECORDING|FX_PRORES_REVOKED|FX_PRORES_FAULT));
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
            /* Do not queue a stale selection for automatic application when a
             * movie later closes. Restore the acknowledged native value. */
            published=0u;stage=QUERY;
        } else if(reply.result==FX_BUSY) { /* Retry only the same idempotent operation. */ }
        else if(stage==CONFIGURE && reply.result==FX_OK)stage=OPEN;
        else if(stage==OPEN && reply.result==FX_OK) {binding=reply.value[0];if(!binding)return 4;stage=PAGE;}
        else if(stage==OPEN && (reply.result==FX_CAPABILITY || reply.result==FX_STATE)) {unavailable=1u;stage=PAGE;}
        else if(stage==PAGE && reply.result==FX_OK)stage=binding?QUERY:CHOICE;
        else if(stage==QUERY && reply.result==FX_OK) {
            if(!snapshot(&reply))return 5;
            stage=!published || memcmp(&current,&shown,sizeof(current))?CHOICE:POLL;
        } else if(stage==CHOICE && reply.result==FX_OK) {choice_revision=(uint32_t)reply.value[0];stage=STATE;}
        else if(stage==STATE && reply.result==FX_OK) {shown=current;published=1u;stage=POLL;}
        else if(stage==POLL && (reply.result==FX_OK || reply.result==FX_EMPTY)) {
            stage=binding?QUERY:POLL;
            if(reply.result==FX_OK && reply.bytes==sizeof(FxUiEvent)) {
                FxUiEvent event;memcpy(&event,reply.data,sizeof(event));
                if(event.control==2u && event.event==FX_INPUT_CHANGE && event.value<4u &&
                   event.reserved==choice_revision && editable()) {desired=event.value;stage=SELECT;}
            }
        } else if(stage==SELECT && reply.result==FX_OK) {
            if(!snapshot(&reply))return 6;
            stage=CHOICE;
        } else return 7;
    }
    FxResult result;
    if(stage==CONFIGURE) {
        const uint64_t args[4]={FX_LIFE_CONFIGURE,FX_MODULE_EXTENSION,0u,0u};
        result=fx_sdk_submit(&client,api,FX_S_LIFECYCLE,args,0,0u);
    } else if(stage==OPEN)result=fx_sdk_open(&client,api,"camera.video.prores",FX_PRORES_ABI);
    else if(stage==QUERY)result=fx_sdk_platform(&client,api,FX_BIND_CALL,binding,FX_PRORES_QUERY,0u,0,0u);
    else if(stage==SELECT) {
        FxProResSelection request={FX_PRORES_ABI,sizeof(request),desired,0u};
        result=fx_sdk_platform(&client,api,FX_BIND_CALL,binding,FX_PRORES_SELECT,0u,&request,sizeof(request));
    } else if(stage==POLL)result=fx_sdk_events(&client,api);
    else {
        FxUiControl c={.abi=1u,.bytes=sizeof(c),.id=stage==PAGE?1u:stage==CHOICE?2u:3u,
            .parent=stage==PAGE?0u:1u,.kind=stage==PAGE?FX_UI_PAGE:stage==CHOICE?FX_UI_CHOICE:FX_UI_ACTION,
            .flags=FX_UI_VISIBLE};
        if(stage==PAGE){c.flags|=FX_UI_ENABLED;text(c.label,"ProRes 品质");}
        else if(stage==CHOICE) {
            if(editable())c.flags|=FX_UI_ENABLED;
            text(c.label,"品质");c.value=current.selected;c.option_count=4u;
            text(c.options[0],"HQ（默认）");text(c.options[1],"标准");
            text(c.options[2],"LT");text(c.options[3],"Proxy");
        } else {
            text(c.label,"状态");
            text(c.text,unavailable?"功能未准备完成":
                current.flags&FX_PRORES_FAULT?"跟踪错误，请重启":
                current.flags&FX_PRORES_REVOKED?"正在停止":
                current.flags&FX_PRORES_RECORDING?"录制中，已锁定":"准备完成");
        }
        result=fx_sdk_ui(&client,api,&c);
    }
    return result==FX_OK?0:8;
}
