# Fenestra Z8 C3.11 公开源码版

Fenestra 的 Nikon Z 8 C3.11 移植。这里提供核心、WLM SDK、功能插件、Windows 调试工作台和本地构建工具的源码。

> 这是实验性代码。上游 Fenestra 已发现潜在变砖风险并要求勿刷入其当前版本。本仓库供源码研究和离线构建，尚无整机资格验证。见 [WonderLantern/Fenestra](https://github.com/WonderLantern/Fenestra)。

## WLM 下载

七个功能插件可在 [GitHub Releases](https://github.com/PIPA12344/Fenestra-Z8-C311-source/releases) 单独下载。发布页列出各插件需要的底座和 SHA-256 校验文件。

## 本地构建

安装 Python 3.11+ 和 LLVM，并把 `clang`、`ld.lld`、`llvm-objcopy`、`llvm-nm` 加入 PATH。自行下载并解压 C3.11，将 `Z_8_0311.bin` 放入本地 `inputs/`。工具会在提取前核对原厂包 SHA-256。见 [尼康下载中心](https://downloadcenter.nikonimglib.com/en/download/fw/606.html)。

```sh
python -m pip install -r requirements-z8.txt
python fenestra_z8.py extract --firmware inputs/Z_8_0311.bin --external-project vendor/NikonFirmwarePatch-container --output inputs/z8-c311-main.bin
python fenestra.py build --target z8-c311 --firmware inputs/z8-c311-main.bin --output build/local-core
python fenestra_z8.py build-module examples/prores/module.c --id prores --output-dir build/local-prores
```

构建路径需是新目录。最后一条生成的是 ProRes 前端，实际设置生效需要带相应后端的底座或原生 WLM。需要底座和其他插件的构建命令时，见 [构建说明](docs/z8-build.zh-CN.md)。

## 调试与开发

- Windows 调试工作台由 `Start-Z8-Debug.cmd` 启动。先安装 `requirements-host.txt`，再通过工作台操作已启用 Fenestra USB Debug 的相机。调用记录在本地生成。工作台的卡上传需要与实际构建匹配的本地原生调用配置。
- SDK 接口和模块格式见 [Z8 SDK](docs/z8-sdk.md) 和 [模块开发](docs/module-development.md)。
- `python -B verify_z8_source.py` 校验公开源码清单。离线测试可运行 `python -B -m unittest discover -s tests`（先创建本地 `build/` 目录）。依赖本地构建或相机原厂输入的测试会在对应输入缺席时跳过。

## 来源与许可

本移植基于 [WonderLantern/Fenestra 的 b199dcd 提交](https://github.com/WonderLantern/Fenestra/tree/b199dcdfbb0ef536c6269ae5a9d97c376d488b8b)，增加 Z8 C3.11 适配、原生插件构建和调试工作台。公开版从移植源码筛选整理。

项目主许可是 [CC BY-NC-SA 4.0](LICENSE)，附 [商业摄影例外](EXCEPTIONS.md)。独立依赖及另有许可标注的模块按各自条款授权。上游署名和许可见 [NOTICE](NOTICE) 与依赖目录。
