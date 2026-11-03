# 验证记录

本文记录每次发布如何验证，以及当前版本的主要结果。各版本变更见 [CHANGELOG](../CHANGELOG.md)。

## 发布验证方法

**自动检查（每次 CI 与标签发布）：**

- Mac（`macos-15` arm64）：Swift、引擎、desktop 和发布脚本测试；构建应用后用 `verify-bundle.py` 检查原包和解压后的应用（版本、许可证、更新公钥、签名、私有运行时、引擎文件、示例）。
- Windows（`windows-2025`）：引擎、desktop 和打包测试；构建后在最终目录重新检查私有运行时；实际安装、逐文件核对、同 AppId 覆盖安装并卸载安装器，确认用户数据保留。
- 发布：核对标签、版本、build 和工作流身份；全部资产以草稿上传，核对大小与 SHA-256 后才公开；公开后再匿名下载全部资产，检查 SHA-256、HTTP Range、源码身份、Mac feed 与 Windows 更新清单签名。之后可用 `verify-release.yml` 独立复验（见[开发指南](DEVELOPMENT.md#github-ci-与发布)）。

**人工验收（每个版本，两端分别进行）：**

- 安装：从空程序目录全新安装；从上一公开版本升级；卸载后用户数据保留；空文档库首次启动。Windows 在普通用户的交互会话中进行。
- 应用内更新：上一公开版本通过 Sparkle（Mac）和“检查更新”（Windows）升级到新版；核对文档、文件夹、收藏、笔记、书签、阅读页码、服务配置和凭据均保留。
- 阅读模式首次使用：从不存在的运行环境目录安装 MinerU 并校验模型；中途取消后可重新完成。
- 真实翻译：用安装包内的 worker 翻译多种类型文档的选页，提交后重新打开，确认原 PDF 哈希不变。
- 界面：深浅主题、对照同步滚动与关闭同步、缩放、跳页、详情展开、单段重试、重启后恢复。
- 重排 PDF：引擎改动时用 `retranslate-pdfs.py` 与 `check-pdf-quality.py` 重新翻译并逐页复核受影响文档。

验收使用独立的测试文库和固定测试凭据，不使用用户文档或真实 API Key；失败、修复后的复测和最终通过分别记录。证据保存在本机 `validation/`，不进入 Git。

## 0.5.0 / build 14（当前版本）

2026-10-27 公开 [v0.5.0](https://github.com/txp666/PageBridge/releases/tag/v0.5.0)，源码提交 `edf1c4f702316c5bc4bc255a71d91a327cb49efc`。发布前 [Mac CI](https://github.com/txp666/PageBridge/actions/runs/40504943467) 与 [Windows CI](https://github.com/txp666/PageBridge/actions/runs/40504943537) 通过。[发布任务](https://github.com/txp666/PageBridge/actions/runs/40507257974)完成测试、构建、Windows 安装器实测、签名和公开，最后一步因 GitHub CDN 短暂返回旧 feed 而报失败；随后的[独立复验](https://github.com/txp666/PageBridge/actions/runs/40509507824)全部通过（12 项资产、5 个主文件的 HTTP Range、源码身份、两端更新签名）。

| 安装文件 | 字节 | SHA-256 |
|---|---:|---|
| `PageBridge-0.5.0-macOS-arm64.zip` | 240,386,761 | `6db90bca790571c3f152bfa58309bfc8f1cb7490600c02a49e7a7b6a8d4d10f8` |
| `PageBridge-0.5.0-windows-x64.zip` | 317,129,209 | `81c6ba60629ff3c304e3a1af34b178d39b9a1bed48ca8dbfdb733c2d93f9a73d` |
| `PageBridge-0.5.0-windows-x64-setup.exe` | 206,885,471 | `a9dd97d8d87c8b65dc9e51ec5f4bea0564417d67a7f0838a4bb1bfc1d73ef8eb` |

测试环境：Apple Silicon Mac；Windows 11 Pro ARM64（build 26200）虚拟机，以系统 x64 仿真运行 x64 应用。

- **Mac 应用内更新：** `/Applications/PageBridge.app` 从公开 0.4.0 经 Sparkle 检查、下载、安装并重启到 0.5.0；安装后的文件与公开包完全一致。文档库的原 PDF、批注、书签、服务配置和阅读位置全部保留。
- **Windows 应用内更新：** 公开 0.4.0 在界面中检查并下载 0.5.0 安装器，“安装并退出”后由安装向导原目录升级；载荷与公开包清单一致，测试文库、文件夹、收藏、笔记、书签、页码、服务配置和凭据管理器测试项全部保留。新版再次检查显示已是最新版本。
- **Windows 安装与首次启动：** 0.4.0 升级、正常卸载、从空程序目录和注册表重新安装均成功；空文档库首次启动、默认设置、通过系统文件选择器导入、跳页、退出后恢复页码均通过。
- **MinerU 首次安装：** 两端都从空目录安装运行环境，13 个模型和配置文件 SHA-256 全部通过；Mac 安装初期取消后可重新完成，无遗留进程。
- **真实翻译：** Windows 用安装包内的 worker 对六类文档（BERT 论文、Berkshire 财报、NASA 彩色幻灯片、W-9 表单、法语 INRS 手册、NASA 扫描稿）各翻译 1 页，171 个块全部完成；Mac 翻译 2 页，48 个块全部完成。全部重新打开，原 PDF 哈希不变。
- **排版回归：** 20 份原始 PDF 的 134 个物理页（6 份全文 87 页，其余 14 份选页 47 页）全部生成并重新打开，原 PDF 不变；其中 18 份完成，1 份有 1 个未完成公式段，1 份为不受支持的阿拉伯语扫描页。这是流程与排版检查，不是翻译准确率。
- **Windows 阅读界面：** 重启后重新打开六类阅读稿，两栏等宽、顶部对齐；双向同步、关闭与重新开启同步、150% 缩放、150 页文档末页跳转和长段详情开关后页码稳定。

已知问题：密集文字需要点击展开；少量公式、页脚的识别错误和专业术语误译；没有阿拉伯文文字层的阿拉伯语扫描页不受支持；阅读稿不能导出 PDF，也没有自身的全文搜索和批注。Mac 为 ad-hoc 签名、未经 Apple 公证，Windows 没有 Authenticode 签名。

本机证据：`validation/release-050-20261027/`。
