/* SPDX-License-Identifier: GPL-3.0-only
 * Stock-native profile proof: the existing core declares a checked function,
 * then executes one pointer-free getter on the captured original UI task.
 * This plugin never issues a property SET, memory WRITE, or hook COMMIT.
 */
#include "fenestra_native_profile.h"
#include "fenestra_tasks.h"
#include "fenestra_memory.h"
#include "profile.h"
enum {CONFIGURE,PAGE,PROFILE_BEGIN,PROFILE_STEP,OPEN_TASKS,QUERY_TASKS,
      STATE_ROW,VALUE_ROW,ACTION_ROW,POLL,POST_READ,FETCH_READ};
static FxSdkClient client;
static FxNativeProfile profile;
static uint32_t stage,ready,have_value,value,action_revision,failed;
static uint64_t tasks,ticket;
static const char *status="Checking stock profile";
static void copy_text(char *out,const char *in) {uint32_t i=0u;do{out[i]=in[i];}while(in[i++]);}
static void number(char *out,uint32_t n) {
    char reverse[10];uint32_t size=0u;
    do{reverse[size++]=(char)('0'+n%10u);n/=10u;}while(n);
    uint32_t i=0u;while(size)out[i++]=reverse[--size];out[i]=0;
}
static void fault(const char *reason) {status=reason;ready=0u;failed=1u;stage=STATE_ROW;}
int fenestra_module_entry(const FxModuleServices *base) {
    if(!base || base->bytes!=sizeof(FxServiceModule))return 1;
    const FxServiceModule *api=(const FxServiceModule *)base;
    if(api->service_abi!=FX_SERVICE_ABI)return 2;
    if(stage==PROFILE_BEGIN) {
        FxResult r=fx_native_profile_start(&profile,&client,&fx_native_probe_bindings[0],
            fx_native_probe_stock_main_sha256,FX_NATIVE_PROBE_NATIVE_BEGIN,FX_NATIVE_PROBE_NATIVE_END);
        if(r!=FX_OK)fault("Invalid native profile");else stage=PROFILE_STEP;
    }
    if(stage==PROFILE_STEP) {
        FxResult r=fx_native_profile_tick(&profile,api);
        if(r==FX_BUSY)return 0;
        if(r!=FX_OK)fault("Stock/profile mismatch");else stage=OPEN_TASKS;
    }
    if(client.pending) {
        FxServiceReply r;FxResult taken=fx_sdk_take(&client,api,&r);
        if(taken==FX_BUSY)return 0;
        if(taken!=FX_OK)return 3;
        if(r.result==FX_BUSY) { /* POST was not admitted, or FETCH is not ready. */ }
        else if(stage==CONFIGURE && r.result==FX_OK)stage=PAGE;
        else if(stage==PAGE && r.result==FX_OK)stage=PROFILE_BEGIN;
        else if(stage==OPEN_TASKS) {
            if(r.result==FX_OK && r.value[0] && r.value[1]==FX_BIND_DEVICE && r.value[2]==FX_CALL_DEVICE) {tasks=r.value[0];stage=QUERY_TASKS;}
            else fault("core.tasks unavailable");
        } else if(stage==QUERY_TASKS) {
            if(r.result!=FX_OK || r.bytes!=sizeof(FxTaskInfo))fault("Invalid task contract");
            else {
                FxTaskInfo info;memcpy(&info,r.data,sizeof(info));
                static const char context[FX_TASK_CONTEXT_BYTES]="original.ui";
                if(info.abi!=1u || info.bytes!=sizeof(info) || (info.operations&10u)!=10u || memcmp(info.context,context,sizeof(context)))fault("Wrong native context");
                else {ready=info.task?1u:0u;status=ready?"Verified; read only":"UI task not captured; Refresh";stage=STATE_ROW;}
            }
        } else if(stage==STATE_ROW && r.result==FX_OK)stage=VALUE_ROW;
        else if(stage==VALUE_ROW && r.result==FX_OK)stage=ACTION_ROW;
        else if(stage==ACTION_ROW && r.result==FX_OK) {action_revision=(uint32_t)r.value[0];stage=POLL;}
        else if(stage==POLL && (r.result==FX_OK || r.result==FX_EMPTY)) {
            if(r.result==FX_OK && r.bytes==sizeof(FxUiEvent)) {
                FxUiEvent event;memcpy(&event,r.data,sizeof(event));
                if(!failed && event.control==4u && event.event==FX_INPUT_ACTIVATE && event.reserved==action_revision)
                    stage=ready?POST_READ:QUERY_TASKS;
            }
        } else if(stage==POST_READ) {
            if(r.result==FX_OK && r.value[0]) {ticket=r.value[0];stage=FETCH_READ;}
            else fault("Native read not queued");
        } else if(stage==FETCH_READ) {
            if(r.result==FX_OK && !r.bytes && r.value[0]==ticket && r.value[1]==FX_OK) {
                value=(uint32_t)r.value[2];have_value=1u;status="Getter completed on native UI";stage=STATE_ROW;
            } else fault("Native read failed");
        } else return 4;
    }
    if(stage==PROFILE_BEGIN || stage==PROFILE_STEP)return 0;
    FxResult r;
    if(stage==CONFIGURE)r=fx_sdk_lifecycle(&client,api,FX_LIFE_CONFIGURE,FX_MODULE_EXTENSION);
    else if(stage==OPEN_TASKS)r=fx_sdk_open(&client,api,"core.tasks",1u);
    else if(stage==QUERY_TASKS)r=fx_sdk_platform(&client,api,FX_BIND_CALL,tasks,FX_TASK_QUERY,0u,0,0u);
    else if(stage==POST_READ) {
        FxTaskPost post={.context="original.ui",.binding=profile.handle,.call_bytes=64u};
        const uint64_t args[8]={UINT64_C(0x05001261),0u,0u,0u,0u,0u,0u,0u};
        memcpy(post.call,args,sizeof(args));
        r=fx_sdk_platform(&client,api,FX_BIND_CALL,tasks,FX_TASK_POST,0u,&post,sizeof(post));
    } else if(stage==FETCH_READ)r=fx_sdk_platform(&client,api,FX_BIND_CALL,tasks,FX_TASK_FETCH,ticket,0,0u);
    else if(stage==POLL)r=fx_sdk_events(&client,api);
    else {
        uint32_t id=stage==PAGE?1u:stage==STATE_ROW?2u:stage==VALUE_ROW?3u:4u;
        FxUiControl c={.abi=1u,.bytes=sizeof(c),.id=id,.parent=id==1u?0u:1u,
            .kind=id==1u?FX_UI_PAGE:FX_UI_ACTION,.flags=FX_UI_VISIBLE};
        if(id==1u){c.flags|=FX_UI_ENABLED;copy_text(c.label,"Native profile probe");}
        else if(id==2u){copy_text(c.label,"State");copy_text(c.text,status);}
        else if(id==3u){copy_text(c.label,"RAW quality property");if(have_value)number(c.text,value);else copy_text(c.text,"Not read");}
        else {if(!failed)c.flags|=FX_UI_ENABLED;copy_text(c.label,ready?"Read RAW quality":"Refresh native UI");}
        r=fx_sdk_ui(&client,api,&c);
    }
    return r==FX_OK?0:5;
}
