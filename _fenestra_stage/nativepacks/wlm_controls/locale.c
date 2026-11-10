/* SPDX-License-Identifier: GPL-3.0-only
 * Only the selected native text-table descriptor. No providers or feature code.
 */
#include "fenestra_native_pack.h"
#include "fenestra_native_locale.h"
#include "fx_wlm_locale.h"
const FxNativeLocale fx_wlm_locale={.abi=1u,.bytes=sizeof(FxNativeLocale),.languages=FX_WLM_LANGUAGES,.text_count=FX_WLM_TEXT_COUNT,
    .offsets_directory=FX_WLM_OFFSETS_DIRECTORY,.pools_directory=FX_WLM_POOLS_DIRECTORY};
static const FxNativePackSymbol symbol={.name="camera.locale.text",.version=1u,.flags=FX_NATIVE_PACK_SYMBOL_DATA,.address=(uintptr_t)&fx_wlm_locale};
static uint32_t context;
static FxResult event(void *c,uint32_t op){(void)c;return op>=FX_NATIVE_PACK_GUI_POLL&&op<=FX_NATIVE_PACK_PUBLISH?FX_OK:FX_ARGUMENT;}
static uint32_t pending(void *c){(void)c;return 0u;}
FxResult fx_wlm_locale_entry(const FxNativePackHost *host,FxNativePackExports *out){
    if(!host||!out||host->abi!=FX_NATIVE_PACK_ABI||host->bytes<sizeof(*host))return FX_ARGUMENT;
    *out=(FxNativePackExports){.abi=FX_NATIVE_PACK_ABI,.bytes=sizeof(*out),.symbol_count=1u,.symbols=&symbol,.context=&context,.event=event,.pending=pending};return FX_OK;
}
const uint32_t fx_wlm_component_metadata[]={UINT32_C(0x46585743),FX_NATIVE_PACK_ABI,0u,sizeof(FxNativePackHost),sizeof(FxNativePackExports),sizeof(FxNativePackProvider),sizeof(FxNativePackSymbol),sizeof(context)};
