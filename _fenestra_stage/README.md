# Fenestra Z8 C3.11 公开源码版

Fenestra 的 Nikon Z 8 C3.11 移植。这里提供核心、WLM SDK、功能插件、Windows 调试工作台和本地构建工具的源码。

> 这是实验性代码。WonderLantern/Fenestra 已发现潜在变砖风险并要求勿刷入其当前版本。本仓库供源码研究和离线构建，尚无整机资格验证。见 [WonderLantern/Fenestra](https://github.com/WonderLantern/Fenestra)。

## 开始使用

1. **确认底座。** 当前七个功能 WLM 都配套 Nikon Z 8 C3.11 的 `cold-retry3`。已有该底座可以直接装插件。需要自行构建时，按手册下载原始输入并使用固定源码标签。[构建底座](docs/z8-usage.zh-CN.md#准备底座)
2. **下载和安装。** 从 Release 选取所需的 WLM，复制到启动卡根目录的 `FENESTRA/`，再做一次完整冷启动。[下载 WLM](https://github.com/PIPA12344/Fenestra-Z8-C311-source/releases/tag/wlm-2026.11.10-r10) · [安装、更新与停用](docs/z8-usage.zh-CN.md#安装插件)
3. **机内使用。** 在设定菜单进入 Fenestra 激活，插件界面在并列的 **Z8Mouldes**。画幅、ProRes、H.265、Log、照片工具 r10、卡内 WAV 和竖拍旋转各有使用步骤。[七个插件的用法](docs/z8-usage.zh-CN.md#七个插件怎么用)

## 调试与开发

- Windows 调试工作台由 `Start-Z8-Debug.cmd` 启动，可查看状态和构建身份，执行 RTOS 命令并读取寄存器和内存。[工作台用法](docs/z8-usage.zh-CN.md#使用-windows-调试工作台)
- 核心和插件的开发构建见[本地构建](docs/z8-build.zh-CN.md)。SDK 接口和模块格式见 [Z8 SDK](docs/z8-sdk.md) 和 [模块开发](docs/module-development.md)。
- `python -B verify_z8_source.py` 校验公开源码清单。离线测试可运行 `python -B -m unittest discover -s tests`（先创建本地 `build/` 目录）。依赖本地构建或相机原厂输入的测试会在对应输入缺席时跳过。

## 来源与许可

本移植基于 [WonderLantern/Fenestra 的 b199dcd 提交](https://github.com/WonderLantern/Fenestra/tree/b199dcdfbb0ef536c6269ae5a9d97c376d488b8b)，增加 Z8 C3.11 适配、原生插件构建和调试工作台。公开版从移植源码筛选整理。

部分 WLM 基于其他项目的功能实现移植开发。画幅、ProRes、H.265、Log 色彩和照片工具使用 NikonFirmwarePatch 的对应实现；卡内 WAV 由 z8-card-wav 适配为 WLM。[功能来源与移植](docs/z8-usage.zh-CN.md#功能来源与移植)

项目主许可是 [CC BY-NC-SA 4.0](LICENSE)，附 [商业摄影例外](EXCEPTIONS.md)。独立依赖及另有许可标注的模块按各自条款授权。署名和许可见 [NOTICE](NOTICE) 与依赖目录。
