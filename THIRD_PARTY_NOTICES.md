# 许可证与第三方说明

PageBridge 0.3.0 的原创程序代码以 **GNU Affero General Public License, version 3（AGPL-3.0-only）** 提供，完整条款见 [LICENSE](LICENSE)。可以按该许可证使用、修改和再分发；软件按原样提供，不附带担保。第三方代码、文献和其他资源继续适用各自的许可证，本项目的许可证不改变其权利归属。

## 随应用分发的软件

以下是主要组件；全部 Python 依赖的版本、下载位置及校验值记录在 [engine/uv.lock](engine/uv.lock)。实际保留的组件受 [原生运行时裁剪配置](scripts/bundle-profile.json) 控制，不能把锁文件中所有开发或网页依赖视为已捆绑。

| 组件 | 本版锁定版本 | 许可证与来源 |
|---|---|---|
| PDFMathTranslate-next | 2.9.0 | AGPL-3.0；[对应版本源码](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/tree/v2.9.0) · [许可证](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/v2.9.0/LICENSE) |
| BabelDOC | 0.6.2 | AGPL-3.0；[对应版本源码](https://github.com/funstory-ai/BabelDOC/tree/v0.6.2) · [许可证](https://github.com/funstory-ai/BabelDOC/blob/v0.6.2/LICENSE) |
| PyMuPDF / MuPDF | 1.25.2 / 1.25.2 | 本构建使用开源 AGPL 版本；[PyMuPDF 源码与 COPYING](https://github.com/pymupdf/PyMuPDF/tree/1.25.2) · [官方许可说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) |
| CPython | 3.12.12 | PSF License Version 2 及所含第三方条款；[对应版本 LICENSE](https://github.com/python/cpython/blob/v3.12.12/LICENSE) · [源码](https://github.com/python/cpython/tree/v3.12.12) |
| Python standalone 构建 | 由 `uv` 管理的 CPython 3.12.12 运行时 | [python-build-standalone 构建项目](https://github.com/astral-sh/python-build-standalone)；其随附第三方库不因此改用 PageBridge 许可证 |
| ONNX Runtime | 1.31.0 | MIT；[对应版本源码](https://github.com/microsoft/onnxruntime/tree/v1.31.0) · [许可证](https://github.com/microsoft/onnxruntime/blob/v1.31.0/LICENSE)；另含第三方声明 |
| Sparkle | 2.10.0 | 主体为 MIT，另含 bsdiff、sais-lite、Ed25519 等独立许可；[对应版本源码](https://github.com/sparkle-project/Sparkle/tree/2.10.0) · [完整许可证](https://github.com/sparkle-project/Sparkle/blob/2.10.0/LICENSE) |

PDFMathTranslate-next、BabelDOC、PyMuPDF/MuPDF 及其他依赖的著作权归各自上游作者与贡献者所有。PyMuPDF/MuPDF 的商业许可代理为 Artifex Software, Inc.；此构建未声称持有商业许可。

应用包内的路径均相对于 `PageBridge.app/Contents/Resources/`：

- `LICENSE` 与 `Third-Party-Notices.txt`：PageBridge 完整许可证及本说明（源码根目录使用文件名 `THIRD_PARTY_NOTICES.md`）。
- `Sparkle-LICENSE.txt`：Sparkle 2.10.0 完整 MIT 许可、著作权名单与其外部组件声明；更新框架和辅助程序位于 `../Frameworks/Sparkle.framework/`。Swift Package Manager 按 `macOS/Package.resolved` 锁定版本并校验官方二进制归档。
- `engine/runtime/lib/python3.12/site-packages/`：保留的 Python 源文件、发行元数据、`*.dist-info/licenses/` 以及各组件包目录中的许可证。部分组件不使用 `dist-info/licenses` 布局。
- `engine/runtime/lib/python3.12/site-packages/onnxruntime/LICENSE` 和 `ThirdPartyNotices.txt`：ONNX Runtime 的完整许可及第三方声明。
- `engine/runtime/lib/python3.12/LICENSE.txt`：Python 的许可与历史说明。
- `engine/`：PageBridge worker、翻译与排版保护源文件、锁文件和运行时裁剪配置。

当前 PyMuPDF wheel 的发行元数据标明 AGPL，但未附单独的 `COPYING` 文件；随包根目录 `LICENSE` 提供完整 AGPL v3 条款。PyMuPDF 的对应版本原始 `COPYING` 与版权说明仍应连同其对应源码保留。

## 对应源码与发行材料

配套 `PageBridge-0.3.0-source.zip` 包含 PageBridge 原创 Swift/Python 源码、测试、构建脚本、资源、文档和依赖锁文件。它不包含开发机虚拟环境、用户文档、API Key、对话或验证输出，也不是所有第三方二进制的完整源码镜像。

配套 `PageBridge-0.3.0-third-party-sources.zip` 另外提供 PDFMathTranslate-next 2.9.0、BabelDOC 0.6.2、PyMuPDF 1.25.2 的官方源码发行件，以及 MuPDF 1.25.2 官方源码归档。前三项下载文件与锁文件中的大小、SHA-256 一致。MuPDF 归档包含构建文件和第三方源码树；清单记录了获取 URL、版本、许可证、计算所得哈希及检查边界，未执行所下载源码。

PyMuPDF 1.25.2 源码发行件未内嵌 MuPDF；其 [构建脚本](https://raw.githubusercontent.com/pymupdf/PyMuPDF/1.25.2/setup.py) 指定从 [MuPDF 1.25.2 源码归档](https://mupdf.com/downloads/archive/mupdf-1.25.2-source.tar.gz) 获取底层源码，配套第三方源码包已单独包含该归档。其他 Python 源码发行件可按锁文件中的 `sdist` URL 与 SHA-256 取得和核对；这份配套包是上述四项组件的源码集合，并非全部捆绑依赖的穷尽性源码与许可审计。

公开发布二进制时，应在同一下载位置提供与该包相匹配的 PageBridge 源码及上述第三方源码包，并保留上游版权、许可和其他所需对应源码的明确可复制来源。仅 wheel、锁文件或项目自有源码不能作为整个捆绑程序完整对应源码的证明。有关对应源码的范围及网络分发方式，以 [AGPL v3 第 1、6 节](LICENSE) 为准。项目仓库为 [txp666/PageBridge](https://github.com/txp666/PageBridge)，版本附件通过该仓库的 Releases 分发。

## 示例、图标及运行时资源

五份示例 PDF 是未修改的原始出版文献，合计 49 页；作者、DOI、来源、逐份许可及 SHA-256 见 [demos/README.md](demos/README.md) 和 [demos/catalog.json](demos/catalog.json)。文献不改用软件的 AGPL 许可证。机器译文是修改版本，应保留作者与原始来源归属，并明确标注机器翻译。

PageBridge 图标由本项目使用图像生成工具制作，原始 PNG、生成提示与 ICNS 转换方法见 [assets/README.md](assets/README.md)。它未使用外部产品标志。第三方名称与标志不表示这些作者或服务方为 PageBridge 背书。

首次翻译可能由上游下载字体、版面分析模型等资源；这些下载资源依各自随附许可使用，不因本项目开源而统一改用 AGPL。语言模型与 Ollama 不包含在本安装包内。

## 在线服务

应用更新通过 Sparkle 连接本项目 GitHub Releases 的 HTTPS 更新源，检查并验证签名后的更新信息与应用归档。更新检查不上传本机 PDF、批注、翻译密钥或模型对话；系统信息统计与静默自动安装均关闭，自动检查由使用者选择。Sparkle 的更新归档签名与 Apple Developer ID 签名、公证是不同机制。

OpenAlex 提供在线文献元数据检索，Sci-Hub 和公开 PDF 来源提供可选的全文获取路径。它们是外部服务，不属于 PageBridge 源码或随包许可授权的内容；软件开源不授予对任意检索结果、远程文献或服务数据的额外权利。服务访问、收录、验证要求及可用性由相应提供方决定。

自定义 API 与本机模型由使用者配置。PageBridge 的开源许可不包括第三方 API 订阅、配额或语言模型权重许可。
