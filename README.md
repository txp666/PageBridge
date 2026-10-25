# PageBridge

<img src="assets/PageBridge-icon.png" alt="PageBridge icon" width="96">

**Mac 与 Windows 原生 PDF 阅读、翻译工作区。** 整理文档、保留排版翻译、原译文对照、批注和 AI 问答，也能搜索文献并打开全文。

适用于手册、报告、教材、合同和论文等带文本层的 PDF。Mac 使用 SwiftUI / AppKit / PDFKit，Windows 使用 WinUI 3；翻译基于 [PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next) / [BabelDOC](https://github.com/funstory-ai/BabelDOC)，支持本机 Ollama 和 OpenAI 兼容 API。

**0.4.0 / build 13** · [使用指南](docs/USER_GUIDE.md) · [版本记录](CHANGELOG.md) · [构建状态](https://github.com/txp666/PageBridge/actions)

## 下载与安装

下载 v0.4.0，或在 [Releases](https://github.com/txp666/PageBridge/releases) 中查看其他版本。

| 平台 | 安装包 | 安装方式 |
|---|---|---|
| Apple Silicon Mac · macOS 14+ | [macOS arm64 ZIP · 211 MiB](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-macOS-arm64.zip) · [SHA-256](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-macOS-arm64.zip.sha256) | 解压，将 `PageBridge.app` 放入“应用程序” |
| Windows 11 · x64（推荐） | [Windows 安装器 · 185 MiB](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-windows-x64-setup.exe) · [SHA-256](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-windows-x64-setup.exe.sha256) | 按安装向导选择目录和快捷方式，默认安装到当前用户目录 |
| Windows 11 · x64（便携） | [Windows 便携 ZIP · 282 MiB](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-windows-x64.zip) · [SHA-256](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-windows-x64.zip.sha256) | 完整解压，运行 `PageBridge.Windows.exe`，保留所有子目录 |

同一发行还提供[项目源码](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-source.zip)和[第三方对应源码](https://github.com/txp666/PageBridge/releases/download/v0.4.0/PageBridge-0.4.0-third-party-sources.zip)，各附 SHA-256。最终大小、哈希与发布状态以 [Release 页面](https://github.com/txp666/PageBridge/releases/tag/v0.4.0)为准。

Mac 包目前采用 ad-hoc 签名，**尚无 Developer ID 签名或 Apple 公证**；Windows 安装器及程序 **尚无 Authenticode 签名**。请核对来源和 SHA-256，按系统提示确认。尚无 Intel Mac 或原生 Windows ARM64 包；Windows ARM 虚拟机的 x64 兼容运行不等同于原生 ARM64 支持。

应用自带 Python 和翻译依赖，无需另外安装 Python；**不包含语言模型**。Ollama 和模型需单独安装，首次翻译可能下载字体和版面资源。Windows 应用内网页验证使用系统 WebView2 Runtime。

## 主要功能

- **文档库：** 文件夹、名称搜索、排序、收藏、最近阅读、回收站和首页预览。
- **翻译与阅读：** 整篇或指定页翻译、取消与失败保护，原文／对照／译文、连续翻页、同步滚动、文内搜索、缩略图、目录和书签。
- **批注：** 文字高亮、下划线、页笔记，编辑、删除，导出批注 PDF 和 Markdown。
- **文档助手：** 独立选择已配置模型；文档问答、当前页、选文或自由提问，引用片段核对、复制回答和保存笔记。两端均采用顶部文件名、中央对话、底部操作的紧凑布局。
- **文献获取：** OpenAlex 搜索，Sci-Hub／公开 PDF 全文来源，DOI 直接打开、下载校验与去重导入。
- **导出：** 原文、译文和交替页双语 PDF。Windows 功能与已知差异见 [Windows 说明](docs/WINDOWS.md)。

## 界面预览

![原文与译文对照](docs/images/reader.jpg)

![在线文献搜索](docs/images/literature-search.jpg)

以上是 Mac 0.3.0 的真实界面截图，展示公开文献；v0.4.0 的助手布局已调整。作者、许可及截图范围见[图片说明](docs/images/README.md)。

## 快速开始

1. 点击 `+` 导入 PDF；阅读和批注无需模型。也可从“搜索在线文献”按标题或 DOI 获取全文。
2. 打开侧栏设置，选择厂商、模型并填写 API Key；本机 Ollama 点击“获取模型”。可选预设厂商，也可填写自定义 OpenAI 兼容 Base URL 和模型 ID。
3. 点击“翻译”，选择语言和页码，例如 `1-5, 8`。失败或取消保留上次成功译文。
4. 切换“对照”阅读，选中文字添加批注，或打开助手提问。快捷问题会按当前模型与范围立即发送。
5. 用“导出 PDF”保存结果；带批注的版本从“阅读笔记”面板导出。

内置五份公开示例共 49 页，作者、来源与许可见[示例说明](demos/README.md)。更完整的配置、快捷键和排错见[使用指南](docs/USER_GUIDE.md)。

## 数据与更新

| 平台 | 本机数据 | API 密钥 | 更新 |
|---|---|---|---|
| Mac | `~/Library/Application Support/PageBridge/` | macOS 钥匙串 | Sparkle 检查更新，校验更新签名；由用户确认安装 |
| Windows | `%LOCALAPPDATA%\PageBridge` | Windows 凭据管理器 | “检查更新”校验签名与文件后，由用户下载并启动安装向导 |

Windows 安装器默认目录为 `%LOCALAPPDATA%\Programs\PageBridge`，卸载只移除程序与快捷方式，保留文档、译文、配置和系统凭据。替换程序保留独立用户数据。两端文档库格式不同，暂不支持直接互拷数据目录或自动跨平台同步。两端更新签名均不替代 Apple 公证或 Windows Authenticode。Windows 使用独立更新清单；便携版也可检查更新，但 OTA 会运行安装器，不会就地替换便携目录。

文档解析与排版在本机执行。使用云模型时，翻译文本或助手选取的片段、问题与有限近期对话会发送给所选服务；OpenAlex 接收检索词，所选全文来源接收 DOI 或下载请求。应用不携带云服务额度。

当前不提供通用 OCR。机器翻译和 AI 回答可能出错，公式、数字和关键条款需核对原文；助手每次仅使用有限片段，不代表通读全文。全文可用性取决于来源网站；遇到网站验证需自行完成，应用不自动解答验证码。

## 开发与贡献

```sh
git clone https://github.com/txp666/PageBridge.git
cd PageBridge
# Mac
bash engine/setup.sh
bash scripts/build-app.sh
# Windows PowerShell：./scripts/build-windows.ps1
```

详见[开发指南](docs/DEVELOPMENT.md)、[Windows 构建说明](docs/WINDOWS.md)、[验证记录](docs/VERIFICATION.md)、[贡献指南](CONTRIBUTING.md)和[安全说明](SECURITY.md)。本地检查与历史 CI 记录分别保留；本版发行状态和 CI 结果以对应 Release／Actions 记录为准。

原创代码采用 [AGPL-3.0-only](LICENSE)。第三方组件和示例保留各自许可及归属，见[第三方说明](THIRD_PARTY_NOTICES.md)。反馈问题请附版本、系统和脱敏复现，不要公开密钥、Cookie 或私有文档。
