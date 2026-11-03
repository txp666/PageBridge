# 版本记录

## 0.6.0 / build 16 — 只保留原页版式阅读，阅读稿可导出 PDF

本版把翻译统一为原页版式阅读稿一种方式，并让阅读稿可以导出为 PDF。

**优点**

- 阅读稿可导出译文 PDF（页数与原文相同，未翻译的页不变）和双语 PDF（每个已翻译的页紧跟在原页之后）。译文是可选择、可搜索的文字，放不下时缩小字号，不截断；导出在本机完成，不需要翻译服务。
- 应用大幅变小：Mac ZIP 约 230 MB → 60 MB，安装后约 670 MB → 150 MB；Windows 安装器约 200 MB → 90 MB，安装后约 830 MB → 330 MB。
- 参考文献、脚注、页脚等小字的译文最多缩到原字号的 85%，“放不下”的蓝色标记明显减少（实测 224 段中由 58 段降到 7 段）。
- 扫描页和文字页使用同一套解析与翻译流程，表格、公式和图片的处理更一致。

**缺点（不兼容变更）**

- 移除“重排 PDF”模式。0.5.x 用该模式生成的译文不再显示（文件保留到删除该文档），需要用阅读模式重新翻译。
- 译文一侧不能高亮、下划线或加批注，批注只能加在原文 PDF 上；0.5.x 加在译文 PDF 上的批注不再显示。
- 有文字层的 PDF 也要先下载约 4 GB 的本地识别组件才能翻译。
- 导出的 PDF 按 PageBridge 自己的排字规则生成，与屏幕上的阅读稿相近，但不逐像素一致。

**English**

Translation now has a single mode, the original-layout reading draft, and reading drafts can be exported as PDF.

**Advantages**

- Export reading drafts to a translated PDF (same page count, untranslated pages unchanged) or a bilingual PDF (each translated page right after its original). The translation is real, selectable, searchable text, shrunk to fit rather than truncated; export runs locally without a translation service.
- Much smaller apps: Mac ZIP about 230 → 60 MB, installed about 670 → 150 MB; Windows installer about 200 → 90 MB, installed about 830 → 330 MB.
- Small print (references, footnotes, footers) may shrink to 85% of its original size, so far fewer blocks show the blue "doesn't fit" marker (58 of 224 measured blocks down to 7).
- Scanned and text pages go through the same parsing and translation path, so tables, formulas and images are handled consistently.

**Disadvantages (breaking changes)**

- The Reflowed PDF (重排 PDF) mode is removed. Translations made in that mode by 0.5.x are no longer shown (their files stay until the document is deleted); translate again in reading mode.
- The translation side cannot be highlighted, underlined or annotated; annotations go on the original PDF only, and 0.5.x annotations on translated PDFs are no longer shown.
- PDFs with a text layer also need the roughly 4 GB local recognition component before translating.
- Exported PDFs use PageBridge's own typesetting: close to the on-screen reading draft, but not pixel-identical.

## 0.5.1 / build 15 — MinerU 模型下载修复

修复首次使用阅读模式时一直停在“正在下载 MinerU 模型”的问题。

- 模型优先从 ModelScope 下载（国内直连，实测约 1 分钟），失败或 2 分钟内没有收到数据时自动改用 Hugging Face。
- 下载时显示进度“正在下载 MinerU 模型：x / 818 MB”；两个来源都失败时提示检查网络或代理。
- 使用说明新增电脑配置要求。

**English**

Fixes reading mode getting stuck on "downloading MinerU models" on first use.

- Models now download from ModelScope first (about a minute in tests), switching to Hugging Face if it fails or receives nothing for 2 minutes.
- Download progress is shown as "x / 818 MB"; if both sources fail, the error asks you to check your network or proxy.
- The docs now list the system requirements.

## 0.5.0 / build 14 — 原页版式对照阅读

