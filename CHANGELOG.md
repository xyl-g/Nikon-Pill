# 更新日志 / Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号遵循语义化。

## [0.2.6] — 2026-11-10

### 变更
- 跟进上游：NikonFirmwarePatch **v0.4.0-rc.2 → v0.4.0 正式版**（递归 diff 确认引擎代码、
  9 个预编译单元与 resources/ 配方与 rc.2 完全一致，仅版本标注与链接差异）；
  toolkit ZIP 新 SHA-256 `82e82f18…`，全部 URL/默认路径/README 同步更新

## [0.2.5] — 2026-11-10

### 变更
- Fenestra vendor 轻量化：不再 `git clone` 整仓库，改为 3 个 HTTP 下载
  （`patcher.py` + `host/cipher_patch.py` 走 raw URL，`cold-retry3.fxpatch` 走 Release，
  共 ~350 KB，全部 SHA-256 校验）；CI 无需 git 历史，`vendor/` 极小

## [0.2.4] — 2026-11-10

### 修复
- **独立校验静默失败（自 0.2.0 起）**：`_engine_cmd` 把所有源码态引擎拼到 `patch.py`，
  verify 一直拿 patch.py 跑 verify.py 的参数（usage error），界面显示"校验未通过"；
  现按引擎角色路由脚本（冻结态本就正确）
- README 三张截图全部换新（NFP PASS / Fenestra 基线 / 错误面板），对应当前浅色界面

## [0.2.3] — 2026-11-10

### 修复
- 切换构建方案后不再需要重选固件：若已加载固件则自动重跑对应方案的校验
  （NFP 引擎 `--check` / Fenestra 内容哈希），构建按钮状态实时刷新
- 新窗口功能组卡片恢复默认全选（0.2.0 重构时丢失，被测试脚本掩盖）

## [0.2.2] — 2026-11-10

### 修复
- Windows 构建失败（exit 255）：cmd `if (…)` 括号块内 echo 文本含未转义 `)` 被
  当成块终结符；`.gitattributes` 强制 `*.bat` 为 CRLF

## [0.2.1] — 2026-11-10

### 变更
- Fenestra 模式下整块折叠功能组与版本/tag 区块（此前仅禁用，视觉噪音）
- 中央内容包入无边框 QScrollArea：窗口缩小时滚动而非挤压，消除回弹；
  最小窗口尺寸 780×560；细样式滚动条

## [0.2.0] — 2026-11-10

### 新增
- **双构建方案**：NikonFirmwarePatch（组合构建）与 **Fenestra Z8 cold-retry3**
  （官方基线一键应用）二选一；Fenestra 引擎为上游 `patcher.py`（纯标准库、跨平台）
- Fenestra 产物自动比对官方 r10 基线哈希（`0a9cdd07…`）
- 冻结引擎自调用新增 `--nfp-engine-fenestra`（runpy 执行内置 `fenestra/patcher.py`）
- 构建脚本自动 vendor Fenestra；spec 将其打为 `fenestra/` 数据

## [0.1.x] — 2026-11-07 ~ 11-08

### 0.1.3
- 应用图标改为 QPainter 手绘胶囊（红/黄 + 高光），跨平台像素一致——
  Windows CI 用 Segoe UI Emoji 渲染出粉色药丸的问题根除；窗口内 logo 同源

### 0.1.2
- 安装包瘦身 40%（DMG 52→32 MB）：spec 按目标路径裁剪 Qt
  （仅保留 Core/Gui/Widgets + 硬依赖 DBus/Network/OpenGL；翻译只留中英）；
  vendor 时裁掉工具包内引擎不读的 `luts/ docs/`

### 0.1.1
- DMG/EXE 产物名自动带版本号（取自 git tag）

### 0.1.0
- 首个双平台发布：PySide6 GUI（浅色卡片主题、💊 手绘 logo、就地错误面板、
  结构化日志、时间戳输出目录、构建后自动独立校验）
- 完整 NFP 工具包（9 个预编译单元）内置；冻结应用以
  `--nfp-engine-patch/--nfp-engine-verify` 重入自身跑引擎，用户免 Python 免编译器
- GitHub Actions 双平台构建，推 `v*` tag 自动发布 Release
