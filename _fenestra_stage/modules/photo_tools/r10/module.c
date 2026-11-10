/* SPDX-License-Identifier: GPL-3.0-only
 * Loadable control surface for camera.photo.tools/v1. Native code and all file
 * I/O remain owned by the reviewed feature-suite core until reboot.
 */
#include "fenestra_sdk.h"
#include "fenestra_photo_tools.h"
#include "fenestra_memory.h"
enum {CONFIGURE,PAGE,OPEN,QUERY,UI,POLL,START,CANCEL,DECLARE,TOPEN,TREAD,TSTORE};
/* RAW-only test capsule only (r6): page 0x409fc000 = "C40RAW03", +8 C30 rate (0 = 30 fps, 1 = 40 fps in FX RAW-only),
 * +0xc its numerator, +0x30 C60 rate (1 = 80 fps, DX RAW-only on sensor driver 12), +0x34 C120 rate (1 = 140 fps,
 * RAW-only), +0x38/+0x3c their numerators. RAM only: the capsule starts every boot at the stock rates. */
#define C40_TABLE "photo.c40.rate"
#define C40_PAGE UINT64_C(0x40b51000)
static const char c40_marker[8]={'C','4','0','R','A','W','0','3'};
static FxSdkClient client;
static uint32_t stage,ui_id=1u,unavailable,published,armed,mode,revision[12],opened,rate[3],rate_ok,want,want_at;
static uint64_t binding,table;
static FxPhotoToolsStatus current,shown;
/* Copied UI strings are UTF-8. Keep complete codepoints and reserve the full
 * u32/u32 suffix before appending dynamic progress. */
