# Windows 版

Windows 界面使用 **WinUI 3**，与 Mac 版共用 MinerU 阅读稿与 PDF 导出引擎；文档库、凭据和后台任务由随包的 Python 服务（`desktop/`）处理。安装、更新和日常操作见[使用指南](https://github.com/txp666/PageBridge/blob/main/docs/USER_GUIDE.md)，完整电脑配置见 [README](https://github.com/txp666/PageBridge/blob/main/README.md#电脑配置)。本文只列 Windows 特有的内容。

## 运行环境

- **系统：** Windows 11 x64（未在 Windows 10 上测试）。Windows 11 ARM 通过系统 x64 仿真运行；锁定版本的 PyMuPDF 等依赖没有 Windows ARM64 wheel，因此没有原生 ARM64 包。
- **内存与磁盘：** 至少 8 GB 内存，建议 16 GB，本机 Ollama 模型另需内存。原页版式阅读模式首次使用会下载本地识别组件到 `%LOCALAPPDATA%\PageBridge\mineru\4.0.12`，请预留约 4 GB 磁盘空间。不需要独立显卡。
- **运行库：** .NET 10 和所需 WinUI 运行库随包提供，不需要另外安装。界面引用 `Microsoft.WindowsAppSDK.WinUI 2.3.9` 与 `Microsoft.WindowsAppSDK.InteractiveExperiences 2.1.9`，不包含 Windows App SDK 的 AI、ML、Search、Widgets 组件。
- **WebView2：** 应用内网站验证使用系统的 Microsoft Edge WebView2 Runtime；缺少时显示说明和系统浏览器入口。普通 PDF 阅读不需要 WebView2。
- **数据：** 文档、译文、批注和配置保存在 `%LOCALAPPDATA%\PageBridge`，API Key 保存在 Windows 凭据管理器；卸载程序不会删除它们。
- **签名：** 安装器和程序没有 Authenticode 签名，首次运行时系统可能提示风险，请核对下载来源和 SHA-256。
- 界面跟随系统深浅主题，PDF 页面保留原稿颜色，不反色。

## 与 Mac 版的差异

- **文字选择：** 阅读器以页面图像叠加 PDF 文字层显示，拖选限于单页。
- **尚未提供：** 跨页拖选、示例卡片（示例 PDF 为文件名下拉列表）、侧栏拖动调宽和部分编辑器快捷键。
- **文档助手：** 从当前文档已保存的问答记录恢复所选模型，尚未发送问题的模型切换不一定在重启后保留。`Ctrl+Enter` 发送。
- **在线文献：** “地址与检索配置”除 Sci-Hub 地址外，还可选填自己的 OpenAlex Key（提高查询额度），保存在 Windows 凭据管理器，与翻译服务的 Key 分开。
- **文内搜索：** 只搜索原文，译文视图也是如此；“结果”列出页码和摘要，命中过多时显示限量提示。关闭搜索清除高亮。
- **链接与文件：** PDF 内部链接跳到对应页，网页和邮件链接交给系统默认应用；文件右键和导出菜单可在资源管理器中定位原文或输出文件。
- **更新：** 只在手动“检查更新”时联网，不定期后台检查。
- **虚拟机中的 Ollama：** `localhost` 指虚拟机自身。请在虚拟机内运行 Ollama，或建立明确的本地端口转发。

| 快捷键 | 操作 |
| --- | --- |
| `Ctrl+O` | 导入 PDF |
| `Ctrl+F` | 文内搜索 |
| `Ctrl+,` | 翻译服务设置 |
| `Ctrl+Shift+E` | 导出译文 PDF |
| `Ctrl++` / `Ctrl+-` | 放大／缩小 |
| `Ctrl+C` | 复制选中的 PDF 文字 |

## 构建与测试

在 Windows x64 开发环境（或 Windows 11 ARM 的 x64 工具链）中准备：

- .NET 10 SDK 和 Windows SDK／WinUI 构建工具。
- Python 3 或 uv，用于安装锁定的 CPython 3.12.12 x64 引擎（`engine/setup-windows.ps1`）。
- 本仓库源码。从 Mac 复制时排除 `engine/.venv`、`engine/.tools`、`dist`、`validation` 和本机用户数据。

在仓库根目录运行 PowerShell：

```powershell
./scripts/build-windows.ps1
# 已有通过校验的开发引擎时跳过引擎安装：
./scripts/build-windows.ps1 -SkipEngineSetup
```

若执行策略禁止运行 `.ps1`，可只对本次进程使用 `powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/build-windows.ps1`。`-SkipInstaller` 不编译安装器，`-NoArchive` 只生成目录（同时跳过安装器）。

脚本发布 self-contained WinUI 应用，组装私有 Python、引擎（文件清单见 `scripts/bundle-profile.json` 的 `engineFiles`）和桌面服务，按同一配置裁掉 pip、包内测试和开发资源，并在组装后和移到最终目录后各运行一次隔离运行时检查。安装器由 `scripts/setup-inno.ps1` 下载的固定版本 Inno Setup 编译，下载会校验 SHA-256 和官方签名。

产物位于 `dist/`：`PageBridge-<版本>-windows-x64/` 目录、同名便携 ZIP、`PageBridge-<版本>-windows-x64-setup.exe` 及各自的 SHA-256；版本取自 `VERSION.json`，`WINDOWS_BUILD.json` 记录版本、build、架构和更新能力。已存在的旧产物会改名保留。构建目录继承父目录权限，请在普通用户会话中验收。Windows 刚退出的验证进程可能短暂占用 DLL，目录迁移最多重试 60 秒，仍失败时保留临时目录和日志。

测试：

```powershell
./engine/.venv/Scripts/python.exe -X utf8 -B -m unittest discover -s engine/tests -v
$env:PYTHONPATH = "$PWD/desktop"
./engine/.venv/Scripts/python.exe -X utf8 -B -m unittest discover -s desktop/tests -v
Remove-Item Env:PYTHONPATH
./engine/.venv/Scripts/python.exe -B -m unittest discover -s scripts -p 'test_windows*.py' -v
dotnet run --project windows/PageBridge.GeometryChecks
./engine/.venv/Scripts/python.exe scripts/verify-windows-engine.py `
  --engine dist/PageBridge-<版本>-windows-x64/resources/engine `
  --output validation/windows/packaged-engine.json
```

`-X utf8` 避免默认代码页影响中文日志。真实 Windows 凭据管理器测试需要已登录的交互式桌面会话：设置 `$env:PAGEBRIDGE_TEST_WINDOWS_CREDENTIALS = '1'` 后运行 desktop 测试，结束后删除该变量；SSH 或 CI 会话中这些测试会跳过。`scripts/test-windows-installer.ps1` 在 CI 中实际安装、检查并卸载安装器。

界面验收可加启动参数 `--reader-validation`（数据写入 `%LOCALAPPDATA%\PageBridge-WindowsReaderValidation`，独立单实例，可与正常窗口并行，并记录生命周期与选区诊断日志）或 `--ui-validation`（数据写入 `%LOCALAPPDATA%\PageBridge-WindowsUIValidation`）；`--dark`／`--light` 强制主题。只导入公开示例或专门生成的测试文档。

`.github/workflows/windows.yml` 在 Windows runner 上测试、打包、编译并实测安装器；标签发布流程见[开发指南](https://github.com/txp666/PageBridge/blob/main/docs/DEVELOPMENT.md#github-ci-与发布)。Windows 构建作业不接触更新签名私钥。

参考：[Windows App SDK 版本与支持周期](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/release-channels) · [Windows App SDK 部署概述](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/) · [第三方组件与许可](https://github.com/txp666/PageBridge/blob/main/THIRD_PARTY_NOTICES.md)（Windows 包内为 `resources/THIRD_PARTY_NOTICES.md`）
