# PageBridge — Mac 本地 PDF 翻译应用设计

日期：2026-10-23。阶段：方案 1 的原生 Mac 开发构建已完成，接入真实 PDFMathTranslate-next、自定义 API 与 Ollama，并完成本机导入、翻译、对照和导出验证。

当前实现与边界以 [README](README.md) 和 [原生验收](validation/README.md) 为准。前期 [浏览器原型](prototype/README.md) 独立保留，其中翻译为模拟；原生应用使用真实引擎。本文是完整 V1 设计目标，不代表全部可靠性、分发和质量验收已完成。

本轮最小闭环采用原子 JSON 保存元数据，而非 SQLite；一次执行一个任务。Keychain、独立 worker、真实进度、取消、输出校验、PDFKit 和私有运行时打包已实现。队列、资源管理界面、Developer ID 签名/公证、干净 Mac 与复杂文档回归仍未完成。

## 1. 产品定位

面向阅读外文论文、产品手册和技术资料的 Mac 用户。将 PDF 导入、翻译服务配置、任务进度、原译文对照和文件导出集中在一个原生桌面应用中。重点是尽量保持文字、公式和图表的布局，同时让用户清楚知道文档如何处理、文本发送到哪里。

应用名暂定 PageBridge。建议首发目标为 macOS 14+、Apple Silicon，通过 DMG 分发；这是设计选择，最低系统版本和 Intel 支持须由后续依赖及打包验证确定。

不需要 PageBridge 账户，不设置自有翻译中转服务器。用户使用自己的 API 服务，或者连接本机 Ollama。

| 工作方式 | 本机处理 | 网络行为 | 对用户的表述 |
| --- | --- | --- | --- |
| 自定义 API | PDF 解析、版面处理、结果生成和保存 | 待翻译正文片段、必要上下文及提示词发送至所选服务；不以整份 PDF 上传为默认流程 | 本地处理，使用在线翻译服务 |
| 本机 Ollama | PDF 处理及模型推理 | 翻译仅连接回环地址；资源齐备后应无外网依赖 | 完全本地翻译 |
| 远程模型服务 | PDF 处理在本机，推理在远端 | 文本发送至明确显示的远端地址 | 远程服务，不能标为完全离线 |

离线模式必须先备齐布局模型、字体、CMap、tokenizer 和用户选择的语言模型。资源下载与文档翻译分开呈现；缺少资源时不偷偷联网或改用云服务。

## 2. 首版范围

| 模块 | V1 行为 |
| --- | --- |
| 导入 | 拖入 PDF、文件选择、一次添加多份；默认串行处理 |
| 文档检查 | 页数、文本层、损坏/加密状态、页码范围、磁盘空间检查；不覆盖原件 |
| 翻译设置 | 源语言、目标语言、全部或指定页；默认英语到简体中文 |
| 翻译服务 | OpenAI 兼容 API、本机 Ollama；允许多个命名配置 |
| 输出 | 译文 PDF、双语 PDF；具体双语编排方式以锁定引擎验证结果为准 |
| 阅读 | 原文、译文、左右对照；缩放、页码跳转、搜索、按对应页同步浏览 |
| 任务 | 分阶段进度、取消、失败后重新运行、串行队列、历史记录 |
| 存储 | 在 Finder 中显示、另存为、移除记录/删除本地副本、清理翻译缓存 |
| 资源 | 组件版本、准备状态、下载与离线包导入、存储占用 |

V1 以可选取文本的 PDF 为主要支持对象。纯图片扫描件检测后提示暂不支持；已有 OCR 文本层的扫描件列为实验场景。扫描检测是启发式判断，也允许用户查看原文并核对结果。

暂不纳入 V1：通用 OCR、手写识别、译文逐段编辑后重排、聊天问答、云同步、自带大型语言模型、原地暂停与精确断点恢复。API 配置限于经过验证的兼容协议，不承诺任意 HTTP API 都能接入。

## 3. 使用流程与页面

主流程：添加 PDF → 检查文件 → 选择语言及服务 → 翻译 → 对照阅读 → 导出。

首次使用时，先显示资源是否就绪；用户选择在线 API 或本机模型，完成配置后即可导入文件。以后直接进入文档库或上次阅读位置。

### 文档与阅读窗口

