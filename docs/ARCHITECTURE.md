# 架构与数据流

## 概览

| 层 | Mac | Windows |
|---|---|---|
| 界面 | SwiftUI / AppKit，PDFKit 阅读器（`macOS/Sources/PageBridge`） | WinUI 3（`windows/PageBridge.Windows`），页面图像叠加文字层 |
| 本机服务 | `PageBridgeCore`：配置与钥匙串、存储、助手、文献下载、worker 协议 | `desktop/`：Python 服务，经持久 RPC（`bridge.py`）提供文档库、凭据管理器、阅读、助手、文献、更新 |
| 翻译引擎 | 共享的 `engine/worker.py`，随包私有 Python 3.12.12 | 同左 |
| 阅读稿解析 | 按需安装的独立 MinerU 4.0.12 Basic / ONNX CPU 环境 | 同左 |

没有 PageBridge 账户或中转服务器，也没有本地 HTTP 服务。宿主为每个任务启动一个 worker 进程，向 stdin 写一行 JSON 请求，从 stdout 读取版本化 NDJSON 事件（`protocolVersion`、`jobId`、`attemptId`、递增 `sequence`、`type`）。API Key 只经该管道进入 worker 内存，不出现在命令行、环境变量或配置文件中。上游日志在文件描述符层被重定向，不会混入协议或成为用户可见的错误信息。协议细节见 [engine/README.md](../engine/README.md)。

每次任务使用新的 attempt 目录；输出全部通过检查后才原子提交。失败或取消保留上次成功结果。取消时 Mac 回收 worker 进程组，Windows 用 Job Object 结束进程树；迟到的结果不会覆盖已接受的取消。

## 原页版式阅读稿

流程：**原 PDF → MinerU 逐页解析出带坐标的块 → 每个完整段落独立翻译 → `reading.json` + `manifest.json` → 原生页画布显示**。协议与产物格式见 [engine/STRUCTURED_READING.md](../engine/STRUCTURED_READING.md)。

- **解析：** 每个所选物理页在独立子进程中解析，单页失败继续处理其余页。子进程清除 Python 路径变量和名称像密钥的环境变量；推理只用本地模型。坐标为归一化的原页左上角 PDF 点（旋转后扣除页面原点），没有可靠坐标的块只在页旁入口显示。
- **背景：** 有原生文字的页面，在内存副本中透明移除已翻译段落的原字形，得到保留照片、色块、水印和细线的 `backgroundImage`；只有 `backgroundBlocks` 列出的块在其上绘制译文，不填纯色矩形。描边和填充形状的边界只用于恢复原页像素。
- **表格与公式：** 原生文字表格保留原数字与格线，文字标签作为独立的 `table_cell` 块按实际坐标翻译，只发送该标签本身。扫描表格保留原图，辅助译文在详情中查看。行内公式保留为原页裁图附件，模板中只有附件占位符；占位符缺失或重复只让该段回退原文。
- **排版：** 页画布用同一排字器测量和绘制，在原框内适配字号（最小字号为正文 8 PDF 点、紧凑表格标签 6.5 点；原文更小的参考文献、脚注等小字最多缩到原字号的 85%，扫描页按区域面积和字数估算原字号），绘制裁在原框内，不扩大段落或增加页面。目录和列表按 PDF 原有文字行和字符坐标保持缩进、点引线和页码列。纯数字、点分序号、页码和代码块直接保留。
- **对照滚动：** 左右两侧按物理页、页内相对位置和横向相对位置同步，缩放或改变布局后恢复锚点；可关闭同步分别滚动。
- **术语表：** 内置术语表是 `engine/glossaries/` 中的文本文件（`index.json` 列出名称和语言对），随引擎打包、只读；用户的术语表保存在数据目录的 `glossaries.json`（Mac `GlossaryLibrary`，Windows `desktop/pagebridge/glossaries.py`，格式相同）。翻译时宿主把所选术语表合并成一段文本（内置在前、自建在后，后出现的同一术语生效）作为请求的 `glossary` 字段交给 worker，并在阅读稿提交后把术语表 ID 列表记在文档上，单段重试、按术语重译和追加页都取这些术语表的当前内容。`engine/glossary.py` 解析后，每段只在提示词末尾列出该段出现的术语（英文整词匹配并识别常见词形变化，含大写缩写的术语区分大小写）；回答缺少某条术语的指定译法时，追加一轮请求点名修改，修改后缺得更少且公式占位符完整才采用，否则保留第一次的译文。
- **按术语重译与追加页：** `retryTerms` 读取已提交的阅读稿，只重译原文含这些术语的段落；`appendPages` 只解析所选页中尚未在阅读稿里的页并合并进去。两者都只为改动过的页重新生成背景，其余页沿用阅读稿中已有的背景。
- **提取术语：** worker 的 `suggest_terms`（`engine/term_suggestions.py`）在本机从阅读稿或 PDF 文字中统计高频短语、缩写和较长的技术词，排除已有术语，把候选列表和文档开头约 4,000 字符发给所选模型，请它挑出领域术语并给出译法；只保留文档中确实出现的术语，由用户核对后写入术语表。
- **失败处理：** 不使用语义、数字或整篇内容门控。失败的段落或页面标为 `source_fallback`，产物状态为 `partial`，其余内容照常可读。保留的基础检查只有路径、文件、任务身份、结构、取消和原文件未改变。单段重试通过 `readingPath` 和 `retryBlockIds` 读取已提交稿件，不重新解析，只更新这些块并发布新稿；重新生成会替换整份阅读稿及其页范围。

