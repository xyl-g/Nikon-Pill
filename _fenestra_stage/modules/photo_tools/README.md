# Photo tools 插件（Z8 C3.11）

公开版 `photo_tools.wlm`（r10） 配套 `cold-retry3`，通过 Fenestra 加载照片工具的原生组件和菜单。使用机内「Z8Mouldes → 照片工具」查看状态和批量合并。RAW 预拍仍由相机原厂照片菜单操作。[安装和操作步骤](../../docs/plugins/photo_tools.md)

本模块使用 `camera.photo.tools/v1` 服务。原生实现采用 NikonFirmwarePatch 的固定版本，运行中的任务和文件写入由该组件管理。

## RAW 预拍

使用相机原生照片菜单：FX/DX、L、C15/C30，然后选择普通“画质”和“RAW 记录”。HE/HE★ 可用；选择“无损压缩”实际回退 HE★。这些设置属于 C15/C30 路径。r10 的 DX C60、FX C120/C140 走 RAW-only 路径，详见上面的操作文档。插件显示入口说明和当前 RAW 记录值。

写入失败或超时可能生成 Z8PRESTP.BIN；保留两卡文件。停止文件机制继续有效。停用插件后，做一次完整冷启动以应用卡上的新状态。

## 像素位移

原生照片菜单可浏览和选择任意组。插件提供“最新未合成组／全部未合成组”两个批处理入口，把「确认合并」设为「是」才启用「开始合并」。它调用同一个永久原生工作线程，不另开相机连接。开始请求先进入 GUI 队列，原厂 GUI 线程确认没有其他照片任务、原生浏览页或合成页占用后才投递，原生任务忙时返回 Busy。

支持完整 4/8/16/32 张、FX/L、8256×5504、14-bit 无损压缩 NEF 组，支持任意起始文件编号和跨文件夹组。HE/HE★、DX、残缺、重复帧和 RAW 预拍输出不适用。输出由原生后端以新的 _MERGED.NEFX 或带序号文件名写入，原片及已有输出保留。4/8 张组输出 8256×5504，16/32 张组输出 16512×11008。

「取消合并」请求原生工作线程取消；等待写入与关闭完成后才能卸载模块或完成核心停止。断开 GUI 前的停止请求也会直接原子设置当前序列的原生取消位。原厂文件句柄、任务块和线程栈的生命周期沿用原生后端，仍运行时保留核心和 owner。

## r10 源码

`r10/precapture.c` 和 `r10/module.c` 是 r10 原生实现和 WLM 界面的编译输入，由 `host/build_photo_tools_r10.py` 打包。[构建步骤](../../docs/z8-build.zh-CN.md#照片工具-r10)

r10 的 RAW 实现基于 NikonFirmwarePatch v0.4.0-rc.2 的预拍摄源码并采用 GPL-3.0-only。构建保留基础版的像素位移组件和共享依赖，替换照片工具的 RAW 连拍后端及前端界面。[预拍摄源码](https://github.com/XinmiaoZhao/NikonFirmwarePatch/tree/a21fc8c04b6dd863439c4192d387b755633287f6/z8precapture) · [GPL-3.0 许可](../../vendor/NikonFirmwarePatch-container/LICENSE)

## 验证范围

tests.test_photo_tools 编译并执行真实服务、原生桥和真实 WLM 状态机的合成测试：GUI 投递、序列唯一 claim、迟到线程撤销、互斥、取消、停止期间延迟 detach、恢复时保留序列、旧核心提示及过期菜单事件拒绝。原生 C 源通过 AArch64 严格警告交叉编译，WLM 通过标准包校验。

运行区偏移由固定版本的 NikonFirmwarePatch `ps8.c` 交叉编译导出，绑定到该实现和 C3.11。上述是代码、ABI 和合成传输证据，本次没有拍摄或 NEFX 输出的实机验收，不能称为整机资格验证。

功能实现来自 NikonFirmwarePatch。[对应源码](https://github.com/XinmiaoZhao/NikonFirmwarePatch/tree/a21fc8c04b6dd863439c4192d387b755633287f6)。本模块 GPL-3.0-only；原生算法遵循对应源码的许可。
