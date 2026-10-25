# Windows 版

Windows 界面使用 **WinUI 3**，复用 Mac 版的 PDFMathTranslate-next / BabelDOC 翻译引擎与内容完整性检查。它是独立的 Windows 移植版本，不替换现有 Mac 应用。

界面锁定 `Microsoft.WindowsAppSDK.WinUI 2.3.9` 与 `Microsoft.WindowsAppSDK.InteractiveExperiences 2.1.9` 组件包（Windows App SDK 2.5.1 稳定发行所用的对应组件），其依赖提供 Foundation、AppWindow／Mica、构建工具和自包含 WinRT 启动。没有引用包含 AI、ML、Search、Widgets 的全量 Windows App SDK 元包，避免将未用组件加入安装目录。`.NET 10` 与所需 WinUI 运行库随包提供。

当前目标为 **Windows 11 x64**。Apple Silicon Mac 上的 Windows 11 ARM 虚拟机以系统 x64 兼容模式运行此包，不代表已提供原生 ARM64 引擎；锁定的 PyMuPDF 等依赖尚未提供对应 ARM64 wheel。v0.4.0 提供正式安装器和便携 ZIP；提供应用内检查、下载与安装确认，尚未提供 Authenticode 签名；Windows 使用独立签名更新清单，不使用 Mac 的 Sparkle 更新源。

## 功能与平台差异

界面按 Mac 版的文档库侧栏、文档卡片、阅读工具栏及页码栏布局移植。服务设置完整替换文档区域，保留文档库侧栏；设置内部同样采用“我的服务”列表、服务配置卡片、连接测试卡片和底部保存区。

提供文件导入与去重、文件夹／收藏／回收站、库卡片缩略图、页面／目录／书签导航、厂商和模型配置、整篇或指定页翻译、取消与失败保护、原文／对照／译文连续阅读、缩放、同步滚动、阅读页码保存，以及原文／译文／双语 PDF 导出。阅读器增加文字选择与复制、高亮／下划线、页笔记、批注编辑与导出，以及使用已配置模型的文档助手。服务配置支持预设或手动模型 ID、获取模型、连接测试、高级设置、重置和删除；切换配置前会确认是否放弃未保存的更改。

**阅读改进**：支持连续滚动跨页、文档库侧栏折叠／恢复、相邻页预取与快速切页；复用后台进程的持久 RPC，减少重复启动解释器。隔离 Windows 虚拟机已分别在深色与浅色启动模式验证这些操作、对照滚动及关闭时保存页码。主界面、设置和弹窗已完成深浅主题截图检查，页码数字与旁边文字的基线也已校正。

阅读器以页面图像叠加 PDF 文字层和批注显示，当前文字选择限于单页。现已接入文内搜索、PDF 页面内跳转及网页链接、OpenAlex／Sci-Hub 检索与全文导入。当前没有通用 OCR；目录来自 PDF 已有的大纲，不自动生成目录。导入 PDF 自带的批注会保留在页面及导出文件中，本版笔记面板只管理在 PageBridge 中新建的批注。仍未完全对齐的细节包括跨页拖选、示例目录卡片、侧栏拖动调宽和部分编辑器快捷键；两端的标题栏与系统文件选择器也遵循各自平台。

## 下载与使用