侧栏列最近文档及任务状态，主区显示 PDF。翻译前仅显示原文和设置入口；运行中显示原文及真实阶段；完成并验证输出后开放译文与对照模式。工具栏保留导出、阅读模式、缩放和页码等必要操作。

左右对照按输入页与输出页的映射同步，不能直接假设两侧滚动偏移相等。部分页面翻译、双语输出或重排可能改变页数；先验证页映射，再承诺同步效果。界面内左右对照与导出的双语 PDF 是两个独立功能。

建议默认最小窗口 1100 × 720；空间不足时切换单页或收起侧栏。支持系统深浅色、键盘导航、VoiceOver 标签、可见焦点；任务状态同时用文字呈现，不只依赖颜色。常用快捷键：⌘O 导入、⌘F 搜索、⌘, 打开设置。

### 翻译服务设置

| 字段/操作 | 规则 |
| --- | --- |
| 配置名称 | 用户可自定义，例如“工作 API”“本机模型” |
| 协议 | V1 为 OpenAI 兼容或 Ollama |
| Base URL | 显示实际主机和完整地址；示例 `https://api.example.com/v1`，该地址不可用 |
| API Key | 遮蔽显示、存 macOS Keychain；配置文件和数据库只存引用，不存密钥 |
| 模型 ID | 可手动输入；服务不支持模型列表时仍能配置 |
| 测试连接 | 发固定短句验证实际翻译响应，不发送用户 PDF；提示测试可能产生少量服务费用 |
| 高级设置 | 超时、请求并发和有限重试；默认保守，参数随任务保存快照 |

远程服务默认 HTTPS；HTTP 回环地址用于本机模型。远程 HTTP 若后续开放，需要明确显示传输风险。发现用户误填完整 `/chat/completions` 地址时给出修正预览，不能静默改为其他域名。跨主机重定向不得携带密钥。

连接测试区分：地址或路径错误、密钥无效、模型不存在、限流、超时、证书问题、返回格式不兼容。获取模型列表成功不等于翻译能力验证成功。测试通过时保存时间和对应配置指纹，配置变化后标记需要重测。

设置页面修改只影响新任务；运行中的任务继续使用创建时的配置。界面在开始翻译前显示实际服务主机，避免误用远程服务。

### 任务状态与失败处理

正常路径为：等待 → 检查 → 解析 → 翻译 → 排版 → 验证输出 → 完成。另有正在取消、已取消、失败、已中断等状态。

阶段名称来自引擎并映射为中文；只有上游有可靠总量时显示百分比。不能将段落计数或分片计数冒充已完成页数。上游显示 100% 后仍须验证并提交文件，才标为完成。

| 场景 | 界面与系统行为 |
| --- | --- |
| 未配置 API / 资源缺失 | 给出配置或资源准备入口，不启动任务 |
| 401/403 | 停止并提示检查凭据，不重复重试 |
| 429 / 暂时网络故障 | 有限退避重试，显示重试状态，允许取消；遵守服务返回的等待时间 |
| 取消 | 立即显示“正在取消”，待进程回收后显示“已取消” |
| worker 崩溃 / App 重启 | 标记“已中断”，保留可诊断信息，允许重新运行 |
| 输出损坏 / 空间不足 | 标记失败，保留原件和以前成功结果，不暴露为正式译文 |
| 输出可用但有警告 | 显示可核实的警告及相关页；没有质量检测依据时不虚构质量评分 |

已发送的 API 请求不能撤回，取消后可能仍产生服务费用。V1 的重新运行可利用有效翻译缓存，但不叫“从中断位置继续”。

## 4. 技术架构

建议结构：原生 SwiftUI/AppKit 界面与 PDFKit 阅读器 → 本机任务管理器 → 独立 Python worker → pdf2zh-next 公开入口 → BabelDOC → 所选翻译服务。

| 层 | 责任与选择理由 |
| --- | --- |
| SwiftUI + AppKit | 原生窗口、菜单、文件导入导出、任务列表及设置；PDFView 通过原生视图桥接 |
| PDFKit | 原文/结果预览、页码、搜索和缩放；不承担翻译重排 |
| JobManager | 队列、状态机、文件归属、重试、取消、进程生命周期 |
| Python worker | 固定 Python 运行时、配置适配、进度转换、结果检查；一个活动任务一个 worker |
| pdf2zh-next | 使用 `pdf2zh_next.high_level.do_translate_async_stream(settings, file)`；不要直接耦合 BabelDOC 内部 API |
| SQLite + 本地文件 | 任务/attempt/服务配置元数据与 manifest；文档、结果、缓存各自存储 |
| Keychain | 按服务配置存储密钥，启动任务时按需读取 |