- 翻译窗口默认生成原文与译文对照阅读稿，使用 MinerU 4.0.12 Basic / ONNX CPU 在本机解析文本或扫描页，再调用已配置的 Ollama 或 OpenAI 兼容服务翻译。首次使用按需下载独立识别环境和模型；安装包不含这些模型权重。
- 阅读稿保持原页尺寸、分栏和图表位置。译文放在原段落区域内，超出区域可点击展开；原生文字页只移除对应原文字形，保留照片、水印、色块和细线。混色标题保留原图，点击查看译文。
- 原生文字表格保留数字、行列和格线，在实际文字坐标翻译标签，修复财表错位、漏行和背景覆盖。扫描表格保留原图并提供辅助译文。
- 已识别行内公式使用原页裁图作为文字附件，公式占位符只对真实附件发送；某段失败时保留该段原文，可单独重试，不阻止其余内容阅读。
- 原文与译文支持双向连续同步滚动、统一顶部与缩放比例；可关闭同步分别阅读。取消或整体失败保留上次阅读稿，重新生成会替换阅读稿及本次页范围。
- Windows 阅读稿恢复同步开关，标题字号参照原页字体；页面提示移到既有标题栏，长译文详情按弹层宽度换行；修复长文档关闭详情后误退到前页的像素取整问题。修复首次打包时临时 DLL 占用造成的目录迁移失败。
- 现有重排 PDF 模式继续提供译文和交替页双语 PDF 导出；阅读稿与重排 PDF 分别保存。原 PDF 不改写。

已知边界：阅读稿暂不支持自身全文搜索、批注或 PDF 导出；阿语扫描页无阿语文本层时保留原页并提示组件不支持。密集文字可能需展开，部分复杂公式、页脚和专业术语仍可能误识别或误译，应结合原文核对。

## 0.4.0 / build 13 — Mac 与 Windows 双平台发行

- 新增 Windows 11 x64 正式安装器及便携包，使用 WinUI 3，复用锁定的 PDFMathTranslate-next / BabelDOC 引擎；随包提供 Python、.NET 与所需 WinUI 运行库，无需用户单独安装这些运行时。
- Windows 安装器支持当前用户安装、选择安装目录、快捷方式及卸载入口；默认位于 `%LOCALAPPDATA%\Programs\PageBridge`，卸载保留独立文档库、配置和系统凭据。
- Windows 接入 OpenAlex 搜索、Sci-Hub／公开 PDF 来源、直接 DOI、应用内 WebView2 验证页和下载校验。重复导入复用已有文档，保留原分类、译文和笔记。
- Windows 提供侧栏折叠、连续滚动、页面／目录／书签导航、文内搜索与链接、缩放快捷键，以及文件夹新建／重命名／删除和资源管理器定位。
- Windows 增加文字选择、高亮、下划线、页笔记的新增／编辑／删除、书签和批注 PDF／Markdown 导出；批注按 PDF 哈希区分原文、译文及重译版本。
- Windows 文档助手复用已配置的模型，支持四种范围、有限上下文、引用核对、取消、清空和回答存笔记。
- Mac 与 Windows 同步压缩助手布局：文件名与清空留在顶部，对话占据中间，模型和范围同排放在底部，快捷问题收进菜单；移除重复说明，必要的目的地与范围信息保留在提示和引用片段中。
- 修复 Windows 主题混用、页码基线、笔记多行编辑、空助手滚动位置，以及取消时后台进程清理相关问题。
- 两平台版本统一为 `0.4.0 / build 13`，新增联合发布所需的 Windows 资产及校验。Mac 继续使用 Sparkle 签名 OTA；Windows 新增手动检查、用户确认下载／安装、进度和取消，校验 Ed25519 更新清单及安装包 SHA-256，并在翻译或助手忙碌时阻止安装。便携版的 OTA 同样启动安装向导，不原地替换便携文件。Windows 尚无 Authenticode 签名；Mac 仍为 ad-hoc 签名，尚未完成 Apple 公证。

发行资产为 `PageBridge-0.4.0-macOS-arm64.zip`、`PageBridge-0.4.0-windows-x64-setup.exe`、`PageBridge-0.4.0-windows-x64.zip`、项目源码和第三方对应源码 ZIP，各附 SHA-256；另提供 Mac 签名 appcast 和 Windows 签名 `windows-update.json`，共 12 项发布资产。

## 0.3.0 / build 12 — 在线文献检索、应用更新与开源发布

