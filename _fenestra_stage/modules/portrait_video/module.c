/* SPDX-License-Identifier: GPL-3.0-only */
#include "fenestra_sdk.h"

int fenestra_module_entry(const FxModuleServices *base) {
    static FxSdkClient client;
    static uint32_t published;
    if (!base || base->bytes != sizeof(FxServiceModule)) return 1;
    const FxServiceModule *api = (const FxServiceModule *)base;
    if (api->service_abi != FX_SERVICE_ABI) return 2;
    if (published) return 0;
    if (client.pending) {
        FxServiceReply reply;
        FxResult result = fx_sdk_take(&client, api, &reply);
        if (result == FX_BUSY) return 0;
        if (result != FX_OK) return 3;
        if (reply.result == FX_OK) { published = 1u; return 0; }
        if (reply.result != FX_BUSY) return 4;
    }
    const FxUiControl page = {.abi = 1u, .bytes = sizeof(page), .id = 1u,
        .kind = FX_UI_PAGE, .flags = FX_UI_VISIBLE | FX_UI_ENABLED,
        .label = "Portrait video rotation"};
    FxResult result = fx_sdk_ui(&client, api, &page);
    return result == FX_OK || result == FX_BUSY ? 0 : 5;
}