独立 worker 通过匿名管道通信，无需对外监听 HTTP 端口。stdin 接收任务和控制消息，stdout 只输出版本化 NDJSON，stderr 只输出脱敏日志。不得经命令行参数或落盘 TOML 传递 API Key；密钥通过私有管道进入内存，避免持久化到上游 GUI 配置。

协议至少包括 `protocolVersion`、`jobId`、`attemptId`、递增 `sequence`、事件类型、阶段、进度、警告和终态。启动先握手；协议不兼容立即报错。对异常 JSON、超大消息、子进程退出和重复终态有明确处理。

上游 `finish.translate_result` 实际为 Python 对象，桥接层显式提取输出路径和耗时。既处理 `error` 事件，也捕获流之后抛出的异常。前端只消费 PageBridge 自己定义的事件，不依赖上游对象序列化细节。

取消时先停止调度新请求并调用协作取消，再限时回收整个进程组；超时强制终止后清理临时输出。完成与取消由串行状态机裁决：已接受取消后不能被迟到完成事件改成成功，已提交成功后取消是无操作。

每个任务固定输入副本及哈希、语言、页码、服务地址、模型、参数、引擎版本和资源版本。每次重新运行创建新的 attempt，忽略旧 attempt 的迟到事件。缓存有效性包括模型、目标语言、提示词等影响译文的配置。

## 5. 数据和输出完整性

建议存储位置为 `~/Library/Application Support/PageBridge/`，下分数据库、任务输入、attempt 临时输出、已完成结果、缓存和资源。具体路径在实现时由系统 API 获取。

所有输出先写任务临时目录。验证 PDF 可打开、所需文件齐备、页数符合所选输出模式，并保存 manifest 后，使用同一文件系统内的原子提交发布结果。退出码 0、进度 100% 或文件存在均不足以认定成功。

导出使用系统保存对话框；同名文件有替换/另存选择，原始 PDF 永不被任务覆盖。取消导出不删除已完成结果。移除历史记录、删除结果、清空缓存是不同操作，界面注明所影响的文件。

文档和翻译缓存默认属于用户本机数据，普通文件存储不等于数据库已加密。日志默认不含正文、完整提示词、密钥或带凭据 URL；诊断导出也须脱敏。不默认启用遥测或自动上传文档。

## 6. 上游核验与实现风险

核验日期为 2026-10-23。本节保留前期依赖风险判断；本轮已安装并验证 pdf2zh-next 2.9.0 / BabelDOC 0.6.2 / PyMuPDF 1.25.2 / bitstring 4.3.0，完整锁文件见 `engine/uv.lock`。