- 新增“搜索在线文献”，使用 OpenAlex 按标题、关键词、DOI 或 DOI 链接检索，支持开放获取筛选及分页。
- 全文来源默认 Sci-Hub，可切换为公开 PDF；通过 DOI 解析页面提供的 PDF 地址，再沿用文档库下载、校验和阅读流程。已有 DOI 时提供不依赖 OpenAlex 结果的直接打开入口。
- 支持配置 Sci-Hub HTTPS 地址。遇到网站验证，可打开临时 WebKit 窗口自行完成，再把页面提供的 PDF 交回阅读器；不自动点击验证码、不导出浏览器凭据。
- 公开 PDF 下载校验后加入本机文档库并直接阅读；相同文件复用已有记录，保留笔记和译文。没有公开 PDF 时提供原文页面入口。
- 下载支持取消、100 MB 上限、无效文件清理，以及本机 TUN 代理的合成 DNS 地址；保持 HTTPS 验证和匿名请求。
- 修复侧栏添加菜单图标重叠、排序菜单交互和深色主题箭头颜色。
- 站点窗口拒绝不支持的主页面跳转时，结束加载状态并说明原因，避免继续按钮保持禁用。
- 版本号集中到 `VERSION.json`；新增 AGPL-3.0 许可证、第三方说明、贡献与安全指南，提供项目源码包及锁定 AGPL 上游源码材料。应用包内包含完整许可文件。
- 新增归档脚本，包含工作区的新文件、生成源码哈希清单和 SHA-256 文件，并拒绝覆盖既有归档。
- 加入 Sparkle 2.10.0 应用内更新：“检查更新…”与“自动检查更新”菜单，保留自动检查首次同意流程，不静默下载或安装，不上报系统配置画像；翻译期间暂缓检查和安装。
- 更新 feed 与 ZIP 使用 HTTPS 和 Ed25519 签名，公钥随应用分发，私钥仅用于受限发布签名。
- 增加 GitHub CI 与标签发布工作流：测试、构建、三类归档、哈希和签名 feed，draft 资产校验通过后公开。

