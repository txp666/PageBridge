# 开发与构建

当前版本为 **0.3.0 / build 12**。在既有阅读、批注、翻译及文档助手基础上，加入 OpenAlex 标题／关键词／DOI 检索、Sci-Hub／公开 PDF 来源选择、直接 DOI 打开和临时 WebKit 验证窗口。以下命令均从项目根目录执行。

文献功能的实机验收已完成公开图表设计示例的搜索、站点验证、下载和去重打开。引入 Sparkle 更新器后的 Swift 测试共 **129 项，127 通过、2 项可选集成测试跳过**；引擎 655 项单元测试在此前本轮打包阶段通过。实际 CI 运行与最终包验收以 [GitHub Actions](https://github.com/txp666/PageBridge/actions)和[验证记录](VERIFICATION.md)为准，不能将本地编译结果当作 OTA 安装或 CI 发布已完成。

公开发行候选使用 build 12，取代先前仅在本机验证、不含 OTA 的 build 11。两者产物和哈希应分开记录；此前的安装包大小不适用于加入 Sparkle 后的新包。引擎模块未因更新器改变，本轮没有重新执行全文模型翻译。0.2.2 的 7 页复译与 116 页源解析影响回放继续作为历史证据保存。

## 环境

- Apple Silicon Mac、Xcode Command Line Tools / Swift 6。
- 部署目标 macOS 14+；实际验证系统以每次验收记录为准，不把声明目标当作已测矩阵。
- Python 3.12.12 和依赖由 `engine/setup.sh` / `engine/uv.lock` 管理。
- 本地真实翻译需要独立安装 Ollama 并准备模型；应用不携带语言模型。
- 独立页面复核优先使用 Poppler 的 `pdftoppm`。未安装时可使用检查器的 `--renderer auto`，报告会记录实际渲染器。

```sh
bash engine/setup.sh
```

## 开发验证

```sh
engine/.venv/bin/python -B -m unittest discover -s engine/tests -v
python3 scripts/test_bundle_profile.py
engine/.venv/bin/python -B scripts/test_script_baselines.py
engine/.venv/bin/python -B scripts/test_result_adoption.py

CLANG_MODULE_CACHE_PATH=/tmp/pagebridge-clang-cache \
SWIFT_MODULECACHE_PATH=/tmp/pagebridge-swift-cache \
swift test --package-path macOS --cache-path /tmp/pagebridge-spm-cache --disable-sandbox
```

默认 Swift 测试不读取真实云 API Key。可选的真实 Ollama 与临时 Keychain 测试分别用 `PAGEBRIDGE_TEST_OLLAMA=1` 和 `PAGEBRIDGE_TEST_KEYCHAIN=1` 启用。真实 Key 不得出现在命令行、代码、日志或 Git 中。

默认测试中的两个跳过项正是这两项可选集成测试，不能把跳过写成通过。实际原生助手调用本机 Ollama 属于独立的交互验证，也不等同于云 API 或上述固定短句测试。

阅读工作区改动还应通过原生界面复核：文件夹和搜索后的选择一致性、重命名与收藏、回收恢复、缩略图与目录跳转、原文／译文批注隔离、重启后笔记与书签恢复。导出须实际使用 `NSSavePanel`，再独立读取 PDF 注释、页数和 Markdown 页码／标题，不能仅测试复制文件。助手需检查发送范围、真实回答、有效引用导航、未提供页码提示、取消、切换文档后结果归属，以及回答保存为笔记；不把包含 `[P页码]` 的文本本身视为语义验证。

## 先验证 PDF，再打包

源运行模式使用开发虚拟环境，但与发行包共享同一组 worker 模块；运行前后记录所有模块与输入 PDF 的 SHA-256，代码变化会让该次验收失败。

```sh
engine/.venv/bin/python -B scripts/retranslate-pdfs.py \
  --source-engine --input demos/pdfs/avalanches.pdf \
  --output validation/my-avalanches-run
```

输出目录必须是仓库内尚不存在的目录。该命令使用 `127.0.0.1:11434` 上已有的 `qwen3.5:35b-a3b-int4`，可用 `--model` 指定另一已安装模型；一次命令处理一个文档。真实翻译验证应按串行启动，避免同时争用本机模型。它不自动接纳到用户文档库。`run.json` 记录原文件、模型、页数、时间和质量门结果；`events.ndjson` 记录进度。只有成功结果才提供译文路径。诊断目录是本机材料，可能包含论文片段，应留在 Git 之外。

按 `run.json` 中的实际路径调用：

```sh
engine/.venv/bin/python -B scripts/check-pdf-quality.py \
  --original ORIGINAL.pdf --translated TRANSLATED.pdf --dual BILINGUAL.pdf \
  --output-dir validation/my-review --renderer poppler \
  --focus-pages all --fail-on-markers
```

检查器退出成功只表示基础结构检查通过。`--focus-pages all` 会为每一页生成高分辨率复核图；长篇初筛可改为具体页码，例如 `1,14,26,32,39,54,58`，但不能因此把未看的页面写成已检查。仍需复核数字/公式疑点、逐页渲染、列表和图表；链接必须检查目标类型、页码和可见文本位置，不能只数注释数量。

0.1.4 的翻译验收集合为原有 6 页与 68 页文档，加上 `avalanches.pdf`、`wind-data-mining.pdf`、`regnann.pdf` 三篇公开论文，共 112 页；另有 4 页 `reproducible-research.pdf` 验证示例流程。0.3.0 本轮没有重新执行该整篇集合。页码探针用于定位问题，不能替代受影响文档的整篇重译。完整历史表格见 [验证记录](VERIFICATION.md)。

每次补丁先记录新旧模块哈希并审计实际影响范围。已通过的文档只有在原生源重放和相关路径审计证明补丁不触发、请求和排版等不受影响时，才能保留原输出哈希绑定的结论；受影响或影响无法确定的文档须全文重跑、复核。局部探针成功不足以接受整篇；也不能把旧结论标成由最新模块哈希生成。比较两个完整输出时，只有单语及双语页面像素、文字、文字位置、链接全部一致的页面可以继承原结论，旧失败仍须重新核实。

若完整重译出现新的回退，可另存由已审完整页面组成的结果，不改写原始运行或页面内容。必须记录基础运行、替换运行、每页来源、输入与输出哈希，并验证全部页面的像素、文本、链接、尺寸、双语映射、完整目录属性及命名目标；该文件称为已审合成结果，不能声称它是一次全新运行全部通过。

当前候选、测试数量和每份文档的验收状态统一记录在 [验证记录](VERIFICATION.md)。记录真实模型证据时应区分普通重试与整段恢复：若完整段落仅通过普通重试恢复，不能据此声称数字保护标记已在真实模型请求中触发。保存响应的离线回放也不能写成一次新的模型请求。

## 构建应用

```sh
bash scripts/build-app.sh
python3 scripts/verify-bundle.py
```

产物为 `dist/PageBridge.app`。构建流程生成 ICNS、复制原生可执行文件、Sparkle framework 与更新辅助程序、示例 PDF 和私有 Python，按 `scripts/bundle-profile.json` 裁掉未使用的 Web UI/SDK/开发资源，最后从内向外进行 ad-hoc 签名。更改任何打包文件后都需重新签名，不要直接修改已经验收的应用。

原生检查通过后归档当前版本；实际大小、哈希和验收范围见[验证记录](VERIFICATION.md)：

```sh
python3 scripts/package-release.py
```

版本号和 build 号统一保存在根目录 `VERSION.json`，由 `assemble-app.py` 读取。`package-release.py` 默认生成应用和项目源码归档；可用 `--app-only` 或 `--source-only` 单独生成，脚本拒绝覆盖已存在的归档。应用归档不收录开发机资源叉元数据，图标及应用所需资源仍作为普通文件保留。压缩后应独立解压，对比全部文件内容、权限和符号链接，并对解压应用再次运行 `verify-bundle.py --app 解压路径/PageBridge.app --output 验证报告.json`。仅压缩命令退出成功不代表验收完成。

示例库从 `PageBridge.app/Contents/Resources/demos/catalog.json` 读取目录，PDF 位于同一目录下的 `pdfs/`。这些资源由 `assemble-app.py` 从仓库 `demos/` 复制；直接 `swift run` 不会组装完整资源包，不能用于验证示例入口。新增示例时应同步记录页数、作者、来源、许可和 SHA-256，再通过完整应用包检查导入。

`verify-bundle.py` 检查应用版本与 `VERSION.json` 是否一致、许可证匹配、更新源／公钥、内嵌 Sparkle 与辅助程序签名、私有运行时、依赖版本、四个引擎模块、图标、示例清单/哈希及签名。它不调用真实模型。翻译引擎、依赖或调用链变化时，还应使用最终包和公开示例做真实翻译烟测：

```sh
engine/.venv/bin/python -B scripts/retranslate-pdfs.py \
  --app dist/PageBridge.app --input demos/pdfs/reproducible-research.pdf \
  --output validation/final-bundle-demo
```

该命令由最终包内的 Python 和 worker 执行翻译，外层 Python 只运行验证脚本。仅阅读界面改动且引擎模块未变时，应记录这一边界与原输出绑定，不能虚称重新完成翻译。每次新构建仍需打开原生应用，检查示例导入、厂商/模型设置、PDFKit 阅读和系统保存对话框；普通 PDF 导出应与已提交结果字节相同，批注导出则核对页面与注释内容，再压缩发行。文件复制检查不能代替保存对话框验证。

历史 0.1.4 已完成上述原生流程，压缩包为 `dist/PageBridge-0.1.4-macOS-arm64.zip`，旁有 `.sha256` 校验文件。该历史版本应用文件合计 614,261,915 字节，压缩包 222,843,301 字节；解压后的 12,303 个文件树项目与原包的内容、权限、符号链接一致。这些数字只对应历史 0.1.4；各版最终包体、归档 SHA-256 与解压复验分别记录于 [验证记录](VERIFICATION.md)。

`scripts/adopt-reviewed-results.py` 是维护工具，必须先退出应用，只接纳已完整重译、人工复核、输入/结果/四个模块哈希匹配的本轮记录；它保留旧结果并生成独立接纳报告。上述补丁影响审计不会绕过该工具的严格哈希条件，接纳成功也不代表最终应用包已通过。普通用户流程通过应用自动提交结果，不需要运行这个工具。

## 目录与版本管理

| 目录 | 内容 |
|---|---|
| `macOS/Sources/PageBridge` | SwiftUI/AppKit 窗口、文档库、页面导航、PDFKit 阅读器、批注与文档助手、服务设置 |
| `macOS/Sources/PageBridgeCore` | 配置、钥匙串、进程协议、分类/批注/对话存储、助手上下文与请求 |
| `engine` | 锁定依赖、翻译/排版/链接保护及单元测试 |
| `scripts` | 构建、裁剪、真实翻译和 PDF 检查 |
| `assets` | 生成图标原图、ICNS 和生成说明 |
| `demos` | 公开许可原始示例、来源及哈希 |
| `docs` | 用户、开发和验证文档 |

`.gitignore` 排除虚拟环境、构建产物、密钥文件、本地输出、原型和验证工作目录。用户私有 PDF、配置、Keychain 数据和旧译文不应加入 Git；项目内的 `demos` 是明确选定的公开示例。公开仓库为 [txp666/PageBridge](https://github.com/txp666/PageBridge)，远程 `origin` 指向该仓库。

## 开源交付

原创程序代码采用 [AGPL-3.0-only](../LICENSE)，第三方代码、运行时和示例保留各自许可及归属。发布时同时提供 `PageBridge-0.3.0-macOS-arm64.zip`、`PageBridge-0.3.0-source.zip`、`PageBridge-0.3.0-third-party-sources.zip` 和各自的 SHA-256 校验文件；源码包应对应实际二进制构建使用的文件，不以旧的 Git 提交归档替代尚未提交的最新源码。

源码归档应包含原生应用与 Core 源码、引擎适配代码、测试、构建脚本、依赖锁文件、文档、图标及有许可记录的公开示例；排除 `.git`、私有文档、模型密钥、机器配置、缓存、虚拟环境、验证输出和已构建应用。生成后独立解压并检查路径、清单与哈希，确认构建所需文件完整。第三方对应源码与分发要求见[第三方说明](../THIRD_PARTY_NOTICES.md)。

贡献流程见[贡献指南](../CONTRIBUTING.md)，敏感信息报告见[安全说明](../SECURITY.md)。公开上传应使用已复核的产物及匹配源码；发布自动化与签名流程见下文。

## 应用内更新与签名

原生应用固定使用 Sparkle 2.10.0；SwiftPM 版本与提交锁定在 `macOS/Package.swift` 和 `macOS/Package.resolved`。`update-config.json` 记录公开仓库、HTTPS feed、公钥和 Sparkle 版本，打包时写入应用 Info.plist。更新源为：

```text
https://github.com/txp666/PageBridge/releases/latest/download/appcast.xml
```

appcast 与应用 ZIP 均通过 Ed25519 签名校验；公钥可随源码公开，私钥不得写入 Git、普通配置、命令行或日志。本机签名使用钥匙串账户 `tech.pagebridge.mac.ota`；GitHub 发布作业仅通过本仓库的 `SPARKLE_PRIVATE_KEY` Actions Secret 取得签名材料。不要在 PR 或普通 CI 中提供该 Secret。

本机在原生检查、归档完成后，生成第三方源码包与签名 feed：

```sh
engine/.venv/bin/python scripts/fetch-third-party-sources.py
engine/.venv/bin/python scripts/create-appcast.py --account tech.pagebridge.mac.ota
engine/.venv/bin/python scripts/verify-release.py --check-latest --output validation/release-check.json
```

CI 不传 `--account`，由 `create-appcast.py` 从环境中的 `SPARKLE_PRIVATE_KEY` 读取密钥并使用受限临时文件。脚本使用官方 Sparkle 工具签名，核对版本、公钥和更新源；`verify-release.py` 使用公钥独立复核，不需要私钥。更新签名与 Apple Developer ID 签名、公证是不同机制，当前应用仍采用 ad-hoc 签名。

客户端保留 Sparkle 首次自动检查同意流程，关闭静默下载／安装和系统配置画像上报。翻译期间拒绝检查、暂缓已经请求的安装；只有用户已同意安装且当前翻译结束后才继续更新重启。检查到同一 build 的结果只能证明 feed 可读，不能证明下载、替换、重新启动或翻译并发防护已在实机通过；完整 OTA 验收须记录升级前后版本、签名、用户数据及重启结果。

## GitHub CI 与发布

- `.github/workflows/ci.yml`：`main` 推送及 Pull Request 触发 Swift、锁定引擎与发布脚本测试，使用官方 `macos-15` runner 并检查 `uname -m` 为 `arm64`；普通 CI 不持有 OTA 私钥。
- `.github/workflows/release.yml`：`v*` 标签触发仓库／标签身份检查、完整测试、构建、三份归档及 SHA-256、签名 appcast，并独立解压验证应用。先上传 draft Release，再通过 GitHub API 比对每项资产的状态、字节数与 SHA-256，全部一致后公开。
- 版本由 `VERSION.json` 提供；每次发布同时更新语义版本、严格递增的 build、CHANGELOG 与使用说明。标签必须与版本匹配，例如版本 `0.3.0` 对应 `v0.3.0`。发布前检查已有 latest 的 build，拒绝相同或更小的 build。
- 先合入通过 CI 的提交，再对同一提交创建发布标签。已经公开的标签和资产不重打、不移动或覆盖；需要修复时发布新版本和更高 build。失败的 draft 应保留证据并查明失败点，不能通过改写旧公开资产跳过校验。
- 发布后用 `engine/.venv/bin/python scripts/verify-release.py --published --output validation/published-release-check.json` 检查公开 Release、全部下载文件哈希、ZIP 的 HTTP Range `206` 与 latest feed。配套项目源码和第三方源码应与该二进制同页提供，签名 feed 指向该版本的固定应用 ZIP URL。

工作流配置存在不等于某次 CI 或发布已经成功；每次版本都应保存具体 Actions run、tag／commit、公开资产大小、SHA-256 和下载复核结果。

CI 使用 `macos-15` 的 arm64 runner，明确选择 Xcode 16.4，并安装固定 uv 0.9.17，再按锁文件安装引擎。若上传中断，只有相同标签、相同提交且带本工作流来源标记的未发布 draft 可恢复；已公开发行不能覆盖。

发布工作流修复后，可从 main 手动重跑已有版本标签，无需移动标签：

```sh
gh workflow run release.yml --ref main -f tag=v0.3.0
```

手动运行仍完整测试、构建和验证该标签的源码，并记录源码提交与工作流提交。只有同标签、同源码提交且带本工作流来源标记的 draft 可继续；已公开版本会被拒绝。草稿通过 `gh release view --json apiUrl` 定位，不能依赖只查已发布版本的 tag REST 端点。
