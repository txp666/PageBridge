# PageBridge

<img src="assets/PageBridge-icon.png" alt="PageBridge icon" width="112">

原生 macOS PDF 翻译与对照阅读应用。使用 SwiftUI、AppKit 和 PDFKit 构建界面，通过 [PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next) / [BabelDOC](https://github.com/funstory-ai/BabelDOC) 保留 PDF 的主要版面结构，支持本机 Ollama 与自定义 OpenAI 兼容 API。

**当前源码：0.1.4 开发中。** 厂商选择、模型列表、示例库和新图标已加入源码；正文完整性、列表与链接修复正在进行真实文档验收。本轮要求原有两份文档与三篇公开论文共 112 页完整重译、复核通过后再打包。最终状态以 [验证记录](docs/VERIFICATION.md) 为准。

## 开始使用

1. 打开构建好的 `PageBridge.app`，进入左下角“翻译服务”。
2. 在线服务选择厂商和模型、粘贴 API Key；本机服务选择 Ollama，点击“获取模型”选择已下载的模型。
3. 拖入 PDF，或从“示例 PDF”选择论文，点击翻译。完成后对照阅读，再导出译文或双语 PDF。

应用随包提供 Python 与 PDF 翻译引擎；Ollama 和语言模型需单独安装。首次翻译可能下载上游字体和版面分析资源，资源备齐后，本机 Ollama 模式可离线使用。

详细操作、页码范围、导出和排错见 [使用指南](docs/USER_GUIDE.md)。

## 功能

| 功能 | 行为 |
|---|---|
| 原生文档库 | 拖放或选择 PDF；导入建立独立副本，保存历史记录 |
| 服务设置 | DeepSeek、阿里云百炼（北京）、硅基流动（中国站）、OpenAI、Ollama 与自定义 API；支持获取模型和手填模型 ID |
| 对照阅读 | 原文与译文并排，页码跳转、缩放、搜索、文字选择与同步滚动 |
| 翻译任务 | 显示真实阶段和进度，支持取消、重跑及指定页；一次处理一个任务 |
| 输出 | 译文与输入页数一致；双语按原文页、译文页交替排列 |
| 内容保护 | 校验公式占位符、原始公式对象、数值和技术标识；恢复失败时保留上次成功结果 |
| 版面与链接 | 按源文档证据恢复列表条目、脚标和链接热点；本轮真实译文验收状态见验证记录 |
| 示例库 | 内置五份公开论文，共 49 页，包含短文、图表、公式、双栏和长参考文献 |

API Key 保存于 macOS 钥匙串。常用厂商已配置地址；其他地域、网关或兼容服务使用“自定义 API”。“获取模型”只读取模型目录，“测试连接”只发送固定短句；保存设置、打开示例不会自动开始翻译。

## 示例 PDF

| 示例 | 页数 | 适合体验 |
|---|---:|---|
| [可复现计算研究十条建议](demos/pdfs/reproducible-research.pdf) | 4 | 短篇正文、分节与链接 |
| [科学图表设计十条建议](demos/pdfs/better-figures.pdf) | 7 | 图文混排与彩色图表 |
| [神经网络雪崩模型](demos/pdfs/avalanches.pdf) | 8 | 公式与双栏排版 |
| [风数据与自组织神经网络](demos/pdfs/wind-data-mining.pdf) | 11 | 数值表格、矩阵与参考文献 |
| [基因网络与神经网络](demos/pdfs/regnann.pdf) | 19 | 长论文、分段公式与图表 |

示例是未修改的原始出版 PDF。完整作者、DOI、许可与文件哈希见 [示例说明](demos/README.md) 和 [目录清单](demos/catalog.json)。示例可用不等于相应机器译文已通过验收。

## 开发与构建

当前构建面向 Apple Silicon Mac，声明部署目标 macOS 14+。macOS 14 和 Intel 尚未完成实机验证。需要 Xcode Command Line Tools / Swift 6；Python 及翻译依赖由锁文件管理。

在项目根目录执行：

```sh
bash engine/setup.sh
engine/.venv/bin/python -B -m unittest discover -s engine/tests -v

CLANG_MODULE_CACHE_PATH=/tmp/pagebridge-clang-cache \
SWIFT_MODULECACHE_PATH=/tmp/pagebridge-swift-cache \
swift test --package-path macOS --cache-path /tmp/pagebridge-spm-cache --disable-sandbox
```

完成 [真实 PDF 验收流程](docs/DEVELOPMENT.md#先验证-pdf再打包) 后构建：

```sh
bash scripts/build-app.sh
python3 scripts/verify-bundle.py
```

产物位于 `dist/PageBridge.app`，采用 ad-hoc 签名，尚未完成 Developer ID 签名与 Apple 公证。构建会生成 ICNS，加入示例、私有 Python 和必要依赖，并裁剪未使用的 Web UI、SDK、测试与开发资源。完整命令和最终包验证见 [开发指南](docs/DEVELOPMENT.md)。

## 数据与适用范围

导入副本、配置和结果位于 `~/Library/Application Support/PageBridge/`。每次翻译使用独立 attempt；页数、输入哈希和内容保护检查通过后才提交结果。失败或取消保留上次成功译文，导出禁止覆盖应用管理目录。

PDF 解析与排版在本机完成。使用在线 API 时，待翻译文本发送到所选服务；本机 Ollama 的推理在本机。密钥通过子进程 stdin 传递，不写入普通配置、命令行或任务日志。上游资源及历史缓存仍可能位于用户缓存目录，详见 [引擎说明](engine/README.md)。

需要带文本层的 PDF，暂不提供通用 OCR；加密文档需先解密。人名、机构、技术标识、变量、部分图像文字和有意保留的原始结构仍可能是英文。自动检查不能证明语义完全正确，重要数值、公式和技术结论应对照原文。

## 文档与项目结构

- [使用指南](docs/USER_GUIDE.md)：服务设置、导入、翻译、阅读、导出与排错。
- [开发指南](docs/DEVELOPMENT.md)：依赖、测试、真实模型验收、构建与版本管理。
- [架构与数据流](docs/ARCHITECTURE.md)：原生界面、worker、数据与提交边界。
- [验证记录](docs/VERIFICATION.md)：本轮完成状态、验收门槛和最终数据。
- [引擎说明](engine/README.md)：NDJSON 协议、内容保护、取消和缓存。
- [版本记录](CHANGELOG.md)、[第三方来源](docs/THIRD_PARTY.md)、[图标说明](assets/README.md)。

`macOS/` 是原生应用，`engine/` 是翻译运行时与测试，`scripts/` 是构建和质量工具，`assets/` 与 `demos/` 是随包资源。Git 排除私有 PDF、密钥、本地验证材料、生成译文、虚拟环境和应用包；公开示例及其归属说明纳入源码。浏览器原型不参与原生运行链路。

PDFMathTranslate-next 与 BabelDOC 使用 AGPL-3.0。依赖源码和许可元数据随本地包保留；本项目尚未另行声明覆盖全部原创源码的分发许可证。第三方许可、资源归属与对外发行事项见 [第三方来源](docs/THIRD_PARTY.md)。
