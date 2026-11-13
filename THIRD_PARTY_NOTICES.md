# 许可证与第三方说明

PageBridge 的原创程序代码以 **GNU Affero General Public License, version 3（AGPL-3.0-only）** 提供，完整条款见 [LICENSE](LICENSE)。可以按该许可证使用、修改和再分发；软件按原样提供，不附带担保。第三方代码、文献和其他资源继续适用各自的许可证，本项目的许可证不改变其权利归属。

## 随应用分发的软件

以下是随主应用分发的主要组件；主引擎 Python 依赖的版本、下载位置及校验值记录在 [engine/uv.lock](engine/uv.lock)，锁文件中的依赖全部随包。按需安装的 MinerU 环境使用独立的 [依赖版本清单](engine/mineru-requirements.txt) 和 [模型哈希清单](engine/mineru-models.json)，不属于主引擎锁文件。[运行时裁剪配置](scripts/bundle-profile.json) 只去掉 Python 运行时自带的 pip、包内测试和开发资源。

| 组件 | 锁定版本 | 许可证与来源 |
|---|---|---|
| PyMuPDF / MuPDF | 1.25.2 / 1.25.2 | 本构建使用开源 AGPL 版本；[PyMuPDF 源码与 COPYING](https://github.com/pymupdf/PyMuPDF/tree/1.25.2) · [官方许可说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) |
| CPython | 3.12.12 | PSF License Version 2 及所含第三方条款；[对应版本 LICENSE](https://github.com/python/cpython/blob/v3.12.12/LICENSE) · [源码](https://github.com/python/cpython/tree/v3.12.12) |
| Python standalone 构建 | 由 `uv` 管理的 CPython 3.12.12 运行时 | [python-build-standalone 构建项目](https://github.com/astral-sh/python-build-standalone)；其随附第三方库不因此改用 PageBridge 许可证 |
| uv | 0.9.17 | MIT 或 Apache-2.0；[对应版本源码](https://github.com/astral-sh/uv/tree/0.9.17)；两份完整许可位于 `engine/vendor/uv-LICENSE-*`，应用包内的引导工具为 `engine/tools/uv`（Windows 为 `uv.exe`） |
| Sparkle | 2.10.0 | 主体为 MIT，另含 bsdiff、sais-lite、Ed25519 等独立许可；[对应版本源码](https://github.com/sparkle-project/Sparkle/tree/2.10.0) · [完整许可证](https://github.com/sparkle-project/Sparkle/blob/2.10.0/LICENSE) |

PyMuPDF/MuPDF 及其他依赖的著作权归各自上游作者与贡献者所有。PyMuPDF/MuPDF 的商业许可代理为 Artifex Software, Inc.；此构建未声称持有商业许可。

应用包内的路径均相对于 `PageBridge.app/Contents/Resources/`：

- `LICENSE` 与 `Third-Party-Notices.txt`：PageBridge 完整许可证及本说明（源码根目录使用文件名 `THIRD_PARTY_NOTICES.md`）。
- `Sparkle-LICENSE.txt`：Sparkle 2.10.0 完整 MIT 许可、著作权名单与其外部组件声明；更新框架和辅助程序位于 `../Frameworks/Sparkle.framework/`。Swift Package Manager 按 `macOS/Package.resolved` 锁定版本并校验官方二进制归档。
- `engine/runtime/lib/python3.12/site-packages/`：保留的 Python 源文件、发行元数据、`*.dist-info/licenses/` 以及各组件包目录中的许可证。部分组件不使用 `dist-info/licenses` 布局。
- `engine/runtime/lib/python3.12/LICENSE.txt`：Python 的许可与历史说明。
- `engine/`：PageBridge worker 与阅读稿源文件、锁文件和运行时裁剪配置。

当前 PyMuPDF wheel 的发行元数据标明 AGPL，但其 `pymupdf-1.25.2.dist-info/COPYING` 只有许可证名称；随包根目录 `LICENSE` 提供完整 AGPL v3 条款。PyMuPDF 的对应版本原始 `COPYING` 与版权说明仍应连同其对应源码保留。

### Windows 组件

Windows 前端使用 .NET 10 与 Windows App SDK 的 WinUI／InteractiveExperiences 组件，不包含 Sparkle。直接包版本固定在 `windows/PageBridge.Windows/PageBridge.Windows.csproj`，传递依赖由同目录 `packages.lock.json` 锁定，实际解析的依赖与许可证随 Windows 包保存在 `resources/licenses/nuget/`；Microsoft 组件继续适用各自 NuGet 包中声明的许可，不因 PageBridge 的 AGPL 许可证改变。

Windows 私有 Python 路径为 `resources/engine/runtime/`，其 `Lib/site-packages/` 保留 Python 组件元数据与许可证；PageBridge 后端源码、完整许可证及第三方声明位于 `resources/`。Mac 和 Windows 包使用同一份项目源码和翻译引擎锁文件。框架来源见 [WinUI 源码](https://github.com/microsoft/microsoft-ui-xaml)、[Windows App SDK 源码](https://github.com/microsoft/WindowsAppSDK)及 [.NET runtime 源码](https://github.com/dotnet/runtime)。

## 对应源码与发行材料

每个版本配套的 `PageBridge-<版本>-source.zip` 包含 PageBridge 原创 Swift、C#、XAML 和 Python 源码、测试、构建脚本、资源、文档和两端依赖锁文件。它不包含开发机虚拟环境、用户文档、API Key、对话或验证输出，也不是所有第三方二进制的完整源码镜像。

配套的 `PageBridge-<版本>-third-party-sources.zip` 另外提供 PyMuPDF 1.25.2 的官方源码发行件和 MuPDF 1.25.2 官方源码归档。前者与锁文件中的大小、SHA-256 一致。MuPDF 归档包含构建文件和第三方源码树；清单记录了获取 URL、版本、许可证、计算所得哈希及检查边界，未执行所下载源码。

PyMuPDF 1.25.2 源码发行件未内嵌 MuPDF；其 [构建脚本](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.25.2/setup.py) 指定从 [MuPDF 1.25.2 源码归档](https://mupdf.com/downloads/archive/mupdf-1.25.2-source.tar.gz) 获取底层源码，配套第三方源码包已单独包含该归档。其他 Python 源码发行件可按锁文件中的 `sdist` URL 与 SHA-256 取得和核对；这份配套包是上述两项组件的源码集合，并非全部捆绑依赖的穷尽性源码与许可审计。

公开发布二进制时，应在同一下载位置提供与该包相匹配的 PageBridge 源码及上述第三方源码包，并保留上游版权、许可和其他所需对应源码的明确可复制来源。仅 wheel、锁文件或项目自有源码不能作为整个捆绑程序完整对应源码的证明。有关对应源码的范围及网络分发方式，以 [AGPL v3 第 1、6 节](LICENSE) 为准。项目仓库为 [txp666/PageBridge](https://github.com/txp666/PageBridge)，版本附件通过该仓库的 Releases 分发。

## 示例、图标及运行时资源

五份示例 PDF 是未修改的原始出版文献，合计 49 页；作者、DOI、来源、逐份许可及 SHA-256 见 [demos/README.md](demos/README.md) 和 [demos/catalog.json](demos/catalog.json)。文献不改用软件的 AGPL 许可证。机器译文是修改版本，应保留作者与原始来源归属，并明确标注机器翻译。

PageBridge 图标由本项目使用图像生成工具制作，原始 PNG、生成提示与 ICNS 转换方法见 [assets/README.md](assets/README.md)。开源许可、第三方声明与系统代码签名是不同的事项：Mac 包为 ad-hoc 签名、未经 Apple 公证，Windows 包没有 Authenticode 签名。它未使用外部产品标志。第三方名称与标志不表示这些作者或服务方为 PageBridge 背书。

语言模型与 Ollama 不包含在本安装包内。

## 导出 PDF 中的字体

导出的译文 PDF 和双语 PDF 嵌入 MuPDF 内置字体的子集，这些字体编译在随包的 PyMuPDF/MuPDF 库中，与 MuPDF 1.25.2 源码归档 `resources/fonts/` 下的文件相同：

| 字体 | 用途 | 许可证与著作权 | 源码归档中的完整许可 |
|---|---|---|---|
| Droid Sans Fallback（`droid/DroidSansFallback.ttf`） | 中日韩文字 | Apache-2.0；© 2005–2008 The Android Open Source Project | `droid/NOTICE` |
| Nimbus Sans（`urw/NimbusSans-*.cff`） | 拉丁文字 | SIL Open Font License 1.1；© 2016 (URW)++ Design & Development | `urw/OFL.txt` |
| Noto Sans Math（`noto/NotoSansMath-Regular.otf`） | 数学符号 | SIL Open Font License 1.1；© 2018 Google LLC | `noto/COPYING` |

该源码归档包含在上述第三方源码包中。

## 按需下载的对照阅读组件

对照阅读使用独立安装的 **MinerU 4.0.12 Basic / ONNX**。主应用仅携带接入源码、固定依赖清单、模型清单和 uv 安装工具；首次使用时把运行环境（CPython 来自 python-build-standalone，依赖来自 PyPI）和官方模型（来自 ModelScope 或 Hugging Face）下载到用户数据目录下的 `PageBridge/mineru/4.0.12/`，不写入应用包。该模式不使用 MinerU 云服务或远程 VLM；页面文字由用户配置的翻译服务处理。

MinerU 的[对应版本源码与许可](https://github.com/opendatalab/MinerU/tree/mineru-4.0.12-released)保留上游条款，其 LICENSE 含附加条件，不能简写为纯 Apache-2.0。所下载的依赖与模型仍分别适用各自许可证；固定安装版本见 `engine/mineru-requirements.txt`，Basic 模型文件及哈希见 `engine/mineru-models.json`。安装环境保留发行元数据和随附许可；MinerU 的完整附加条款位于该环境的 `mineru-4.0.12.dist-info/licenses/LICENSE.md`，可从对应版本源码查看同一许可。正式再分发模型或该运行环境前应单独核对其条款，PageBridge 不把这些资源重新授权为 AGPL。

## 在线服务

应用更新连接PageBridge 官网在 Cloudflare 上的 HTTPS 更新源：Mac 通过 Sparkle 检查签名的 appcast 与应用归档，Windows 检查签名的 `windows-update.json` 与安装器哈希。更新检查不上传本机 PDF、批注、翻译密钥或模型对话；Mac 的系统信息统计与静默安装均关闭，自动检查由使用者选择。更新签名与 Apple 公证、Windows Authenticode 是不同机制。

OpenAlex 提供在线文献元数据检索，Sci-Hub 和公开 PDF 来源提供可选的全文获取路径。它们是外部服务，不属于 PageBridge 源码或随包许可授权的内容；软件开源不授予对任意检索结果、远程文献或服务数据的额外权利。服务访问、收录、验证要求及可用性由相应提供方决定。

自定义 API 与本机模型由使用者配置。PageBridge 的开源许可不包括第三方 API 订阅、配额或语言模型权重许可。