static void append_text(char *out,uint32_t capacity,uint32_t *at,const char *in){
    while(*in){uint32_t c=(uint8_t)*in,n=1u,minimum=0u;
        if(c<128u){}else if(c>=0xc2u&&c<0xe0u){c&=31u;n=2u;minimum=128u;}
        else if(c>=0xe0u&&c<0xf0u){c&=15u;n=3u;minimum=2048u;}
        else if(c>=0xf0u&&c<0xf5u){c&=7u;n=4u;minimum=65536u;}else break;
        uint32_t valid=1u;for(uint32_t i=1u;i<n;++i){uint32_t b=(uint8_t)in[i];if((b&192u)!=128u){valid=0u;break;}c=(c<<6)|(b&63u);}
        if(!valid||c<minimum||c>0x10ffffu||(c>=0xd800u&&c<=0xdfffu)||*at>=capacity||n>=capacity-*at)break;
        for(uint32_t i=0u;i<n;++i)out[(*at)++]=in[i];in+=n;
    }if(capacity)out[*at<capacity?*at:capacity-1u]=0;
}
static void copy_text(char *out,uint32_t capacity,const char *in){uint32_t at=0u;append_text(out,capacity,&at,in);}
#define text(out,in) copy_text((out),(uint32_t)sizeof(out),(in))
static void number(char *out,uint32_t capacity,uint32_t *at,uint32_t n){char reverse[10],digits[11];uint32_t length=0u,i=0u;do{reverse[length++]=(char)('0'+n%10u);n/=10u;}while(n);while(length)digits[i++]=reverse[--length];digits[i]=0;append_text(out,capacity,at,digits);}
static int snapshot(const FxServiceReply *r){
    if(r->bytes!=sizeof(current))return 0;FxPhotoToolsStatus v;memcpy(&v,r->data,sizeof(v));
    if(v.abi!=FX_PHOTO_TOOLS_ABI||v.bytes!=sizeof(v)||v.mode>FX_PHOTO_ALL)return 0;current=v;return 1;
}
static int ready(void){return !unavailable&&(current.flags&FX_PHOTO_MERGE)&&!(current.flags&(FX_PHOTO_RUNNING|FX_PHOTO_REVOKED|FX_PHOTO_FAULT));}
static const char *state(void){
    if(unavailable)return "功能未准备完成";
    if(current.flags&FX_PHOTO_FAULT)return "处理错误，请重启";
    if(current.flags&FX_PHOTO_REVOKED)return "正在停止";
    if(current.flags&FX_PHOTO_RUNNING)return current.flags&FX_PHOTO_CANCELLING?"取消中，等待写入":"处理中";
    if(!current.serial)return "准备完成";
    switch(current.status){case 0:return "已完成";case 5:return "内存不足";case 7:return "已取消";
      case 11:return "输出文件已存在";case 16:return "未检测到存储卡";case 17:return "相机处理中";
      case 18:return "暂无完整新组";default:return "文件组或读写错误";}
}
static void progress(char out[FX_UI_TEXT]){uint32_t at=0u;append_text(out,current.total?FX_UI_TEXT-22u:FX_UI_TEXT,&at,state());if(current.total){append_text(out,FX_UI_TEXT,&at," ");number(out,FX_UI_TEXT,&at,current.rows);append_text(out,FX_UI_TEXT,&at,"/");number(out,FX_UI_TEXT,&at,current.total);}}
static void control(FxUiControl *c){
    memset(c,0,sizeof(*c));c->abi=1;c->bytes=sizeof(*c);c->id=ui_id;c->parent=ui_id==1u?0u:1u;
    c->kind=ui_id==1u?FX_UI_PAGE:(ui_id==3u||ui_id==4u||ui_id>=9u)?FX_UI_CHOICE:FX_UI_ACTION;c->flags=FX_UI_VISIBLE;
    if(ui_id==1u){c->flags|=FX_UI_ENABLED;text(c->label,"照片工具");}
    else if(ui_id==2u){text(c->label,"RAW 预拍摄");text(c->text,"C15/C30：相机图像品质 / RAW菜单");}
    else if(ui_id==3u){if(ready())c->flags|=FX_UI_ENABLED;text(c->label,"合并组");c->value=mode;c->option_count=2;text(c->options[0],"最新未合并组");text(c->options[1],"所有未合并组");}
    else if(ui_id==4u){if(ready())c->flags|=FX_UI_ENABLED;text(c->label,"确认合并");c->value=armed;c->option_count=2u;text(c->options[0],"否");text(c->options[1],"是");}
    else if(ui_id==5u){if(ready()&&armed)c->flags|=FX_UI_ENABLED;text(c->label,"开始合并");text(c->text,"4/8/16/32 FX 无损NEF；保留原片");}
    else if(ui_id==6u){if((current.flags&FX_PHOTO_RUNNING)&&!(current.flags&FX_PHOTO_CANCELLING))c->flags|=FX_UI_ENABLED;text(c->label,"取消合并");}
    else if(ui_id==7u){text(c->label,"合并状态");progress(c->text);}
    else if(ui_id==9u){text(c->label,"C30 连拍速度");
        if(rate_ok){c->flags|=FX_UI_ENABLED;c->value=rate[0];c->option_count=2u;text(c->options[0],"30 张/秒");text(c->options[1],"40 张/秒（FX 仅RAW）");}
        else{c->option_count=1u;text(c->options[0],"不可用");}}
    else if(ui_id==10u){text(c->label,"C60 连拍速度");
        if(rate_ok){c->flags|=FX_UI_ENABLED;c->value=rate[1];c->option_count=2u;text(c->options[0],"60 张/秒");text(c->options[1],"80 张/秒（DX 仅RAW）");}
        else{c->option_count=1u;text(c->options[0],"不可用");}}
    else if(ui_id==11u){text(c->label,"C120 连拍速度");
        if(rate_ok){c->flags|=FX_UI_ENABLED;c->value=rate[2];c->option_count=2u;text(c->options[0],"120 张/秒");text(c->options[1],"140 张/秒（仅RAW）");}
        else{c->option_count=1u;text(c->options[0],"不可用");}}
    else {text(c->label,"RAW 格式");text(c->text,current.raw_recording==1u?"C15/C30下无损选项录制HE*":current.raw_recording==3u?"HE*":current.raw_recording==4u?"HE":"使用FX/DX L、C15/C30及HE/HE*");}
}
int fenestra_module_entry(const FxModuleServices *base){
    if(!base||base->bytes!=sizeof(FxServiceModule))return 1;const FxServiceModule *api=(const FxServiceModule *)base;
    if(api->service_abi!=FX_SERVICE_ABI)return 2;
    if(client.pending){
        FxServiceReply r;FxResult t=fx_sdk_take(&client,api,&r);if(t==FX_BUSY)return 0;if(t!=FX_OK)return 3;
        if(r.result==FX_BUSY&&r.reserved==FX_SERVICE_DEFERRED){} /* Preserve the exact START/CANCEL until the native reply arrives. */
        else if(stage==START||stage==CANCEL){armed=0;published=0;stage=QUERY;}
        else if(r.result==FX_BUSY){} /* Retry idempotent setup/query only. */
        /* The first directory snapshot needs this page before QUERY can wait
         * on the GUI task captured by the directory's page-open callback. */
        else if(stage==CONFIGURE&&r.result==FX_OK)stage=PAGE;
        else if(stage==PAGE&&r.result==FX_OK){revision[1]=(uint32_t)r.value[0];stage=DECLARE;}
        /* The rate table is optional: any failure leaves the row disabled and continues to the photo service. */
        else if(stage==DECLARE)stage=(r.result==FX_OK||r.result==FX_STATE)?TOPEN:OPEN;    /* STATE: declared earlier this boot */
        else if(stage==TOPEN){if(r.result==FX_OK&&r.value[0]){table=r.value[0];stage=TREAD;}else stage=OPEN;}
        else if(stage==TREAD){
            uint32_t v[16];rate_ok=0u;
            if(r.result==FX_OK&&r.bytes==sizeof(v)){memcpy(v,r.data,sizeof(v));
                if(!memcmp(v,c40_marker,8)&&v[2]<2u&&v[3]==40000u&&v[12]<2u&&v[13]<2u&&v[14]==80000u&&v[15]==140000u){
                    rate[0]=v[2];rate[1]=v[12];rate[2]=v[13];rate_ok=1u;}}
            if(!opened)stage=OPEN;else{published=0;stage=binding?QUERY:UI;ui_id=2u;}
        }
        else if(stage==TSTORE)stage=TREAD;                                 /* show what the page now holds */
        else if(stage==OPEN){opened=1u;if(r.result==FX_OK&&r.value[0]){binding=r.value[0];stage=QUERY;}else if(r.result==FX_CAPABILITY||r.result==FX_STATE){unavailable=1;stage=UI;ui_id=2;}else return 4;}
        else if(stage==QUERY&&r.result==FX_OK){if(!snapshot(&r))return 5;if(!published||memcmp(&current,&shown,sizeof(current))){stage=UI;ui_id=2;}else stage=POLL;}
        else if(stage==UI&&r.result==FX_OK){revision[ui_id]=(uint32_t)r.value[0];if(++ui_id==12u){shown=current;published=1;stage=POLL;}}
        else if(stage==POLL&&(r.result==FX_OK||r.result==FX_EMPTY)){
            stage=binding?QUERY:POLL;
            if(r.result==FX_OK&&r.bytes==sizeof(FxUiEvent)){
                FxUiEvent e;memcpy(&e,r.data,sizeof(e));
                if(e.control<12u&&e.reserved==revision[e.control]){
                    if(e.control==3u&&e.event==FX_INPUT_CHANGE&&e.value<2u&&ready()){mode=e.value;armed=0;published=0;}
                    else if(e.control==4u&&e.event==FX_INPUT_CHANGE&&e.value<2u&&ready()){armed=e.value;published=0;}
                    else if(e.control==5u&&e.event==FX_INPUT_ACTIVATE&&ready()&&armed)stage=START;
                    else if(e.control==6u&&e.event==FX_INPUT_ACTIVATE&&(current.flags&FX_PHOTO_RUNNING))stage=CANCEL;
                    else if(e.control>=9u&&e.event==FX_INPUT_CHANGE&&e.value<2u&&rate_ok&&table){want=e.value;want_at=e.control==9u?8u:e.control==10u?0x30u:0x34u;stage=TSTORE;}
                }
            }
        }else return 6;
    }
    FxResult r;
    if(stage==CONFIGURE)r=fx_sdk_lifecycle(&client,api,FX_LIFE_CONFIGURE,FX_MODULE_EXTENSION);
    else if(stage==DECLARE){
        FxBindingInfo d;memset(&d,0,sizeof(d));text(d.name,C40_TABLE);
        d.kind=FX_BIND_TABLE;d.abi=FX_CALL_BYTES;d.version=1u;d.flags=FX_BIND_READ|FX_BIND_WRITE;
        d.capabilities=FX_SDK_NATIVE;d.bytes=64u;d.address=C40_PAGE;r=fx_sdk_declare(&client,api,&d);
    }
    else if(stage==TOPEN)r=fx_sdk_open(&client,api,C40_TABLE,1u);
    else if(stage==TREAD)r=fx_sdk_platform(&client,api,FX_BIND_COPY,table,0u,64u,0,0u);
    else if(stage==TSTORE){uint32_t v=want;r=fx_sdk_platform(&client,api,FX_BIND_STORE,table,want_at,sizeof(v),&v,sizeof(v));}
    else if(stage==OPEN)r=fx_sdk_open(&client,api,"camera.photo.tools",FX_PHOTO_TOOLS_ABI);
    else if(stage==QUERY)r=fx_sdk_platform(&client,api,FX_BIND_CALL,binding,FX_PHOTO_TOOLS_QUERY,0,0,0);
    else if(stage==START){FxPhotoToolsStart v={FX_PHOTO_TOOLS_ABI,sizeof(v),mode,FX_PHOTO_TOOLS_CONFIRM};r=fx_sdk_platform(&client,api,FX_BIND_CALL,binding,FX_PHOTO_TOOLS_START,0,&v,sizeof(v));}
    else if(stage==CANCEL)r=fx_sdk_platform(&client,api,FX_BIND_CALL,binding,FX_PHOTO_TOOLS_CANCEL,0,0,0);
    else if(stage==POLL)r=fx_sdk_events(&client,api);
    else {FxUiControl c;control(&c);r=fx_sdk_ui(&client,api,&c);}
    return r==FX_OK?0:7;
}
