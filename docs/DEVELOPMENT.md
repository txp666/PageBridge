# 开发与构建

当前源码面向 0.1.4；本轮是否完成验收和打包，以 [验证记录](VERIFICATION.md) 为准。以下命令均从项目根目录执行。

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

## 先验证 PDF，再打包

源运行模式使用开发虚拟环境，但与发行包共享同一组 worker 模块；运行前后记录所有模块与输入 PDF 的 SHA-256，代码变化会让该次验收失败。

```sh
engine/.venv/bin/python -B scripts/retranslate-pdfs.py \
  --source-engine --input demos/pdfs/avalanches.pdf \
  --output validation/my-avalanches-run
```

输出目录必须是仓库内尚不存在的目录。该命令使用 `127.0.0.1:11434` 上已有的 `qwen3.5:35b-a3b-int4`，可用 `--model` 指定另一已安装模型；一次命令处理一个文档。本轮真实验证按串行启动，避免同时争用本机模型。它不自动接纳到用户文档库。`run.json` 记录原文件、模型、页数、时间和质量门结果；`events.ndjson` 记录进度。只有成功结果才提供译文路径。诊断目录是本机材料，可能包含论文片段，应留在 Git 之外。

按 `run.json` 中的实际路径调用：

```sh
engine/.venv/bin/python -B scripts/check-pdf-quality.py \
  --original ORIGINAL.pdf --translated TRANSLATED.pdf --dual BILINGUAL.pdf \
  --output-dir validation/my-review --renderer poppler \
  --focus-pages all --fail-on-markers
```

检查器退出成功只表示基础结构检查通过。`--focus-pages all` 会为每一页生成高分辨率复核图；长篇初筛可改为具体页码，例如 `1,14,26,32,39,54,58`，但不能因此把未看的页面写成已检查。仍需复核数字/公式疑点、逐页渲染、列表和图表；链接必须检查目标类型、页码和可见文本位置，不能只数注释数量。

本轮验收固定为原有 6 页与 68 页文档，加上 `avalanches.pdf`、`wind-data-mining.pdf`、`regnann.pdf` 三篇公开论文，全部 112 页。页码探针用于定位问题，不能替代整篇重译。完整表格见 [验证记录](VERIFICATION.md)。

当前候选、测试数量和每份文档的验收状态统一记录在 [验证记录](VERIFICATION.md)。记录真实模型证据时应区分普通重试与整段恢复：若完整段落仅通过普通重试恢复，不能据此声称数字保护标记已在真实模型请求中触发。保存响应的离线回放也不能写成一次新的模型请求。

## 构建应用

```sh
bash scripts/build-app.sh
python3 scripts/verify-bundle.py
```

产物为 `dist/PageBridge.app`。构建流程生成 ICNS、复制原生可执行文件、示例 PDF 和私有 Python，按 `scripts/bundle-profile.json` 裁掉未使用的 Web UI/SDK/开发资源，最后进行 ad-hoc 签名。更改任何打包文件后都需重新签名，不要直接修改已经验收的应用。

示例库从 `PageBridge.app/Contents/Resources/demos/catalog.json` 读取目录，PDF 位于同一目录下的 `pdfs/`。这些资源由 `assemble-app.py` 从仓库 `demos/` 复制；直接 `swift run` 不会组装完整资源包，不能用于验证示例入口。新增示例时应同步记录页数、作者、来源、许可和 SHA-256，再通过完整应用包检查导入。

`verify-bundle.py` 检查私有运行时、依赖版本、四个引擎模块、图标、示例清单/哈希及签名。它不调用真实模型。完成后可用最终包和公开示例做真实翻译烟测：

```sh
engine/.venv/bin/python -B scripts/retranslate-pdfs.py \
  --app dist/PageBridge.app --input demos/pdfs/reproducible-research.pdf \
  --output validation/final-bundle-demo
```

该命令由最终包内的 Python 和 worker 执行翻译，外层 Python 只运行验证脚本。仍需打开原生应用，检查示例导入、厂商/模型设置、PDFKit 对照和系统保存对话框；核对导出哈希与任务结果相同，再压缩发行。文件复制检查不能代替保存对话框验证。

`scripts/adopt-reviewed-results.py` 是维护工具，必须先退出应用，只接纳已完整重译、人工复核、输入/结果/四个模块哈希匹配的本轮记录；它保留旧结果并生成独立接纳报告。普通用户流程通过应用自动提交结果，不需要运行这个工具。

## 目录与版本管理

| 目录 | 内容 |
|---|---|
| `macOS/Sources/PageBridge` | SwiftUI/AppKit 窗口、PDFKit 阅读器、服务设置、示例入口 |
| `macOS/Sources/PageBridgeCore` | 配置、钥匙串、进程协议和原子文件存储 |
| `engine` | 锁定依赖、翻译/排版/链接保护及单元测试 |
| `scripts` | 构建、裁剪、真实翻译和 PDF 检查 |
| `assets` | 生成图标原图、ICNS 和生成说明 |
| `demos` | 公开许可原始示例、来源及哈希 |
| `docs` | 用户、开发和验证文档 |

`.gitignore` 排除虚拟环境、构建产物、密钥文件、本地输出、原型和验证工作目录。用户私有 PDF、配置、Keychain 数据和旧译文不应加入 Git；项目内的 `demos` 是明确选定的公开示例。初始化只建立本地仓库，不配置远程或推送。