- 当前主上游是 [PDFMathTranslate-next/PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next)。`PDFMathTranslate/PDFMathTranslate-next` 是另一个 fork，不能混用其版本与文档。主上游 main 的 pyproject 标注 2.9.0，要求 Python `>=3.10,<3.14`。POC 建议固定 Python 3.12，但最终取决于依赖实测。
- 官方异步入口提供阶段、进度、错误、完成和取消机制；BabelDOC 的 README 推荐通过该入口集成。[API 文档](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/main/docs/en/advanced/API/python.md)、[BabelDOC Python API](https://github.com/funstory-ai/BabelDOC#python-api)
- 自定义 API 的 Base URL、Key、Model 已有上游配置支持；Ollama 有独立适配。[配置源码](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/main/pdf2zh_next/config/translate_engine_model.py)
- 当前 main 依赖约束存在具体冲突：pdf2zh-next 要求 `pymupdf<1.25.3`，BabelDOC main（标注 0.6.4）要求 `pymupdf>=1.26.7`。这说明不能直接组合两个 main；不意味着已证明所有历史发行组合都不能用。第一项 POC 必须找到可解析、可安装、能翻译的锁定组合，不能用忽略依赖检查掩盖冲突。[pdf2zh-next pyproject](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/main/pyproject.toml)、[BabelDOC pyproject](https://github.com/funstory-ai/BabelDOC/blob/main/pyproject.toml)
- `ocr-workaround` 不是通用 OCR 能力。首版扫描件边界必须在导入检查和宣传中一致。[BabelDOC 选项](https://github.com/funstory-ai/BabelDOC#pdf-processing-options)
- 资源支持离线管理，但是否已齐备必须由本机校验判断。[离线资源说明](https://github.com/funstory-ai/BabelDOC#offline-assets-management)
- 上游项目标注 AGPL-3.0。建议按兼容的开源分发路线准备依赖和资源许可清单；若目标是闭源发行，需先完成具体许可评估。独立进程是工程边界，不能据此宣称免除许可义务。[PDFMathTranslate-next LICENSE](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/main/LICENSE)、[BabelDOC LICENSE](https://github.com/funstory-ai/BabelDOC/blob/main/LICENSE)

PDFKit 阅读与 Keychain 存密钥有平台 API 支持。[Apple PDFView](https://developer.apple.com/documentation/pdfkit/pdfview)、[Apple Keychain](https://developer.apple.com/documentation/security/keychain-services)

## 7. 打包与交付计划

正式目标是用户拖入 Applications 即可使用，不依赖预装 Python、uv 或 Homebrew。开发验证阶段可使用 uv 隔离环境；正式版随应用提供锁定运行时和依赖。资源包按版本管理并校验完整性；语言模型由用户准备，暂不随安装包捆绑。

第一轮技术样机就验证 Python 子进程、ONNX/原生扩展与动态库的架构、签名、Hardened Runtime、公证和干净环境启动。当前本地 .app 已嵌入 Python 和引擎并做 ad-hoc 签名；尚未进行 Developer ID 签名、公证或公开发行。Mac App Store 和 Intel 构建另设验证，不假定原生壳可编译就代表整个引擎可以分发。[Apple 分发签名](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac)、[Apple 公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

| 阶段 | 必须交付的证据 |
| --- | --- |
| 技术可行性 | 锁定依赖组合；公开样本真实译文及双语输出；API 与 Ollama 各跑通；基础原生包可启动 |
| 最小闭环 | 导入、配置、真实进度、取消、输出校验、PDFKit 预览和导出 |
| 可靠性 | 串行队列、崩溃恢复、限流处理、历史/缓存管理、配置快照、离线资源检查 |
| 可分发版本 | 签名公证、无开发环境 Mac 验证、许可清单、回归样本报告 |

先验证引擎兼容性与打包，再按通过的验证结果估算开发工期和包体积；不预先承诺每页速度或 GPU 加速收益。

## 8. 验收条件

1. 用单栏、双栏、公式、表格、中英文混排、长译文和特殊字体等样本逐页检查。对公式、数值、单位和图表做人工核对，记录缺陷，不仅比较页数。
2. API Key 不出现在数据库、配置、命令行、日志或诊断包；测试连接不使用用户文档。
3. 错误密钥、429、断网、超时、worker 崩溃、磁盘不足和取消/完成竞态均不会显示伪成功或损坏的正式结果。
4. 重跑不会收到旧任务事件；服务配置修改不会影响运行中的任务；源文件始终保持不变。
5. 使用可用本机模型和完整资源包断网翻译成功；网络观测确认未向外部服务发送翻译内容。
6. 原文与译文的页映射在全部/部分页和选定双语模式下正确；未验证映射时降级为独立浏览并说明。
7. 无开发环境的目标 Mac 可以打开、导入、翻译和导出，签名、公证与文件访问正常。

## 9. 界面原型

以下三图按聊天中的实际展示顺序编号，均由内置 ImageGen 生成。用户已选择方案 1，并据此完成可交互原型；这些参考图本身不是实际引擎输出。

- [界面原型 1](docs/concepts/concept-1.png)
- [界面原型 2](docs/concepts/concept-2.png)
- [界面原型 3](docs/concepts/concept-3.png)

图中的文档、公式、图表、文件大小和进度都是模拟内容，不作为工程资料或翻译质量证据。尤其不能根据草图承诺正在运行时就能预览尚未生成的译文，也不能把模拟的“页进度”直接映射为上游实际段落进度。真实交互以本文状态和数据契约为准。

图像生成提示词保存于 [prompts.md](docs/concepts/prompts.md)。可交互原型的功能边界、运行方式及验证记录分别见 [使用说明](prototype/README.md) 和 [设计验收](prototype/design-qa.md)。