发行文件为 `PageBridge-0.3.0-macOS-arm64.zip`、`PageBridge-0.3.0-source.zip` 和 `PageBridge-0.3.0-third-party-sources.zip`，各自附 SHA-256，并提供签名 `appcast.xml`。下载见 [v0.3.0 Release](https://github.com/txp666/PageBridge/releases/tag/v0.3.0)。

## 0.2.3 / build 10 — 通用文档助手与紧凑阅读布局

- 压缩阅读区顶部标题和工具栏，长文件名按可用空间截断；右侧助手与笔记铺满阅读区，顶部工具与底部输入／导出区固定，不再整体垂直居中。
- 将当前产品定位和助手文案扩展为通用 PDF 文档阅读，适用于手册、报告、教材、合同与论文等；保留实际论文示例和历史验证记录。
- 文档助手可从已保存的服务配置中独立选择模型，在本机记住选择，不改变翻译模型。所选配置被删除时优先回退到当前翻译服务，再回退到首个可用配置。
- 打开助手或切换模型不发送请求；点击发送或快捷问题时才使用所选模型、当前范围和有限的近期对话。
- 文档问答增加概述、目的、要求、步骤和注意事项等章节定位，保留摘要、结论、分页引用及上下文限制。

仅本地构建，未公开发布。

## 0.2.2 / build 9 — 修复图表论文翻译误判

- 修复参考文献 `pp.35–41` 页码紧邻句点时漏计起始页码，以及跨行网址中的 `d3` 被误当正文标识符、合并域名后紧邻中文的访问年份被网址识别吞掉的问题；实际数字、公式和网址更改仍受完整性校验。
- 恢复旧字体项目符号的独立列表排版；访问日期不再干扰参考文献编号识别。对明确被单张图版页隔开的断词句头，按原字形归属组成完整翻译输入，保留公式、引用与原页面目标框。
- 失败后保留不含正文、提示词、模型响应或密钥的诊断摘要；错误提示区分首次失败和存在旧译文的情况，显示实际段落计数。

## 0.2.1 / build 8 — 修复批注弹窗

- 页面便笺点击接入应用笔记编辑器，修复 PDFKit 原生弹窗在当前系统深色外观下内容不可见、编辑未接入持久化的问题。
- 高亮、下划线通过右键“编辑批注笔记…”或侧栏编辑；摘录与笔记分开展示。保留高亮区域的正常拖选，并避让 PDF 链接与表单。
- 明确文本框颜色、滚动与输入焦点；Return 换行、⌘ Return 保存、Esc 取消。输入法组合文字交给系统处理。
- 页面显示不再生成高亮旁的原生评论气泡；导出仍写入完整标准 PDF 批注内容。仅识别当前文档实际持有的批注对象，避免误改重新导入文件中的原有注释。

仅本地构建，未公开发布。

## 0.2.0 / build 7 — 论文阅读工作区

- 增加文件夹、收藏、最近文档和可恢复回收站，支持名称搜索、排序、移动与重命名；分类写入独立 `library.json`，保留旧 `documents.json` 格式。
- 增加文档首页预览、异步页面缩略图、PDF 内置目录与个人页书签；恢复上次阅读页码，并处理 PDFKit 首次布局覆盖进度的问题。
- 增加原文／译文的高亮、下划线和阅读笔记，按文档与 PDF 哈希隔离保存；支持导出带批注 PDF 与 Markdown 笔记，不覆盖工作 PDF。
- 增加论文助手，复用已配置的服务、模型与钥匙串密钥；支持论文片段问答、当前页、选中文字和自由提问。
- 助手明确限制本机提取和请求上下文范围，展示已提供的页码片段，并标记无法核对的引用；回答可复制或存为阅读笔记。打开面板不自动发送，快捷问题按当前范围立即发送。

仅本地构建，未公开发布。

## 0.1.4

- 增加厂商预设、模型选择与主动获取模型列表；常用在线服务只需选择模型并填写 API Key。
- 增加五份公开示例 PDF、应用内示例入口与来源、许可、哈希清单。
- 增加 PageBridge 软件图标与 macOS ICNS 构建。
- 改进普通英文漏译检查，恢复被数字、图号或换行粘连的正文；保留人名、网址和技术标识。
- 按源字形恢复编号标题、项目列表、悬挂缩进、跨栏及跨页短语；对满足严格条件的双栏断句进行一次整句翻译，在两个原文本框连续排版。
- 将姓名与原始重音符号绑定为不可拆分的字形组，区分普通冠词、叠印正文与真实公式。
- 保护公式、上下标、数值、单位及其源文档关系；为已确认的参数归属、预测时间方向、类别连接词和输入／输出定义错误增加校验与有限重试。
- 改进重排后的 URI、文内跳转、图号及引用链接映射；带源链接的段落不参与双框整句合并，避免扩大可点击范围。
- 为本机模型设置响应长度上限并拒绝截断响应；无法恢复时阻止提交，保留上次成功结果。
- 增加源码完整重译、最终包烟测与结果接纳工具，绑定输入、引擎、输出哈希；隔离验证材料，保留旧结果。
- 初始化本地 Git，补齐使用、开发、架构、第三方与验证文档。

仅本地构建，未公开发布。

## 0.1.3

- 对模型返回的样式/公式占位符增加校验、重试与原结构恢复；失败时阻止提交并保留已有结果。
- 修复已确认的公式脚标、平方单位与变量跨行问题，保护目录、索引及原始数学字形。
- 完整重译并检查原有 6 页与 68 页文档。

## 0.1.2

- 裁剪未使用的网页界面、服务 SDK、测试与开发资源，保留私有 Python、PDF 解析与排版能力。
- 通过本机 Ollama 和本机 OpenAI 兼容接口的真实翻译、结果读取及导出复制检查。

## 0.1.1

- 修复顶部双击放大/还原行为。
- 改进长文档双栏同步滚动，并用 68 页文档检查跨页、缩放和末页行为。

## 0.1.0

- 完成 SwiftUI / AppKit / PDFKit 原生应用与独立 Python worker。
- 接入 PDFMathTranslate-next、本机 Ollama 和自定义 API，支持导入、翻译、对照阅读、历史记录与系统导出。