推荐使用 [PageBridge-0.4.0-windows-x64-setup.exe](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-windows-x64-setup.exe)（[SHA-256](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-windows-x64-setup.exe.sha256)）。同时提供 [便携 ZIP](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-windows-x64.zip)（[SHA-256](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-windows-x64.zip.sha256)）。版本列表见 [Releases](https://github.com/txp666/PageBridge/releases)；安装器和程序均未签名，可能触发系统安全提示。

1. **安装器：** 运行 Setup，按向导选择程序目录和快捷方式。默认安装到当前用户的 `%LOCALAPPDATA%\Programs\PageBridge`，无需管理员权限。默认创建开始菜单入口，桌面快捷方式可选且默认未选；安装结束可选择启动，静默安装不会自动启动。**便携 ZIP：** 完整解压到可写目录，保留所有子目录，运行 `PageBridge.Windows.exe`；不能只复制 EXE。
2. 程序自带 Python、.NET 和所需 WinUI 运行库，普通用户无需额外安装；语言模型不随包提供。首次翻译可能下载字体和版面资源。
3. 导入带文字层的 PDF，再配置本机 Ollama 或兼容 API。标题前的按钮折叠／恢复文档库，页面导航按钮展开“页面／目录／书签”；滚轮连续跨页，底栏可直接跳页。
4. 界面默认跟随系统深浅主题；PDF 白色页面保留原稿颜色，不反色。应用内网页验证另依赖系统 WebView2 Runtime。
5. 安装版与便携版均将数据写入 `%LOCALAPPDATA%\PageBridge`，密钥保存在 Windows 凭据管理器，不在安装目录中。两种程序不是独立文档库，不要同时打开并操作同一份用户数据。

### 升级与卸载

从 `+` 菜单或翻译服务设置的“检查更新…”手动检查。发现新版后由用户下载，可查看进度、取消；Ed25519 清单签名、文件长度和 SHA-256 均验证通过后，再确认启动安装向导并退出应用。翻译、助手或保存任务仍在运行时不能安装，未保存设置和助手草稿会确认。没有定时后台检查、自动下载或静默安装。

安装器使用固定应用标识识别升级，不强制结束正在运行的旧窗口；手动下载安装时请先退出旧版。便携版也可检查更新，但 OTA 下载的是 Setup，会按向导安装到已有 Inno 目录或默认本地程序目录，**不原地替换便携文件夹**。若保持便携使用，仍可下载完整新版 ZIP 并解压到新目录。Windows 使用独立的 `windows-update.json`；签名清单不等同 Authenticode，安装器仍可能显示系统安全提示。

安装器提供系统卸载入口。卸载只移除程序和快捷方式，**不会删除** `%LOCALAPPDATA%\PageBridge` 中的文档、译文、批注、对话或配置，也不会清除系统凭据。便携版退出后可直接移除程序目录，同样保留独立用户数据。不要将 Mac 的整个应用数据目录直接覆盖到 Windows。安装／升级／卸载的实际验证结果见[验证记录](VERIFICATION.md)，不能由安装脚本配置推断已经通过。

虚拟机中的 `localhost` 指虚拟机自身，不是 Mac 主机。使用 Ollama 时应在虚拟机中运行服务，或先建立明确的本地端口转发；不要把 Mac 的局域网 HTTP 地址当作受支持的远程 API 配置来绕过连接限制。API 密钥通过 Windows 凭据存储管理，不写入安装包或版本库。

### 在线文献与文件整理

点击侧栏“搜索在线文献”，或加号菜单中的同名入口。输入标题、关键词或 DOI，按 Enter 搜索；可筛选仅开放获取，并加载更多结果。全文来源可选 **Sci-Hub / 公开 PDF**，下载校验后自动加入文档库并打开，重复文件沿用已有记录。

- 已有 DOI 可直接打开全文，不必先完成 OpenAlex 搜索。
- “地址与检索配置”可以修改 Sci-Hub 地址；OpenAlex 支持匿名基础查询，也可选填自己的 Key 提高额度，密钥保存在 Windows 凭据存储中。
- 遇到网站验证时，在应用内打开来源页面，手动完成验证后继续。此面板依赖系统的 Microsoft Edge WebView2 Runtime，不把整套浏览器打进安装包；运行库不可用时显示说明和系统浏览器入口。
- 全文是否可用取决于站点收录、链接和网络。单文件上限 100 MB；下载器会验证文件确为可阅读 PDF。浏览器 Cookie 不转交给下载器，要求登录会话才能下载的文件仍可能无法直接导入。
- 右键文件夹可重命名、删除；删除文件夹只将文档移回“未分类”。在文件夹中导入会自动归入该文件夹，导入后清除名称搜索，避免新文件被筛选隐藏。

### 文内搜索与快捷键

点击标题栏放大镜或按 `Ctrl+F`，输入文字并搜索；“结果”列出命中的页码和摘要，点击定位，页面高亮匹配文字。原文与对照模式搜索原文，译文模式搜索译文；关闭搜索会清除命中高亮。大量命中时显示限量提示。

PDF 中的内部链接跳转到对应页，网页／邮件链接交给系统默认应用。文件右键和导出菜单可在资源管理器中定位原文或输出文件。

| 快捷键 | 操作 |
| --- | --- |
| `Ctrl+O` | 导入 PDF |
| `Ctrl+F` | 文内搜索 |
| `Ctrl+,` | 翻译服务设置 |
| `Ctrl+Shift+E` | 导出译文 |
| `Ctrl++` / `Ctrl+-` | 放大／缩小 |
| `Ctrl+C` | 复制 PDF 选文 |

### 页面导航与阅读批注

- **页面**：显示 PDF 页缩略图，当前页以蓝色边框标记；点击缩略图跳页。**目录**：显示 PDF 内置大纲及对应页码；没有大纲时显示说明。**书签**：列出当前原文或译文中的阅读书签，点击跳页。
- 在 PDF 页内拖动选择文字，使用工具栏或右键菜单添加高亮、下划线或笔记；右键菜单和 `Ctrl+C` 可复制选文。扫描件若没有文字层，需要先在其他工具中完成 OCR。
- 点击“阅读笔记”打开右侧面板，在“批注来源”切换原文／译文。可以为当前页添加笔记，也可以给高亮或下划线补充备注；点击条目的页码定位，使用编辑或删除按钮管理条目。页面上的便签按钮及批注区域双击也可打开编辑器。每条笔记最多 8,000 字符；保存失败时会保留编辑器中的输入。
- 工具栏或笔记面板的书签按钮可添加／移除当前页书签。原文和译文的批注分别保存；译文重新生成后，旧版批注不会直接套到新版坐标上。
- 面板底部的“导出批注 PDF”把当前来源的高亮、下划线和笔记写入一份新 PDF；应用阅读书签不会新增为该文件的 PDF 目录。“导出笔记”生成 Markdown，包含页码、选文、备注及书签条目。导出时选择应用数据目录之外的位置，不覆盖库中的原文或译文。

### 文档助手

在服务设置中添加并保存 Ollama 或 API 配置后，点击阅读工具栏的“助手”。顶部保留当前文件名和清空对话按钮，中间显示对话；模型、提问范围、快捷问题和输入操作集中在底部。助手可独立选择已配置的服务和模型，不改变翻译使用的服务。Windows 从当前文档已保存的问答记录恢复服务；尚未发送的模型切换可能不会跨重启保留。模型完整名称、API 发送目的地和范围说明可通过悬浮提示查看，引用片段仍可在回答中展开。快捷问题会按当前范围立即发送，也可输入自己的问题，点击“发送”或按 `Ctrl+Enter`。

| 提问范围 | 本次提供给模型的文档内容 |
| --- | --- |
| 文档问答 | 在本机检查原文最多 500 页、24 万字符，选取最多 6 页片段、合计 12,000 字符；模型没有通读全文。 |
| 当前页 | 当前阅读来源的一页片段，最多 2,400 字符。 |
| 选中文字 | 当前选文，最多 6,000 字符，不附带整页。 |
| 自由提问 | 不提取 PDF；仍会附带最近对话，其中可能已有文档内容。 |

问题最多 2,000 字符，各模式均会附带有限的最近对话。对话按文档保存在本机，最多保留最近 40 条，并受总大小限制；可以取消请求、清空当前文档对话、复制回答或保存到阅读笔记。回答中的可跳转页码与实际提供的片段关联，展开片段后可核对；未提供给模型的页码引用不会变成可跳转依据。AI 回答可能出错，公式、数字和条款应对照原文确认。更换模型不会自动清空对话，使用远程 API 时会将本次选取的内容及最近对话发送给所选服务。

## 构建

在 **Windows x64 开发环境**或 Windows 11 ARM 的 x64 兼容环境中准备：

- .NET 10 SDK 和当前 Windows SDK／WinUI 构建工具。
- Python 3 或 uv，用于引导安装锁定的 CPython 3.12.12 x64 引擎。
- 安装器构建通过 `scripts/setup-inno.ps1` 获取固定 Inno Setup，校验下载 SHA-256 与官方签名；不依赖 runner 中随时间变化的预装编译器。
- 本仓库源码；从 Mac 复制时排除 `engine/.venv`、`dist`、`validation` 和本机用户数据。

在仓库根目录运行 PowerShell：

```powershell
./scripts/build-windows.ps1
```

若开发机的默认策略禁止运行 `.ps1`，可仅对此次构建进程执行 `powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/build-windows.ps1`；不需要修改系统或用户级执行策略。

再次构建可复用已安装且验证过的引擎：

```powershell
./scripts/build-windows.ps1 -SkipEngineSetup
```

脚本先发布 unpackaged、self-contained WinUI 应用，检查应用 PRI 与编译 XAML（XBF）资源齐全，再组装私有 Python、引擎与桌面服务模块。运行时裁剪去掉未使用的 Web UI、厂商 SDK、测试与开发资源，保留所需 DLL、运行数据和许可证；Windows 保留 `tzdata`。组装后先执行隔离导入与 worker health，移动到最终目录后再验证一次，最后生成 ZIP 和 SHA-256 文件。

运行时构建目录继承父目录的访问权限，避免管理员构建的文件无法在普通权限桌面会话执行；验收也应使用普通用户会话。Windows 在验证进程刚退出时可能短暂保留 DLL 占用。目录迁移对此类错误最多重试 15 秒；若仍失败，保留临时目录和日志以供诊断，不删除源引擎或改动系统防护设置。

产物包括 `dist/PageBridge-0.4.0-windows-x64/`、同名便携 ZIP，以及 `PageBridge-0.4.0-windows-x64-setup.exe` 和各自 SHA-256。版本号来自 `VERSION.json`，`WINDOWS_BUILD.json` 记录版本、build、`stable` 通道、架构与签名／更新能力。重复构建先保留旧目录；正式发行资产不可覆盖，修复应增加版本和 build。

## 测试与 CI

```powershell
./engine/.venv/Scripts/python.exe -B -m unittest discover -s engine/tests -v
$env:PYTHONPATH = "$PWD/desktop"
./engine/.venv/Scripts/python.exe -B -m unittest discover -s desktop/tests -v
Remove-Item Env:PYTHONPATH
./engine/.venv/Scripts/python.exe scripts/verify-windows-engine.py `
  --engine dist/PageBridge-0.4.0-windows-x64/resources/engine `
  --output validation/windows/packaged-engine.json
```

Windows 常规工作流在 Windows runner 上测试和打包，并上传 Actions artifact；推送版本标签后，联合发布 CI 会在 Windows runner 上直接用 Inno Setup 编译安装器，再将安装器、便携 ZIP 与 Mac、项目源码、第三方源码一起核验和发布。Windows 构建作业不读取 OTA 签名私钥；联合发布的签名作业生成独立 `windows-update.json`，客户端只持有公钥。日志和隔离引擎验证报告保存在 artifact 中；工作流定义不代表某次运行已经通过。

Windows 凭据管理器的真实集成测试需要已登录的交互式桌面会话。CI 或 SSH 的普通后台会话使用测试专用内存凭据替身；这不会改变应用生产环境的系统凭据存储。在已登录桌面的 PowerShell 中，可设置 `$env:PAGEBRIDGE_TEST_WINDOWS_CREDENTIALS = '1'` 后运行上述 desktop 测试，结束后用 `Remove-Item Env:PAGEBRIDGE_TEST_WINDOWS_CREDENTIALS` 清除本次测试开关。不要把 SSH 的“没有凭据登录会话”误判为生产环境已通过凭据测试。

阅读器 UI 验证使用固定参数 `--reader-validation`，将数据写入 `%LOCALAPPDATA%\PageBridge-WindowsReaderValidation` 并使用独立单实例标识，可以与普通应用并行运行。只导入公开示例或专门生成的测试文档，不复制用户文档库。分别加 `--dark` 或 `--light` 可在启动时验证整套应用主题；不加主题参数时跟随系统。切换这两个验证参数应先关闭上一份 ReaderValidation 窗口，不能关闭用户正常窗口。仅该验证模式写入按进程区分的生命周期和选区诊断日志，记录启动、关闭、异常类型／栈及鼠标事件、页序号和文字层数量，不写入请求、密钥或文档内容，也不吞掉异常。

发布前本地候选的桌面后端共 119 项测试：Mac 116 项通过、3 项平台相关检查跳过；Windows VM 112 项通过、7 项平台或交互凭据检查跳过。最终候选包完成两种主题各 11 项 GUI 冒烟，覆盖紧凑助手、实际 PDF 渲染、文内搜索、侧栏和包内私有 Python；先前的真实 OpenAlex／公开 PDF 导入、链接、文件夹及完整阅读回归以各自构建哈希保留。这些是本地候选结果，不是 v0.4.0 标签 CI 或公开包已经通过的声明。完整证据范围见[验证记录](VERIFICATION.md)。

Windows 测试命令可增加 Python 的 `-X utf8` 参数，以免默认代码页影响中文日志。真实凭据与真实模型调用需单独记录，不能从后台测试或合成 UI 对话推断。

自动测试和构建成功不等同于界面或真实翻译验收。发布前需在 Windows 检查中文路径导入、窗口缩放、对照滚动、服务凭据、真实短文翻译、取消后无子进程残留、失败保留旧结果，以及导出文件的页面、文本、公式和链接。新增阅读流程还需检查目录／书签跳页、原译文选区及旋转页批注位置、笔记新增／编辑／删除与重启后读取、两种批注导出、助手模型切换及请求取消、引用片段定位，以及深浅主题下侧栏和编辑器布局。WinUI 界面与 Mac 功能是否对齐，以实际交付和验收记录为准。

## 技术来源

- [Windows App SDK 稳定版本及支持周期](https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/release-channels)
- [Windows App SDK 部署概述](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/)
- [第三方组件与许可证](THIRD_PARTY.md)

Windows 引擎与 Mac 引擎适用同一 AGPL-3.0 项目许可证与第三方对应源码义务；正式发布不表示已完成代码签名、所有硬件平台验收或逐字节可重现构建。
