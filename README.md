# 💊 Nikon Pill

**Nikon Z8 · C 3.11 固件补丁图形构建器** —— 一份原厂固件，两套补丁方案二选一：

| 方案 | 来源 | 特点 |
|---|---|---|
| **NikonFirmwarePatch** | [XinmiaoZhao/NikonFirmwarePatch](https://github.com/XinmiaoZhao/NikonFirmwarePatch) v0.4.0 | 四功能组任意组合（15 种）、自定版本号（相机菜单显示）、构建含独立字节级校验 |
| **Fenestra Z8 (cold-retry3)** | [PIPA12344/Fenestra-Z8-C311-source](https://github.com/PIPA12344/Fenestra-Z8-C311-source) r10 | 官方基线一键应用（秒级），机内经 **Z8Mouldes** 模块菜单使用，功能经 `.wlm` 插件扩展 |

两套工具包在打包时**全部内置**（构建脚本自动从各自上游下载并做 SHA-256 校验），最终用户**无需安装 Python、无需编译器、无需下载任何工具**。当前版本 **0.2.6**，变更史见 [CHANGELOG.md](CHANGELOG.md)。

![Nikon Pill 主界面 — NFP 方案全选四组构建完成并通过独立校验](docs/screenshot-main.png)

## ✨ 特性

**两方案共有**

- **校验前置** — 选择固件后自动核对官方 SHA-256（`d67413c2…`）；不匹配时就地展开红色详情面板（期望/实际哈希 + 修复建议），阻止无效构建
- **结构化日志** — 等宽字体、级别着色（info/warn/err/ok），引擎输出的 JSON 噪音已过滤
- **方案切换自动重校验** — 切换构建方案时对已选固件自动重新校验，按钮状态实时刷新

**仅 NikonFirmwarePatch 方案**

- **功能组卡片** — 四组任选组合（共 15 种有效组合），点卡片即切换选中态
- **版本号即菜单版本** — 公开版本 `NN.NN` 直接写入相机菜单显示的固件版本，实时预览「相机将显示 C xx.xx」
- **时间戳输出** — 输出位置可选已存在的文件夹；每次构建自动创建 `v{版本}-{tag}_{时间戳}/` 子目录，永不覆盖历史产物
- **构建后自动独立校验** — 构建完成后自动运行上游 `verify.py` 字节级比对（PASS 才算成功），支持随时「重新校验」

**仅 Fenestra 方案**

- **零选择一键应用** — 功能与版本由官方 `cold-retry3.fxpatch` 决定，界面自动折叠不需要的区块
- **结果哈希钉值比对** — 产物 SHA-256 与官方 r10 基线（`0a9cdd07…`）自动比对，一致即 PASS
- **极轻引擎** — 仅需上游 2 个纯标准库文件（`patcher.py` + `host/cipher_patch.py`，共 ~350 KB）

![校验失败时的就地错误面板](docs/screenshot-error.png)

## 📦 下载使用（最终用户）

到 [Releases](https://github.com/xyl-g/Nikon-Pill/releases) 下载对应平台产物：

| 平台 | 文件 | 安装 |
|---|---|---|
| macOS | `NikonPill-<版本>-macOS.dmg` | 打开 → 拖入 Applications → 首次**右键 → 打开**（ad-hoc 签名，Gatekeeper 询问一次） |
| Windows | `NikonPill-<版本>-Windows.exe` | 双击运行（SmartScreen 首次同样询问） |

应用内流程：

1. **构建方案** — 两张方案卡二选一（NFP 组合构建 / Fenestra 基线应用）
2. **固件文件** — 选择 Nikon 官方 `Z_8_0311.bin`（或直接拖入窗口），自动校验
3. **按方案分支**：
   - *NikonFirmwarePatch*：点功能组卡片组合（画面构图 / 视频品质 / Log 色彩 / 拍照工具），填公开版本（相机将显示）与内部 tag
   - *Fenestra*：功能与版本由 `cold-retry3.fxpatch` 决定，无需选择；产物 `Z_8_0311-cold-retry3.bin`
4. **输出位置** — 任意文件夹（可已存在）；NFP 自动建时间戳子目录，Fenestra 直接产出单文件
5. **构建固件** — 后台运行；NFP 用时 1–3 分钟，Fenestra 数秒。完成后自动校验：
   - NFP：独立字节级比对（PASS 才算成功），支持「重新校验」
   - Fenestra：结果 SHA-256 与官方 r10 基线（`0a9cdd07…`）比对，一致即 PASS

> 版本规则 `NN.NN`、tag `小写字母+数字`（仅 NFP 方案）；`00.xx` 为上游保留，实机请避开。
> 不同功能组合建议用不同版本身份，便于机内区分。

### 刷机与机内使用

- **NFP 产物**：将 `Z_8_NNNN.bin` 拷到存储卡根目录，按尼康官方固件更新流程操作；功能直接出现在原生菜单。
- **Fenestra 产物**：先将 `Z_8_0311-cold-retry3.bin` 改名为 `Z_8_0311.bin` 刷入作为基线固件；再从 [Fenestra Release r10](https://github.com/PIPA12344/Fenestra-Z8-C311-source/releases/tag/wlm-2026.11.10-r10) 下载 `FENESTRA/` 文件夹（含 7 个 `.wlm` 插件：framing / prores / h265 / log_colour / photo_tools / card_wav / portrait_video）放到**启动卡**根目录，冷启动后机内出现 **Z8Mouldes** 菜单。

## 🔧 从源码运行（开发者）

要求：Python ≥ 3.10（本机无需 clang——推荐工具包走预编译模式）。

```sh
git clone https://github.com/xyl-g/Nikon-Pill.git
cd Nikon-Pill
python3 -m venv .venv && .venv/bin/pip install PySide6 capstone==5.0.7   # Windows: py -m venv .venv
./.venv/bin/python backend.py --toolkit ~/git/NikonFirmwarePatch-toolkit-v0.4.0   # 自测（--kind fenestra 可测另一套）
./.venv/bin/python main.py                                             # 启动 GUI
```

源码态没有内置工具包，界面上会出现「工具包…」按钮，指向任一工具包目录：

| 工具来源 | 获取方式 | 构建模式 |
|---|---|---|
| **NFP 完整 Release 工具包**（推荐） | 从 [NFP Releases](https://github.com/XinmiaoZhao/NikonFirmwarePatch/releases/tag/v0.4.0) 下载 `NikonFirmwarePatch-v0.4.0-toolkit.zip`（SHA-256 `82e82f18…`），按其 `SHA256SUMS.txt` 核对后解压 | 预编译（无需编译器） |
| NFP Git 克隆 | `git clone https://github.com/XinmiaoZhao/NikonFirmwarePatch` | 源码编译（**需要 clang**，且编译器版本须与上游审阅配方一致——上游锁定 Apple clang 21.0.0 字节；CLT 自动升级会导致 `Compiled symbols differ` 失败） |
| **Fenestra**（推荐直接下载） | 无需手动准备：构建脚本会自动获取；手动放置时只需 `patcher.py` + `host/cipher_patch.py` + `cold-retry3.fxpatch` 三个文件放在同一目录 | 纯标准库，任意平台 |

官方原厂固件 `Z_8_0311.bin` 须自行从 Nikon 下载（SHA-256 `d67413c2ede6b9ce8887175f20700bd9fbad2a973a4820968c45503d15d80d96`），见 [NFP README 的下载指引](https://github.com/XinmiaoZhao/NikonFirmwarePatch#获取原厂固件)。

## 🛠 打包 DMG / EXE

构建脚本会**自动 vendor 两套工具包**（首次运行时下载并做 SHA-256 校验，存入 `vendor/`；可用环境变量 `NFP_VENDOR_DIR` / `FENESTRA_VENDOR_DIR` 指向本地已有副本），自动生成 💊 图标，再调用 PyInstaller：

```sh
# macOS → dist/Nikon Pill.app + dist/NikonPill-<版本>-macOS.dmg
zsh build-macos.sh

# Windows → dist/NikonPill-<版本>-Windows.exe（one-file，无控制台窗口）
build-windows.bat
```

产物版本号自动取自 git tag（`v0.2.6` → `NikonPill-0.2.6-*`；无 tag 时回退 `0.0.0-dev`）。
或直接用 GitHub Actions：推 `v*` tag 自动构建双平台并发布 Release。

### 打包原理

- PyInstaller（`console=False`）打包 GUI；两套工具包作为数据放入 `nfp/` 与 `fenestra/`（vendor 时即裁掉引擎不读的 `luts/ docs/ emulation/ linux/ tests/` 等）；
- 引擎不单独打包：冻结应用**重入自身**（`--nfp-engine-patch` / `--nfp-engine-verify` / `--nfp-engine-fenestra`），以 `runpy` 执行内置引擎脚本（multiprocessing 自启动同款模式）——最终用户无需 Python 与编译器；
- Qt 裁剪：spec 里按目标路径过滤 `a.binaries`/`a.datas`，只保留 QtCore/Gui/Widgets（+DBus/Network/OpenGL 硬依赖）与中英翻译，安装包体积减 40%；
- Windows 子进程取消用 `taskkill /T /F`（杀整棵进程树）+ `CREATE_NO_WINDOW`；
- 图标由 `make_icon.py` 用 QPainter **手绘胶囊**（不依赖平台 emoji 字体——Segoe UI Emoji 的药丸是粉色的，会与 macOS 不一致）。

## 🧾 与上游的关系

- UI 通过 **subprocess** 调用上游引擎（`patch.py` / `verify.py` / `patcher.py`），不 import、不修改任何上游文件；
- 固件补丁的全部功劳属于两个上游：
  - [NikonFirmwarePatch](https://github.com/XinmiaoZhao/NikonFirmwarePatch)（四功能组：开放门 N-RAW、ProRes/H.265 品质、三 Log 色彩、RAW 预拍与像素偏移）
  - [Fenestra-Z8-C311-source](https://github.com/PIPA12344/Fenestra-Z8-C311-source)（cold-retry3 基线与 Z8Mouldes 模块运行时；其 5/7 WLM 功能亦移植自 NFP）
- 刷机请将输出 `.bin` 放入存储卡根目录，按尼康官方固件更新流程操作；**本工具只做离线构建，不与相机通信，实验固件风险自担**。

## 许可

遵循上游 NikonFirmwarePatch 与 Fenestra-Z8-C311-source 的许可条款；Nikon、Z8 为 Nikon Corporation 的商标，本项目与 Nikon 官方无关。