原 PDF 始终不变。不执行解析器输出的 HTML，也不重建可填写表单。

### 为什么选择 MinerU

阅读模式保留原 PDF，在原页上叠加结构化译文。选择解析器时，对四种方案在六份公开 PDF 的七个问题页上做了同条件实测（同一模型、提示和分批）：

| 方案 | 结果 | 定位 |
|---|---|---|
| MinerU 4.0.12 Basic / ONNX CPU | 日文整段、扫描正文和双栏分块较好，公式页约 12 秒；目录漏页码，参数表合并了列 | 选用，作为可下载的阅读后端 |
| Docling 2.143.0（CPU） | 日文定义框和合并表恢复较好；目录与页码错绑，扫描页阅读顺序错误，公式页约 167 秒 | 备选，未接入 |
| PaddleOCR 3.7.0 PP-StructureV3 | Mac arm64 可运行；日文 OCR 混字，参数表数值和单位错位，公式页约 104 秒 | 仅离线实验 |
| PyMuPDF + Apple Vision | 原生文本页很快，扫描页句序较完整；双栏混排、公式符号丢失 | 不具备复杂结构解析能力 |

实测得出的设计约束：强制 OCR 不能统一解决表格；结构化输出不保证数字归属正确；以视觉行为翻译单位会让模型把译文挪到相邻块，所以按完整段落翻译并把失败限制在单块；没有哪个方案对扫描页全面胜出；表单只能保留在原 PDF 中。MinerU 的许可证含附加条款，见[第三方说明](../THIRD_PARTY_NOTICES.md#按需下载的对照阅读组件)。

## 导出译文 PDF 与双语 PDF

宿主先让用户选择保存位置，再启动 worker 的 `export_reading`（见 [engine/README.md](../engine/README.md#request-and-response-protocol)），只读取原 PDF 和已提交的 `reading.json`，不调用翻译服务、不联网。worker 核对阅读稿的源 SHA-256 与页数，在目标文件夹写临时文件，重新打开核对页数后才替换目标文件。

- **译文页：** 从原页本身开始，图片、线条和未译文字保持矢量。有原生文字的页面只删去 `backgroundBlocks` 中译文块的原字形，规则与页画布背景相同；扫描页用各块的 `backgroundColor` 覆盖译文区域（图表、公式和代码区域除外），并删去其下不可见的 OCR 文字层。译文用 PyMuPDF `Story` 写成真实文字：起始字号取区域内原文字号的中位数（下限 8 点，表格标签 6.5 点），放不下时整体缩小到放进原框，不截断；行内公式从原页同一矩形裁图嵌入。`preserved` 与 `source_fallback` 块保持原样，被删文字上的链接重新加回。
- **页序：** 译文 PDF 与原文页数相同，未选页原样复制；双语 PDF 在每个已选原页之后插入其译文页。
- **字体：** 中文、拉丁文字和数学符号使用 MuPDF 内置字体，只嵌入用到的子集，许可见[第三方说明](../THIRD_PARTY_NOTICES.md#导出-pdf-中的字体)。

## 全库搜索

Mac `LibrarySearch` 与 Windows `desktop/pagebridge/library_search.py` 用 PDFKit／PyMuPDF 提取每页文字，缓存在文档目录的 `search-text.json`（按 PDF 的 SHA-256 失效，格式两端相同），再连同阅读稿中的译文做不区分大小写的子串查找，每页最多一处、总共最多 500 处。搜索完全在本机进行。

## 文档库与批注

Mac 的 `LibraryStore` 在 `library.json` 中按文档 UUID 保存文件夹、收藏、最近打开和回收时间，`documents.json` 保存文档记录。写入前先读取并验证，再原子替换；文件损坏或版本未知时拒绝覆盖。删除文件夹只清除归属，回收与恢复只改元数据，不移动或删除 PDF。Windows 的 `desktop/pagebridge/core.py` 提供相同功能，格式不同。

`PDFPreviewCache` 在独立 actor 中生成缩略图并读取 PDF 大纲，限制缓存的文档数、缩略图数和字节数。个人书签来自阅读元数据，与 PDF 内置目录分开显示。

`AnnotationStore` 按文档 UUID 和原 PDF 的 SHA-256 保存高亮、下划线、笔记和书签，坐标为 PDF 页面坐标；保存和导出时核对哈希。批注只加在原 PDF 上。批注作为 PDFKit 叠加层显示，工作文件不被改写；显示层只接管 PageBridge 创建的批注对象，源 PDF 自带的批注、链接、表单和文字选择保持 PDFKit 原生处理。便笺点击和批注右键打开 `ReaderWorkspace` 的编辑器；编辑前快照文档与哈希绑定，保存时重新核对，防止切换文档时写错。批注 PDF 导出重新打开对应文件并写入完整批注内容；Markdown 导出包含来源、页码、摘录和哈希。

## 在线文献检索

`LiteratureSearchClient` 请求 OpenAlex `/works`：标题和关键词用普通搜索，DOI 或 doi.org 链接规范化后精确筛选，每页最多 20 条。只有用户提交搜索或加载更多时才发送请求；请求代次与任务取消共同阻止过期响应覆盖界面。检索不使用翻译服务、钥匙串或 worker。

全文来源与检索分开（`LiteratureFullTextSource`）：默认 Sci-Hub（`SciHubResolver` 按 DOI 请求页面并解析其中的 PDF 链接，不依赖任何官方 API），也可选公开 PDF（只用 OpenAlex 标为开放获取的地址）。两者共用 `LiteraturePDFDownloader`：独立临时会话，上限 100 MB，初始 URL 和每次重定向都检查 HTTPS 与公共网络目标，不携带密钥、授权头或浏览器登录状态；下载后检查 PDF 标识、完整性和可读页面。域名预解析允许本机 TUN 代理使用的 `198.18.0.0/15` 合成地址，但直接指向该网段的 IP URL 和其他私网、回环、链路本地地址仍被拒绝。

`SciHubBrowserView` 提供临时 WebKit 窗口（nonPersistent 数据存储，Windows 用 WebView2），让用户自行完成网站验证；应用不模拟点击验证码，也不导出 Cookie。加载完成后只读提取页面中的 PDF 地址，交回同一匿名下载器。

Windows 由 `desktop/pagebridge/literature.py` 和 `SciHubBrowserPanel` 实现相同流程。

`AppModel.importLiteraturePDF` 先比对下载文件与未回收文档的 SHA-256，相同则打开已有文档，否则按普通导入流程创建副本并归入当前文件夹。

## 文档助手

助手（Mac：`PaperAssistantContextBuilder`、`PaperAssistantService`、`PaperAssistantModel`；Windows：`desktop/pagebridge/assistant.py`）不经过翻译 worker，复用已保存的服务配置和凭据。Mac 把助手选择的服务保存在 UserDefaults（`PageBridge.assistantServiceID`）；所选配置被删除时依次回退到当前翻译服务和第一个配置。发送时捕获服务、密钥、文档 ID 和输入哈希，回答写回发起请求的文档。

文档问答在本机最多读取 500 页、240,000 字符，按当前页、问题词（另加术语表中译法出现在问题里的原文术语，便于用中文问英文文档）和可识别章节选出最多 6 页、共 12,000 字符（每页最多 2,400）的片段；选文最多 6,000 字符，问题最多 2,000 字符，另附最近 10 条、8,000 字符以内的对话。模型被要求只引用本次提供的 `[P页码]`，只有这些页码会生成可跳转的引用，其余标记为无法核对。网络请求不跟随重定向，响应有长度上限，截断按错误处理；模型输出按纯文本显示。对话按文档保存，最多 40 条。

## 存储

| | Mac | Windows |
|---|---|---|
| 用户数据 | `~/Library/Application Support/PageBridge/` | `%LOCALAPPDATA%\PageBridge` |
| API Key | macOS 钥匙串 | Windows 凭据管理器 |
| MinerU 运行环境 | `…/PageBridge/mineru/4.0.12/` | `%LOCALAPPDATA%\PageBridge\mineru\4.0.12\` |

Mac 数据目录中：`documents.json`（文档记录）、`services.json`（不含密钥的服务配置）、`glossaries.json`（自建术语表）、`library.json`（分类）、`annotations/<文档 UUID>/original/<PDF 哈希>.json`、`assistant/<文档 UUID>.json`。每份文档有原始副本和各次翻译的输出目录（已提交的阅读稿在其中）。元数据都以原子方式写入 JSON。

测试日志和模型调试响应可能包含文档文字，不应公开。

## 本地与在线

| 功能 | 本机处理 | 联网 |
|---|---|---|
| 解析、排版、导出、预览、批注、助手片段提取 | 全部 | 无 |
| MinerU 首次准备 | — | 清华 PyPI 镜像（依赖包，Python 随应用提供），模型（ModelScope 或 Hugging Face） |
| 本机 Ollama 翻译／问答 | 全部（仅连接回环地址） | 无 |
| 在线 API 翻译／问答 | — | 待翻译文本，或问题、片段与近期对话发送至所选服务 |
| 文献检索与下载 | — | 检索词发往 OpenAlex，DOI 发往所选 Sci-Hub 站点，下载连接文献来源 |
| 更新检查 | — | PageBridge 官网（Cloudflare，不发送文档、笔记或密钥） |

所需组件与模型齐备后，使用 Ollama 可以完全离线工作。应用不会因本机模型不可用而自动改用云服务。只支持 OpenAI Chat Completions 兼容接口；远程地址必须是 HTTPS，HTTP 只允许回环地址，拒绝带凭据的 URL 和 HTTP 重定向。模型列表与权限以账户接口返回为准。

## 更新与发行

**Mac：** `UpdateController` 使用 Sparkle 2.10。`update-config.json` 记录仓库、feed 地址和 Ed25519 公钥，构建时写入 Info.plist。feed 和应用 ZIP 都须验证签名，只用 HTTPS；关闭系统概况上报和静默安装。翻译运行时不允许检查更新，已同意的安装由 `UpdateInstallationGate` 推迟到任务结束；更新触发的退出与开始翻译竞争时，`AppDelegate` 拒绝退出。更新只替换应用包，用户数据和钥匙串在包外。

**Windows：** `desktop/pagebridge/updates.py` 在后台线程检查和下载签名的 `windows-update.json` 所指安装器，阅读不等待网络。安装前重新验证签名与哈希，WinUI 锁定文件复核后以普通交互方式启动 Inno Setup 安装向导，然后正常退出；安装器不从 Python 进程中启动。

**发行：** `v*` 标签触发 GitHub Actions 的联合发布：核对版本与 build，分别测试和构建两端，签名 feed 与清单，草稿上传后核对每项资产再公开；私钥只在签名步骤使用。命令见[开发指南](DEVELOPMENT.md#版本与发布)。当前应用为 ad-hoc 签名（Mac）和无 Authenticode 签名（Windows），更新签名不代替系统发行者签名。
